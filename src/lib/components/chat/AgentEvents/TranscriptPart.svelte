<script lang="ts">
	import type { AgentTranscriptModelPart } from './types';
	import AssistantNotePart from './AssistantNotePart.svelte';
	import ActionSummaryPart from './ActionSummaryPart.svelte';
	import ToolPart from './ToolPart.svelte';
	import ApprovalPart from './ApprovalPart.svelte';
	import UserInputPart from './UserInputPart.svelte';
	import ArtifactPart from './ArtifactPart.svelte';
	import ErrorPart from './ErrorPart.svelte';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptModelPart;
	export let agentRunId: string | null = null;

	const reactStage = ($part: AgentTranscriptModelPart): string => {
		if ($part.kind === 'assistant_note') return 'Reasoning';
		if ($part.kind === 'action_summary' || $part.kind === 'legacy_note') return 'Action';
		if ($part.kind === 'tool') {
			if ($part.status === 'running') return 'Action';
			return 'Observation';
		}
		if ($part.kind === 'approval') return 'Approval';
		if ($part.kind === 'user_input') return 'Input';
		if ($part.kind === 'artifact') return 'Artifact';
		if ($part.kind === 'subagent') {
			if ($part.status === 'running') return 'Action';
			return 'Observation';
		}
		if ($part.kind === 'run') return 'Final answer';
		return 'Error';
	};

	const partStatus = ($part: AgentTranscriptModelPart): string => {
		if ($part.kind === 'tool' || $part.kind === 'subagent') {
			if ($part.status === 'running') return 'Running';
			if ($part.status === 'error') return 'Error';
			return 'Completed';
		}
		if ($part.kind === 'approval') {
			if ($part.status === 'pending') return 'Awaiting approval';
			if ($part.status === 'rejected') return 'Rejected';
			return 'Approved';
		}
		if ($part.kind === 'user_input') {
			if ($part.status === 'pending') return 'Waiting';
			if ($part.status === 'accepted') return 'Submitted';
			if ($part.status === 'timeout') return 'Timed out';
			return $part.status;
		}
		if (
			$part.kind === 'assistant_note' ||
			$part.kind === 'action_summary' ||
			$part.kind === 'legacy_note'
		) {
			return $part.status === 'running' ? 'Streaming' : 'Completed';
		}
		if ($part.kind === 'run') {
			return $part.runStatus === 'completed' ? 'Completed' : 'Streaming';
		}
		if ($part.kind === 'error') return 'Error';
		return 'Completed';
	};

	const tone = ($part: AgentTranscriptModelPart): string => {
		if ($part.kind === 'error') return 'error';
		if (($part.kind === 'tool' || $part.kind === 'subagent') && $part.status === 'error')
			return 'error';
		if ($part.kind === 'approval' && $part.status === 'pending') return 'approval';
		if ($part.kind === 'approval' && $part.status === 'rejected') return 'error';
		if ($part.kind === 'artifact') return 'artifact';
		if ($part.kind === 'assistant_note') return 'reasoning';
		if ($part.kind === 'run') return 'final';
		return 'neutral';
	};
</script>

<div
	class="transcript-part"
	data-kind={part.kind}
	data-react-stage={reactStage(part)}
	data-status={partStatus(part)}
	data-tone={tone(part)}
