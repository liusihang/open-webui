<script lang="ts">
	import type { AgentTranscriptModel } from './types';
	import TranscriptPart from './TranscriptPart.svelte';

	export let model: AgentTranscriptModel;
	export let agentRunId: string | null = null;
	export let expandAll = false;

	const runBadge = ($model: AgentTranscriptModel): string => {
		if ($model.summary.hasError || $model.runStatus === 'failed') return 'Error';
		if ($model.runStatus === 'cancelled') return 'Cancelled';
		if ($model.runStatus === 'budget_exceeded') return 'Budget exceeded';
		if ($model.isRunning) return 'Live';
		return 'Completed';
	};

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

<section
	class="agent-transcript agent-activity-timeline"
	data-run-status={model.runStatus}
	data-agent-run-id={agentRunId}
>
	<header class="agent-transcript-header">
		<div class="agent-transcript-title-row">
			<span class="agent-transcript-title">Agent Activity Timeline</span>
			<span
				class="agent-transcript-flag"
				class:live={model.isRunning && !model.summary.hasError}
				class:error={model.summary.hasError || model.runStatus === 'failed'}
				class:pending={model.summary.hasPendingApproval || model.summary.hasPendingUserInput}
			>
				{runBadge(model)}
			</span>
		</div>
		<span class="agent-transcript-headline">{headline(model)}</span>
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
		<div class="agent-transcript-expand-all" aria-hidden="true"></div>
	{/if}
</section>

<style>
	.agent-transcript {
		display: flex;
		flex-direction: column;
		gap: 0.65rem;
		padding: 0.85rem 0.9rem;
		color: var(--gray-700, #374151);
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.5rem;
		background: var(--white, #ffffff);
		box-shadow: 0 1px 2px rgb(15 23 42 / 0.04);
	}
	.agent-transcript-header {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.72rem;
		color: var(--gray-500, #6b7280);
	}
	.agent-transcript-title-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.agent-transcript-title {
		color: var(--gray-950, #030712);
		font-size: 0.95rem;
		font-weight: 650;
		line-height: 1.2;
	}
	.agent-transcript-headline {
		font-weight: 500;
		letter-spacing: 0;
	}
	.agent-transcript-flag {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		font-size: 0.68rem;
		font-weight: 600;
		letter-spacing: 0;
		padding: 0.15rem 0.45rem;
		border-radius: 9999px;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--gray-50, #f9fafb);
		color: var(--gray-600, #4b5563);
		white-space: nowrap;
	}
	.agent-transcript-flag::before {
		content: '';
		width: 0.38rem;
		height: 0.38rem;
		border-radius: 9999px;
		background: currentColor;
	}
	.agent-transcript-flag.live {
		border-color: var(--blue-200, #bfdbfe);
		background: var(--blue-50, #eff6ff);
		color: var(--blue-700, #1d4ed8);
	}
	.agent-transcript-flag.error {
		border-color: var(--red-200, #fecaca);
		background: var(--red-100, #fee2e2);
		color: var(--red-700, #b91c1c);
	}
	.agent-transcript-flag.pending {
		border-color: var(--amber-200, #fde68a);
		background: var(--amber-100, #fef3c7);
		color: var(--amber-700, #b45309);
	}
	.agent-transcript-timeline {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.5rem;
		overflow: hidden;
		background: var(--white, #ffffff);
	}
	.agent-transcript-timeline-row {
		position: relative;
	}
	.agent-transcript-timeline-row + .agent-transcript-timeline-row {
		border-top: 1px solid var(--gray-200, #e5e7eb);
	}
	.agent-transcript-empty {
		margin: 0;
		font-size: 0.72rem;
		color: var(--gray-400, #9ca3af);
	}
</style>
