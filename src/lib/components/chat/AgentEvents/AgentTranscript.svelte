<script lang="ts">
	import type { AgentTranscriptModel } from './types';
	import TranscriptPart from './TranscriptPart.svelte';

	export let model: AgentTranscriptModel;
	export let agentRunId: string | null = null;
	export let expandAll = false;

	const headline = ($model: AgentTranscriptModel): string => {
		const status =
			$model.runStatus === 'completed'
				? 'Done'
				: $model.runStatus === 'failed'
					? 'Failed'
					: $model.runStatus === 'cancelled'
						? 'Cancelled'
						: $model.runStatus === 'budget_exceeded'
							? 'Budget exceeded'
							: $model.runStatus === 'waiting_approval'
								? 'Waiting on approval'
								: $model.runStatus === 'waiting_user_input'
									? 'Waiting for input'
								: $model.runStatus === 'finalizing'
									? 'Writing final answer'
									: 'Working';
		const segments: string[] = [status];
		if ($model.summary.toolCount > 0) {
			segments.push(`${$model.summary.toolCount} tool${$model.summary.toolCount === 1 ? '' : 's'}`);
		}
		if ($model.summary.artifactCount > 0) {
			segments.push(
				`${$model.summary.artifactCount} artifact${$model.summary.artifactCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.approvalCount > 0) {
			segments.push(
				`${$model.summary.approvalCount} approval${$model.summary.approvalCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.userInputCount > 0) {
			segments.push(
				`${$model.summary.userInputCount} input${$model.summary.userInputCount === 1 ? '' : 's'}`
			);
		}
		if ($model.summary.subagentCount > 0) {
			segments.push(
				`${$model.summary.subagentCount} subagent${$model.summary.subagentCount === 1 ? '' : 's'}`
			);
		}
		return segments.join(' · ');
	};
</script>

<section class="agent-transcript" data-run-status={model.runStatus}>
	<header class="agent-transcript-header">
		<span class="agent-transcript-headline">{headline(model)}</span>
		{#if model.summary.hasError}
			<span class="agent-transcript-flag error" aria-hidden="true">error</span>
		{:else if model.summary.hasPendingApproval || model.summary.hasPendingUserInput}
			<span class="agent-transcript-flag pending" aria-hidden="true">pending</span>
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
		<p class="agent-transcript-empty">Agent is starting…</p>
	{/if}

	{#if expandAll}
		<div class="agent-transcript-expand-all" aria-hidden="true" />
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