>
	<div class="transcript-part-seq" aria-label={`Event ${part.seq}`}>#{part.seq}</div>
	<div class="transcript-part-rail" aria-hidden="true">
		<span class="transcript-part-dot"></span>
	</div>
	<div class="transcript-part-content">
		<div class="transcript-part-stage-row">
			<span class="transcript-part-stage">{reactStage(part)}</span>
			<span class="transcript-part-state">{partStatus(part)}</span>
		</div>

		{#if part.kind === 'assistant_note'}
			<AssistantNotePart {part} />
		{:else if part.kind === 'action_summary' || part.kind === 'legacy_note'}
			<ActionSummaryPart {part} />
		{:else if part.kind === 'tool'}
			<ToolPart {part} />
		{:else if part.kind === 'approval'}
			<ApprovalPart {part} />
		{:else if part.kind === 'user_input'}
			<UserInputPart {part} {agentRunId} />
		{:else if part.kind === 'artifact'}
			<ArtifactPart {part} />
		{:else if part.kind === 'error'}
			<ErrorPart {part} />
		{:else if part.kind === 'subagent'}
			<div class="transcript-subagent">
				<div class="transcript-subagent-row">
					<span class="transcript-subagent-name">{part.participantName ?? part.label}</span>
				</div>
				{#if part.summary}
					<div class="transcript-subagent-summary">{part.summary}</div>
				{/if}
				{#if part.resultSummary}
					<div class="transcript-subagent-result">{part.resultSummary}</div>
				{/if}
				<AgentDetailSection
					label="Dev details"
					payload={part.details}
					open={part.defaultExpanded}
				/>
			</div>
		{:else if part.kind === 'run'}
			<div class="transcript-run-row">
				<span class="transcript-run-label">{part.label}</span>
				<span class="transcript-run-summary">{part.summary}</span>
			</div>
		{/if}
	</div>
</div>

<style>
	.transcript-part {
		position: relative;
		display: grid;
		grid-template-columns: 3.25rem 1rem minmax(0, 1fr);
		gap: 0.55rem;
		padding: 0.75rem 0.9rem;
		background: var(--white, #ffffff);
	}
	.transcript-part-seq {
		padding-top: 0.12rem;
		color: var(--gray-500, #6b7280);
		font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
		font-size: 0.7rem;
		white-space: nowrap;
	}
	.transcript-part-rail {
		position: relative;
		display: flex;
		justify-content: center;
	}
	.transcript-part-rail::before {
		content: '';
		position: absolute;
		top: -0.75rem;
		bottom: -0.75rem;
		width: 1px;
		background: var(--gray-200, #e5e7eb);
	}
	.transcript-part-dot {
		position: relative;
		z-index: 1;
		width: 0.58rem;
		height: 0.58rem;
		margin-top: 0.22rem;
		border: 2px solid var(--white, #ffffff);
		border-radius: 9999px;
		background: var(--gray-400, #9ca3af);
		box-shadow: 0 0 0 1px var(--gray-300, #d1d5db);
	}
	.transcript-part[data-tone='reasoning'] .transcript-part-dot {
		background: var(--blue-500, #3b82f6);
		box-shadow: 0 0 0 1px var(--blue-200, #bfdbfe);
	}
	.transcript-part[data-tone='approval'] .transcript-part-dot {
		background: var(--amber-500, #f59e0b);
		box-shadow: 0 0 0 1px var(--amber-200, #fde68a);
	}
	.transcript-part[data-tone='artifact'] .transcript-part-dot,
	.transcript-part[data-tone='final'] .transcript-part-dot {
		background: var(--green-500, #22c55e);
		box-shadow: 0 0 0 1px var(--green-200, #bbf7d0);
	}
	.transcript-part[data-tone='error'] .transcript-part-dot {
		background: var(--red-500, #ef4444);
		box-shadow: 0 0 0 1px var(--red-200, #fecaca);
	}
	.transcript-part-content {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}
	.transcript-part-stage-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.transcript-part-stage {
		color: var(--gray-950, #030712);
		font-size: 0.78rem;
		font-weight: 650;
		line-height: 1.25;
	}
	.transcript-part-state {
		color: var(--gray-500, #6b7280);
		font-size: 0.68rem;
		line-height: 1.25;
		text-align: right;
	}
	.transcript-subagent {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		padding: 0.25rem 0;
	}
	.transcript-subagent-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.transcript-subagent-name {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.transcript-subagent-summary {
		font-size: 0.72rem;
		color: var(--gray-600, #4b5563);
	}
	.transcript-subagent-result {
		font-size: 0.7rem;
		color: var(--gray-500, #6b7280);
	}
	.transcript-run-row {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.35rem 0.5rem;
		padding: 0.2rem 0;
		font-size: 0.78rem;
	}
	.transcript-run-label {
		color: var(--gray-900, #111827);
		font-weight: 500;
	}
	.transcript-run-summary {
		color: var(--gray-600, #4b5563);
	}

	@media (max-width: 520px) {
		.transcript-part {
			grid-template-columns: 2.6rem 0.8rem minmax(0, 1fr);
			gap: 0.4rem;
			padding: 0.7rem;
		}
		.transcript-part-seq {
			font-size: 0.65rem;
		}
	}
</style>
