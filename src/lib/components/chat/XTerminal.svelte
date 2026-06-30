<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import { Terminal } from '@xterm/xterm';
	import { FitAddon } from '@xterm/addon-fit';
	import { WebLinksAddon } from '@xterm/addon-web-links';
	import '@xterm/xterm/css/xterm.css';

	import { terminalServers, settings, selectedTerminalId } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import TerminalIcon from '$lib/components/icons/Terminal.svelte';
	import { copyToClipboard } from '$lib/utils';

	type I18nValue = {
		t: (key: string, options?: Record<string, unknown>) => string;
	};
	type TerminalServerEntry = {
		id?: string;
		url?: string;
		key?: string;
	};
	type SettingsWithTerminals = {
		terminalServers?: TerminalServerEntry[];
	};

	const i18n = getContext<Readable<I18nValue>>('i18n');

	export let overlay = false;
	export let chatId: string | null = null;

	let terminalEl: HTMLDivElement;
	let term: Terminal | null = null;
	let fitAddon: FitAddon | null = null;
	let ws: WebSocket | null = null;
	export let connected = false;
	export let connecting = false;
	let resizeObserver: ResizeObserver | null = null;
	let pingInterval: ReturnType<typeof setInterval> | null = null;
	let copiedSelection = false;

	// Resolve the active terminal server's info for the WebSocket URL
	const getTerminalInfo = (): { serverId: string; baseUrl: string } | null => {
		// System terminal (admin-configured, has an `id`)
		const systemTerminals = (($terminalServers ?? []) as TerminalServerEntry[]).filter((t) => t.id);
		const systemMatch = systemTerminals.find((t) => t.id === $selectedTerminalId);
		if (systemMatch) {
			// For system terminals, WS goes through the Open WebUI backend proxy
			return { serverId: systemMatch.id as string, baseUrl: WEBUI_API_BASE_URL };
		}

		// Direct terminal (user-configured, matched by URL)
		const directTerminals = (
			(($settings as SettingsWithTerminals | undefined)?.terminalServers ??
				[]) as TerminalServerEntry[]
		).filter((s) => s.url);
		const directMatch = directTerminals.find((s) => s.url === $selectedTerminalId);
		if (directMatch) {
			// For direct terminals, construct WS URL from the server URL directly
			return { serverId: '__direct__', baseUrl: directMatch.url as string };
		}

		return null;
	};

	const connect = async () => {
		if (ws) disconnect();

		const info = getTerminalInfo();
		if (!info) return;

		connecting = true;

		const token = localStorage.getItem('token') ?? '';

		try {
			let sessionId: string;
			let wsUrl: string;
			let authToken: string;

			if (info.serverId === '__direct__') {
				// Direct connection to open-terminal
				const base = info.baseUrl.replace(/\/$/, '');
				const directTerminals = (
					(($settings as SettingsWithTerminals | undefined)?.terminalServers ??
						[]) as TerminalServerEntry[]
				).filter((s) => s.url);
				const directMatch = directTerminals.find((s) => s.url === $selectedTerminalId);
				const apiKey = directMatch?.key ?? '';
				authToken = apiKey;

				// Create session
				const createHeaders: Record<string, string> = { Authorization: `Bearer ${apiKey}` };
				if (chatId) createHeaders['X-Session-Id'] = chatId;
				const res = await fetch(`${base}/api/terminals`, {
					method: 'POST',
					headers: createHeaders
				});
				if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
				const session = await res.json();
				sessionId = session.id;

				const wsBase = base.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
				wsUrl = `${wsBase}/api/terminals/${sessionId}`;
			} else {
				// System terminal — proxy through Open WebUI backend
				const base = info.baseUrl.replace(/\/$/, '');
				authToken = token;

				// Create session via proxy
				const proxyHeaders: Record<string, string> = { Authorization: `Bearer ${token}` };
				if (chatId) proxyHeaders['X-Session-Id'] = chatId;
				const res = await fetch(`${base}/terminals/${info.serverId}/api/terminals`, {
					method: 'POST',
					headers: proxyHeaders
				});
				if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
				const session = await res.json();
				sessionId = session.id;

				const wsBase = base.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
				wsUrl = `${wsBase}/terminals/${info.serverId}/api/terminals/${sessionId}`;
			}

			ws = new WebSocket(wsUrl);
			ws.binaryType = 'arraybuffer';

			ws.onopen = () => {
				// First-message auth (no token in URL)
				if (ws) {
					ws.send(JSON.stringify({ type: 'auth', token: authToken }));
				}
				connected = true;
				connecting = false;
				// Focus the terminal so it receives keyboard input immediately
				term?.focus();
				// Send initial resize
				if (term && ws) {
					ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
				}
				// Keepalive ping to prevent idle timeout from proxies/LBs
				if (pingInterval) clearInterval(pingInterval);
				pingInterval = setInterval(() => {
					if (ws && ws.readyState === WebSocket.OPEN) {
						ws.send(JSON.stringify({ type: 'ping' }));
					}
				}, 25000);
			};

			ws.onmessage = (event) => {
				if (term) {
					if (event.data instanceof ArrayBuffer) {
						term.write(new Uint8Array(event.data));
					} else {
						term.write(event.data);
					}
				}
			};

			ws.onclose = () => {
				connected = false;
				connecting = false;
				if (term) {
					term.write('\r\n\x1b[90m[Connection closed]\x1b[0m\r\n');
				}
			};

			ws.onerror = () => {
				connected = false;
				connecting = false;
			};
		} catch (err) {
			connecting = false;
			if (term) {
				term.write(`\r\n\x1b[31m[Error: ${err}]\x1b[0m\r\n`);
			}
		}
	};

	const disconnect = () => {
		if (pingInterval) {
			clearInterval(pingInterval);
			pingInterval = null;
		}
		if (ws) {
			ws.close();
			ws = null;
		}
		connected = false;
		connecting = false;
	};

	const initTerminal = () => {
		if (!terminalEl || term) return;

		term = new Terminal({
			cursorBlink: true,
			fontSize: 13,
			fontFamily:
				"'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
			theme: {
				background: '#0b1020',
				foreground: '#d7deea',
				cursor: '#f8fafc',
				cursorAccent: '#0b1020',
				selectionBackground: '#334155',
				selectionForeground: '#ffffff',
				black: '#0f172a',
				red: '#f87171',
				green: '#34d399',
				yellow: '#facc15',
				blue: '#60a5fa',
				magenta: '#c084fc',
				cyan: '#22d3ee',
				white: '#e2e8f0',
				brightBlack: '#64748b',
				brightRed: '#fca5a5',
				brightGreen: '#86efac',
				brightYellow: '#fde68a',
				brightBlue: '#93c5fd',
				brightMagenta: '#d8b4fe',
				brightCyan: '#67e8f9',
				brightWhite: '#ffffff'
			},
			allowProposedApi: true,
			scrollback: 5000
		});

		fitAddon = new FitAddon();
		term.loadAddon(fitAddon);
		term.loadAddon(new WebLinksAddon());

		term.open(terminalEl);

		// Fit after a frame so the container has dimensions
		requestAnimationFrame(() => {
			fitAddon?.fit();
		});

		// Forward keystrokes to WebSocket
		term.onData((data) => {
			if (ws && ws.readyState === WebSocket.OPEN) {
				ws.send(new TextEncoder().encode(data));
			}
		});

		// Forward binary data (e.g. paste with special chars)
		term.onBinary((data) => {
			if (ws && ws.readyState === WebSocket.OPEN) {
				const buffer = new Uint8Array(data.length);
				for (let i = 0; i < data.length; i++) {
					buffer[i] = data.charCodeAt(i) & 0xff;
				}
				ws.send(buffer);
			}
		});

		// Ensure all key events are processed by xterm.js and not intercepted
		// by the browser or surrounding UI (fixes vi/vim keystroke handling).
		term.attachCustomKeyEventHandler(() => true);

		// Handle resize
		term.onResize(({ cols, rows }) => {
			if (ws && ws.readyState === WebSocket.OPEN) {
				ws.send(JSON.stringify({ type: 'resize', cols, rows }));
			}
		});

		// Watch container size changes
		resizeObserver = new ResizeObserver(() => {
			requestAnimationFrame(() => {
				fitAddon?.fit();
			});
		});
		resizeObserver.observe(terminalEl);

		// Connection is handled by the reactive block below (which fires
		// when `term` is set here), so we intentionally do NOT call
		// connect() to avoid creating a duplicate WebSocket whose onclose
		// handler would write a spurious "[Connection closed]" message.
	};

	const copySelection = async () => {
		const selection = term?.getSelection() ?? '';
		if (!selection) return;
		copiedSelection = true;
		await copyToClipboard(selection);
		setTimeout(() => {
			copiedSelection = false;
		}, 1500);
	};

	const clearTerminal = () => {
		term?.clear();
	};

	// Reconnect when the selected terminal changes
	$: if ($selectedTerminalId !== undefined && term) {
		// Clear the terminal screen and reconnect to the new server
		disconnect();
		term.clear();
		if ($selectedTerminalId) {
			connect();
		}
	}

	onMount(() => {
		initTerminal();
	});

	onDestroy(() => {
		disconnect();
		resizeObserver?.disconnect();
		term?.dispose();
		term = null;
		fitAddon = null;
	});
