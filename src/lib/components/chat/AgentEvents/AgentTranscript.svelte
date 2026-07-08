<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { AgentTranscriptModel } from './types';
	import TranscriptPart from './TranscriptPart.svelte';

	export let model: AgentTranscriptModel;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	const headline = ($model: AgentTranscriptModel): string => {
		const status =
			$model.runStatus === 'completed'
				? $i18n.t('Done')
				: $model.runStatus === 'failed'
					? $i18n.t('Failed')
					: $model.runStatus === 'cancelled'
						? $i18n.t('Cancelled')
						: $model.runStatus === 'budget_exceeded'
							? $i18n.t('Budget exceeded')
							: $model.runStatus === 'waiting_approval'
								? $i18n.t('Waiting on approval')
								: $model.runStatus === 'waiting_user_input'
									? $i18n.t('Waiting for input')
								: $model.runStatus === 'finalizing'
									? $i18n.t('Writing final answer')
									: $i18n.t('Working');
		const segments: string[] = [status];
		if ($model.summary.toolCount > 0) {
			segments.push(`${$model.summary.toolCount} ${$i18n.t('tool')}${$model.summary.toolCount === 1 ? '' : 's'}`);
		}
		if ($model.summary.artifactCount > 0) {
			segments.push(
				`${$model.summary.artifactCount} ${$i18n.t('artifact')}${$model.summary.artifactCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.approvalCount > 0) {
			segments.push(
				`${$model.summary.approvalCount} ${$i18n.t('approval')}${$model.summary.approvalCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.userInputCount > 0) {
			segments.push(
				`${$model.summary.userInputCount} ${$i18n.t('input')}${$model.summary.userInputCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.subagentCount > 0) {
			segments.push(
				`${$model.summary.subagentCount} ${$i18n.t('subagent')}${$model.summary.subagentCount === 1 ? '' : 's'}`
			);
		}
		return segments.join(' \u00b7 ');
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

<section class="agent-transcript" data-run-status={model.runStatus}>
	<header class="agent-transcript-header">
		<span class="agent-transcript-headline">{headline(model)}</span>
		{#if connectionText(model)}
			<span class="agent-transcript-connection" role="status">{connectionText(model)}</span>
		{/if}
		{#if model.summary.hasError}
			<span class="agent-transcript-flag error" aria-hidden="true">{$i18n.t('error')}</span>
		{:else if model.summary.hasPendingApproval || model.summary.hasPendingUserInput}
			<span class="agent-transcript-flag pending" aria-hidden="true">{$i18n.t('pending')}</span>
		{/if}
	</header>

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
</section>

<style>
	.agent-transcript {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		padding: 0.4rem 0;
		color: var(--gray-700, #374151);
	}
	.agent-transcript-header {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.72rem;
		color: var(--gray-500, #6b7280);
	}
	.agent-transcript-headline {
		font-weight: 500;
		letter-spacing: 0.01em;
	}
	.agent-transcript-connection {
		color: var(--amber-600, #d97706);
		font-size: 0.68rem;
	}
	.agent-transcript-flag {
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		padding: 0.05rem 0.35rem;
		border-radius: 0.2rem;
	}
	.agent-transcript-flag.error {
		background: var(--red-100, #fee2e2);
		color: var(--red-700, #b91c1c);
	}
	.agent-transcript-flag.pending {
		background: var(--amber-100, #fef3c7);
		color: var(--amber-700, #b45309);
	}
	.agent-transcript-timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
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
