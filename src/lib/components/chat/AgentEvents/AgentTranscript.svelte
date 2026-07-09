<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { AgentTranscriptModel } from './types';
	import TranscriptPart from './TranscriptPart.svelte';

	export let model: AgentTranscriptModel;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let open = false;
	let initialized = false;

	$: if (!initialized && model) {
		open =
			model.isRunning ||
			model.summary.hasError ||
			model.summary.hasPendingApproval ||
			model.summary.hasPendingUserInput;
		initialized = true;
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

	const elapsedText = ($model: AgentTranscriptModel): string | null => {
		if ($model.elapsedMs === null) {
			return null;
		}
		const totalSeconds = Math.max(0, Math.round($model.elapsedMs / 1000));
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
		<span class="agent-transcript-headline">{headline(model)}</span>
		{#if elapsedText(model)}
			<span class="agent-transcript-time">{elapsedText(model)}</span>
		{/if}
		{#if connectionText(model)}
			<span class="agent-transcript-connection" role="status">{connectionText(model)}</span>
		{/if}
		<span class="agent-transcript-chevron" aria-hidden="true">{open ? 'v' : '>'}</span>
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
		margin: 0.15rem 0 0.45rem;
		color: var(--gray-600, #4b5563);
	}
	.agent-transcript-summary {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		width: fit-content;
		max-width: 100%;
		cursor: pointer;
		list-style: none;
		font-size: 0.76rem;
		color: var(--gray-500, #6b7280);
		user-select: none;
	}
	.agent-transcript-summary::-webkit-details-marker {
		display: none;
	}
	.agent-transcript-headline {
		font-weight: 500;
		color: var(--gray-500, #6b7280);
	}
	.agent-transcript-time {
		color: var(--gray-400, #9ca3af);
	}
	.agent-transcript-connection {
		color: var(--amber-600, #d97706);
		font-size: 0.72rem;
	}
	.agent-transcript-chevron {
		color: var(--gray-400, #9ca3af);
		font-size: 0.9rem;
		line-height: 1;
		transform: translateY(-0.02rem);
	}
	.agent-transcript-timeline {
		list-style: none;
		margin: 0.45rem 0 0;
		padding: 0 0 0 0.1rem;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.agent-transcript-timeline-row {
		position: relative;
	}
	.agent-transcript-empty {
		margin: 0;
		font-size: 0.72rem;
		color: var(--gray-400, #9ca3af);
	}
</style>
