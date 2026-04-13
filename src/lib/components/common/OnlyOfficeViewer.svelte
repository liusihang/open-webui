<script context="module" lang="ts">
	const DOCS_API_PATH = '/web-apps/apps/api/documents/api.js';
	const scriptLoaders = new Map<string, Promise<void>>();

	const normalizeDocumentServerUrl = (value: string) => value.trim().replace(/\/+$/, '');

	const loadDocsApiScript = async (documentServerUrl: string) => {
		const normalized = normalizeDocumentServerUrl(documentServerUrl);
		if (typeof window === 'undefined') return;
		if (window.DocsAPI?.DocEditor) return;

		if (scriptLoaders.has(normalized)) {
			try {
				await scriptLoaders.get(normalized);
			} catch (error) {
				scriptLoaders.delete(normalized);
				throw error;
			}
			return;
		}

		const loader = new Promise<void>((resolve, reject) => {
			const script = document.createElement('script');
			script.src = `${normalized}${DOCS_API_PATH}`;
			script.async = true;
			script.onload = () => resolve();
			script.onerror = () => {
				script.remove();
				reject(new Error('Failed to load OnlyOffice document API script.'));
			};
			document.head.appendChild(script);
		});

		scriptLoaders.set(normalized, loader);
		try {
			await loader;
		} catch (error) {
			scriptLoaders.delete(normalized);
			throw error;
		}
	};
</script>

<script lang="ts">
	import { browser } from '$app/environment';
	import { createEventDispatcher, getContext, onDestroy, onMount, tick } from 'svelte';

	import { createOnlyOfficeSession } from '$lib/apis/onlyoffice';
	import Spinner from './Spinner.svelte';

	declare global {
		interface Window {
			DocsAPI?: {
				DocEditor: new (
					placeholderId: string,
					config: Record<string, unknown>
				) => {
					destroyEditor?: () => void;
				};
			};
		}
	}

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let fileId = '';
	export let terminalServerId = '';
	export let terminalFilePath = '';
	export let readOnly = true;
	export let className = 'w-full h-[55dvh] md:h-[60vh]';

	let mounted = false;
	let currentSessionKey = '';
	let loading = false;
	let errorMessage = '';
	let placeholderId = '';
	let editorInstance: { destroyEditor?: () => void } | null = null;
	let initializeRequestId = 0;

	type OnlyOfficeEventHandler = (event: unknown) => void;
	type OnlyOfficeEvents = Record<string, unknown> & {
		onAppReady?: OnlyOfficeEventHandler;
		onError?: OnlyOfficeEventHandler;
	};

	const destroyEditor = () => {
		if (editorInstance?.destroyEditor) {
			editorInstance.destroyEditor();
		}
		editorInstance = null;
	};

	const buildSessionKey = () =>
		`${fileId || ''}:${terminalServerId || ''}:${terminalFilePath || ''}:${readOnly ? 'view' : 'edit'}`;

	const initializeViewer = async () => {
		if (!browser || !mounted) return;
		if (!fileId && !(terminalServerId && terminalFilePath)) return;

		const requestId = ++initializeRequestId;
		const isStaleRequest = () => !mounted || requestId !== initializeRequestId;

		loading = true;
		errorMessage = '';
		destroyEditor();

		try {
			const token = localStorage.token;
			if (!token) {
				throw new Error('Missing auth token.');
			}

			const session = await createOnlyOfficeSession(
				token,
				fileId
					? {
							source_type: 'file',
							file_id: fileId,
							mode: readOnly ? 'view' : 'edit'
						}
					: {
							source_type: 'terminal',
							terminal_server_id: terminalServerId,
							terminal_file_path: terminalFilePath,
							mode: readOnly ? 'view' : 'edit'
						}
			);
			if (isStaleRequest()) return;
			if (!session?.document_server_url || !session?.config) {
				throw new Error('OnlyOffice session response is invalid.');
			}

			await loadDocsApiScript(session.document_server_url);
			if (isStaleRequest()) return;
			if (!window.DocsAPI?.DocEditor) {
				throw new Error('OnlyOffice document API is unavailable.');
			}

			const placeholderSource = (fileId || `${terminalServerId}-${terminalFilePath}`).replace(
				/[^a-zA-Z0-9_-]/g,
				'_'
			);
			placeholderId = `onlyoffice-editor-${placeholderSource}-${Date.now()}`;
			await tick();
			if (isStaleRequest()) return;

			const sessionConfig = (session.config ?? {}) as Record<string, unknown>;
			const existingEvents = ((sessionConfig.events as OnlyOfficeEvents | undefined) ??
				{}) as OnlyOfficeEvents;

			editorInstance = new window.DocsAPI.DocEditor(placeholderId, {
				...sessionConfig,
				events: {
					...existingEvents,
					onAppReady: (event: unknown) => {
						if (isStaleRequest()) return;
						loading = false;
						dispatch('ready', { fileId, terminalServerId, terminalFilePath });
						existingEvents.onAppReady?.(event);
					},
					onError: (event: unknown) => {
						if (isStaleRequest()) return;
						const eventData =
							typeof event === 'object' && event !== null && 'data' in event
								? (event as { data?: { errorDescription?: string; message?: string } }).data
								: undefined;
						const detail =
							eventData?.errorDescription ||
							eventData?.message ||
							'OnlyOffice editor failed to initialize.';
						errorMessage = detail;
						loading = false;
						dispatch('error', { message: detail, event });
						existingEvents.onError?.(event);
					}
				}
			});

			// Some deployments do not reliably fire onAppReady; avoid a stuck spinner.
			window.setTimeout(() => {
				if (isStaleRequest()) return;
				if (loading && !errorMessage) {
					loading = false;
				}
			}, 2500);
		} catch (error) {
			if (isStaleRequest()) return;
			const message =
				error instanceof Error ? error.message : 'Failed to initialize OnlyOffice preview.';
			errorMessage = message;
			loading = false;
			dispatch('error', { message });
		}
	};

	onMount(() => {
		mounted = true;
		if (fileId) {
			currentSessionKey = buildSessionKey();
			void initializeViewer();
		}

		return () => {
			destroyEditor();
		};
	});

	onDestroy(() => {
		mounted = false;
		initializeRequestId += 1;
		destroyEditor();
	});

	$: if (!fileId) {
		if (!terminalServerId || !terminalFilePath) {
			initializeRequestId += 1;
			destroyEditor();
			placeholderId = '';
			loading = false;
			errorMessage = '';
		}
	}

	$: {
		const sessionKey = buildSessionKey();
		if (
			mounted &&
			(fileId || (terminalServerId && terminalFilePath)) &&
			sessionKey !== currentSessionKey
		) {
			currentSessionKey = sessionKey;
			void initializeViewer();
		}
	}
</script>

<div class="relative {className}">
	<div id={placeholderId} class="w-full h-full {errorMessage ? 'hidden' : ''}"></div>

	{#if loading}
		<div class="absolute inset-0 flex items-center justify-center bg-white/70 dark:bg-gray-900/70">
			<Spinner className="size-5" />
		</div>
	{/if}

	{#if errorMessage}
		<div
			class="absolute inset-0 flex items-center justify-center text-sm text-red-500 px-4 text-center"
			role="alert"
			aria-live="polite"
		>
			{errorMessage || $i18n.t('Failed to initialize OnlyOffice preview.')}
		</div>
	{/if}
</div>