</script>

<div class="terminal-shell h-full min-h-0 relative">
	<div class="terminal-live-header">
		<div class="terminal-live-title">
			<TerminalIcon className="size-3.5" strokeWidth="1.75" />
			<span>{$i18n.t('Terminal')}</span>
			<span class="terminal-live-status-dot" class:connected class:connecting aria-hidden="true"
			></span>
			<span class="terminal-live-status-text">
				{connecting ? $i18n.t('Connecting') : connected ? $i18n.t('Connected') : $i18n.t('Idle')}
			</span>
		</div>
		<div class="terminal-live-actions">
			<button type="button" class="terminal-live-action" on:click={copySelection}>
				<Clipboard className="size-3.5" strokeWidth="1.75" />
				<span>{copiedSelection ? $i18n.t('Copied') : $i18n.t('Copy')}</span>
			</button>
			<button type="button" class="terminal-live-action" on:click={clearTerminal}>
				<GarbageBin className="size-3.5" strokeWidth="1.75" />
				<span>{$i18n.t('Clear')}</span>
			</button>
		</div>
	</div>
	<div class="terminal-surface">
		<div bind:this={terminalEl} class="absolute inset-0 px-1" class:pointer-events-none={overlay} />
	</div>
</div>

<style>
	.terminal-shell {
		display: flex;
		flex-direction: column;
		background: #0b1020;
		border: 1px solid rgba(148, 163, 184, 0.18);
		border-radius: 0.65rem;
		overflow: hidden;
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.04),
			0 1px 2px rgba(15, 23, 42, 0.08);
	}
	.terminal-live-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		min-height: 2.1rem;
		padding: 0.35rem 0.55rem;
		background: rgba(15, 23, 42, 0.96);
		border-bottom: 1px solid rgba(148, 163, 184, 0.16);
		color: #cbd5e1;
	}
	.terminal-live-title,
	.terminal-live-actions,
	.terminal-live-action {
		display: inline-flex;
		align-items: center;
	}
	.terminal-live-title {
		gap: 0.42rem;
		min-width: 0;
		font-size: 0.72rem;
		font-weight: 700;
	}
	.terminal-live-status-dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 9999px;
		background: #64748b;
	}
	.terminal-live-status-dot.connected {
		background: #22c55e;
	}
	.terminal-live-status-dot.connecting {
		background: #f59e0b;
		animation: terminal-pulse 1.2s ease-in-out infinite;
	}
	.terminal-live-status-text {
		color: #94a3b8;
		font-size: 0.66rem;
		font-weight: 500;
		white-space: nowrap;
	}
	.terminal-live-actions {
		gap: 0.35rem;
		flex-shrink: 0;
	}
	.terminal-live-action {
		gap: 0.28rem;
		border-radius: 0.42rem;
		border: 1px solid rgba(148, 163, 184, 0.18);
		background: rgba(30, 41, 59, 0.58);
		color: #cbd5e1;
		font-size: 0.68rem;
		font-weight: 600;
		line-height: 1;
		padding: 0.32rem 0.42rem;
		transition:
			background 120ms ease,
			border-color 120ms ease,
			color 120ms ease;
	}
	.terminal-live-action:hover {
		background: rgba(51, 65, 85, 0.72);
		border-color: rgba(148, 163, 184, 0.3);
		color: #f8fafc;
	}
	.terminal-surface {
		position: relative;
		flex: 1;
		min-height: 0;
		background: #0b1020;
	}
	.terminal-surface :global(.xterm) {
		padding: 0.45rem 0.2rem 0.25rem;
	}
	.terminal-surface :global(.xterm-viewport),
	.terminal-surface :global(.xterm-screen) {
		background: #0b1020 !important;
	}
	@keyframes terminal-pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.45;
		}
	}
</style>
