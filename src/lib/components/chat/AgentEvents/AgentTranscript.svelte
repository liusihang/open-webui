<script lang="ts">
	import { getContext } from 'svelte';
	import { onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import type { AgentTranscriptModel } from './types';
	import TranscriptPart from './TranscriptPart.svelte';

	export let model: AgentTranscriptModel;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let open = false;
	let previousAutoOpenKey: string | null = null;
	let now = Date.now();
	let tickTimer: ReturnType<typeof setInterval> | null = null;

	const transcriptAttentionKey = ($model: AgentTranscriptModel): string | null => {
		if ($model.connectionState !== 'connected') return `connection:${$model.connectionState}`;
		if ($model.summary.hasError) return `error:${$model.parts.length}`;
		const pendingApproval = $model.parts.find(
			(part) => part.kind === 'approval' && part.status === 'pending'
		);
		if (pendingApproval) return `approval:${pendingApproval.seq}`;
		const pendingInput = $model.parts.find(
			(part) => part.kind === 'user_input' && part.status === 'pending'
		);
		if (pendingInput) return `input:${pendingInput.seq}`;
		if ($model.isRunning) return 'active';
		return null;
	};

	$: nextAutoOpenKey = transcriptAttentionKey(model);
	$: if (nextAutoOpenKey !== previousAutoOpenKey) {
		if (nextAutoOpenKey) {
			open = true;
		}
		previousAutoOpenKey = nextAutoOpenKey;
	}

	const headline = ($model: AgentTranscriptModel): string => {
		if ($model.runStatus === 'completed') return $i18n.t('Processed');
		if ($model.runStatus === 'failed') return $i18n.t('Failed');
		if ($model.runStatus === 'cancelled') return $i18n.t('Cancelled');
		if ($model.runStatus === 'budget_exceeded') return $i18n.t('Stopped');
		if ($model.runStatus === 'waiting_approval') return $i18n.t('Waiting for approval');
		if ($model.runStatus === 'waiting_user_input') return $i18n.t('Waiting for input');
		if ($model.runStatus === 'finalizing') return $i18n.t('Writing final answer');
		return $i18n.t('Processing');
	};

	onMount(() => {
		tickTimer = setInterval(() => {
			if (model && !model.isTerminal && model.startedAt !== null) {
				now = Date.now();
			}
		}, 1000);
	});

	onDestroy(() => {
		if (tickTimer !== null) {
			clearInterval(tickTimer);
			tickTimer = null;
		}
	});

	const displayElapsedMs = ($model: AgentTranscriptModel, currentNow: number): number | null => {
		if ($model.startedAt === null) {
			return $model.elapsedMs;
		}
		if ($model.isTerminal) {
			return $model.elapsedMs;
		}
		return Math.max(0, currentNow - $model.startedAt);
	};

	const elapsedText = (elapsedMs: number | null): string | null => {
		if (elapsedMs === null) {
			return null;
		}
		const totalSeconds = Math.max(0, Math.round(elapsedMs / 1000));
		const hours = Math.floor(totalSeconds / 3600);
		const minutes = Math.floor((totalSeconds % 3600) / 60);
		const seconds = totalSeconds % 60;
		if (hours > 0) {
			return `${hours}h ${minutes}m`;
		}
		if (minutes > 0) {
			return `${minutes}m ${seconds}s`;
		}
		return `${seconds}s`;
	};

	$: displayedElapsedMs = displayElapsedMs(model, now);

	const connectionText = ($model: AgentTranscriptModel): string | null => {
		if ($model.isTerminal) {
			return null;
		}
		if ($model.connectionState === 'disconnected') {
			return $i18n.t('Connection lost. Reconnecting\u2026');
		}
		if ($model.connectionState === 'reconnecting') {
			return $i18n.t('Reconnecting\u2026');
		}
		return null;
	};
</script>

<details class="agent-transcript" data-run-status={model.runStatus} bind:open>
	<summary class="agent-transcript-summary">
		<span
			class="agent-transcript-state"
			class:active={model.isRunning}
			class:attention={model.summary.hasPendingApproval || model.summary.hasPendingUserInput}
			class:error={model.summary.hasError}
			aria-hidden="true"
		></span>
		<span class="agent-transcript-headline">{headline(model)}</span>
		{#if elapsedText(displayedElapsedMs)}
			<span class="agent-transcript-time">{elapsedText(displayedElapsedMs)}</span>
		{/if}
		{#if connectionText(model)}
			<span class="agent-transcript-connection" role="status">{connectionText(model)}</span>
		{/if}
		<span class="agent-transcript-chevron" class:open aria-hidden="true">
			<ChevronDown className="size-3.5" strokeWidth="2" />
		</span>
	</summary>

	{#if model.parts.length > 0}
		<ol class="agent-transcript-timeline">
			{#each model.parts as part (part.seq + ':' + part.kind)}
				<li class="agent-transcript-timeline-row">
					<TranscriptPart {part} {agentRunId} />
				</li>
			{/each}
		</ol>
	{:else}
		<p class="agent-transcript-empty">{$i18n.t('Agent is starting\u2026')}</p>
	{/if}
</details>

<style>
	.agent-transcript {
		display: block;
		margin: 0.2rem 0 0.55rem;
		--agent-transcript-body-color: var(--tw-prose-body, var(--color-gray-800, #1f2937));
		--agent-transcript-muted-color: var(--tw-prose-captions, var(--color-gray-500, #6b7280));
		--agent-transcript-tool-color: var(--tw-prose-captions, var(--color-gray-400, #9ca3af));
		--agent-transcript-surface-color: var(--color-gray-50, #f9f9f9);
		--agent-transcript-raised-surface: #f5f5f4;
		--agent-transcript-border-color: var(--color-gray-200, #e5e7eb);
		--agent-transcript-accent-color: #7c3aed;
		--agent-transcript-focus-color: color-mix(in srgb, #7c3aed 58%, transparent);
		--agent-transcript-attention-surface: color-mix(in srgb, #7c3aed 7%, transparent);
		--agent-transcript-attention-border: color-mix(in srgb, #7c3aed 20%, transparent);
		--agent-transcript-warning-color: #b45309;
		--agent-transcript-danger-color: #b91c1c;
		--agent-transcript-danger-surface: color-mix(in srgb, #dc2626 7%, transparent);
		--agent-transcript-danger-border: color-mix(in srgb, #dc2626 18%, transparent);
		--agent-transcript-success-color: #047857;
		--gray-50: var(--agent-transcript-surface-color);
		--gray-100: var(--agent-transcript-raised-surface);
		--gray-200: var(--agent-transcript-border-color);
		--gray-400: var(--agent-transcript-tool-color);
		--gray-500: var(--agent-transcript-muted-color);
		--gray-600: var(--agent-transcript-muted-color);
		--gray-700: var(--agent-transcript-body-color);
		--gray-800: var(--agent-transcript-body-color);
		--gray-900: var(--agent-transcript-body-color);
		--gray-950: var(--agent-transcript-body-color);
		--white: var(--agent-transcript-surface-color);
		color: var(--agent-transcript-body-color);
	}
	:global(.dark) .agent-transcript {
		--agent-transcript-surface-color: var(--color-gray-850, #262626);
		--agent-transcript-raised-surface: var(--color-gray-800, #333333);
		--agent-transcript-border-color: var(--color-gray-700, #4a4a4a);
		--agent-transcript-accent-color: #a78bfa;
		--agent-transcript-focus-color: color-mix(in srgb, #a78bfa 68%, transparent);
		--agent-transcript-attention-surface: color-mix(in srgb, #a78bfa 12%, transparent);
		--agent-transcript-attention-border: color-mix(in srgb, #a78bfa 26%, transparent);
		--agent-transcript-warning-color: #fbbf24;
		--agent-transcript-danger-color: #fca5a5;
		--agent-transcript-danger-surface: color-mix(in srgb, #ef4444 12%, transparent);
		--agent-transcript-danger-border: color-mix(in srgb, #ef4444 28%, transparent);
		--agent-transcript-success-color: #6ee7b7;
	}
	.agent-transcript-summary {
		display: inline-flex;
		align-items: center;
		gap: 0.38rem;
		width: fit-content;
		max-width: 100%;
		min-height: 1.65rem;
		margin-left: -0.3rem;
		padding: 0.16rem 0.3rem;
		border-radius: 0.45rem;
		cursor: pointer;
		list-style: none;
		font-size: 0.78rem;
		color: var(--agent-transcript-muted-color);
		user-select: none;
		transition: background-color 180ms cubic-bezier(0.25, 1, 0.5, 1);
	}
	.agent-transcript-summary:hover {
		background: color-mix(in srgb, var(--agent-transcript-muted-color) 8%, transparent);
	}
	.agent-transcript-summary:focus-visible {
		outline: 2px solid var(--agent-transcript-focus-color);
		outline-offset: 1px;
	}
	.agent-transcript-summary::-webkit-details-marker {
		display: none;
	}
	.agent-transcript-state {
		width: 0.42rem;
		height: 0.42rem;
		border-radius: 9999px;
		background: var(--agent-transcript-tool-color);
		flex: 0 0 auto;
	}
	.agent-transcript-state.active {
		background: var(--agent-transcript-accent-color);
		animation: agent-transcript-pulse 1.4s ease-in-out infinite;
	}
	.agent-transcript-state.attention {
		background: var(--agent-transcript-warning-color);
	}
	.agent-transcript-state.error {
		background: var(--agent-transcript-danger-color);
	}
	.agent-transcript-headline {
		font-weight: 550;
		color: var(--agent-transcript-muted-color);
	}
	.agent-transcript-time {
		color: var(--agent-transcript-tool-color);
	}
	.agent-transcript-connection {
		color: var(--agent-transcript-warning-color);
		font-size: 0.72rem;
	}
	.agent-transcript-chevron {
		display: inline-flex;
		color: var(--agent-transcript-tool-color);
		transform: rotate(-90deg);
		transition: transform 180ms cubic-bezier(0.25, 1, 0.5, 1);
	}
	.agent-transcript-chevron.open {
		transform: rotate(0deg);
	}
	.agent-transcript-timeline {
		list-style: none;
		margin: 0.5rem 0 0;
		padding: 0 0 0 0.15rem;
		display: flex;
		flex-direction: column;
		gap: 0.32rem;
	}
	.agent-transcript-timeline-row {
		position: relative;
	}
	.agent-transcript-empty {
		margin: 0;
		font-size: 0.72rem;
		color: var(--agent-transcript-tool-color);
	}
	@keyframes agent-transcript-pulse {
		0%,
		100% {
			opacity: 0.45;
		}
		50% {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.agent-transcript-summary,
		.agent-transcript-chevron {
			transition: none;
		}
		.agent-transcript-state.active {
			animation: none;
		}
	}
</style>
