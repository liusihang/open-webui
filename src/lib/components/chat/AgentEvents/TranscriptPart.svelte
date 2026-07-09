<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

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

	const i18n = getContext<Writable<i18nType>>('i18n');

	const subagentStatusText = (status: 'running' | 'done' | 'error'): string => {
		if (status === 'error') return $i18n.t('Failed');
		if (status === 'done') return $i18n.t('Done');
		return $i18n.t('Running');
	};
</script>

<div class="transcript-part" data-kind={part.kind}>
	{#if part.kind === 'assistant_note'}
		<AssistantNotePart {part} />
	{:else if part.kind === 'action_summary' || part.kind === 'legacy_note'}
		<ActionSummaryPart {part} />
	{:else if part.kind === 'tool'}
		<ToolPart {part} />
	{:else if part.kind === 'approval'}
		<ApprovalPart {part} {agentRunId} />
	{:else if part.kind === 'user_input'}
		<UserInputPart {part} {agentRunId} />
	{:else if part.kind === 'artifact'}
		<ArtifactPart {part} />
	{:else if part.kind === 'error'}
		<ErrorPart {part} />
	{:else if part.kind === 'subagent'}
		<div class="transcript-subagent">
			<div class="transcript-subagent-row">
				{#if part.status === 'running'}
					<span class="transcript-subagent-spinner" aria-hidden="true"></span>
				{:else if part.status === 'error'}
					<span class="transcript-subagent-icon error" aria-hidden="true">!</span>
				{:else}
					<span class="transcript-subagent-icon done" aria-hidden="true">✓</span>
				{/if}
				<span class="transcript-subagent-name">{part.participantName ?? part.label}</span>
				<span class="transcript-subagent-status">{subagentStatusText(part.status)}</span>
			</div>
			{#if part.summary}
				<div class="transcript-subagent-summary">{part.summary}</div>
			{/if}
			{#if part.resultSummary}
				<div class="transcript-subagent-result">{part.resultSummary}</div>
			{/if}
			{#if part.status === 'error'}
				<AgentDetailSection
					label={$i18n.t('Details')}
					payload={part.details}
					open={part.defaultExpanded}
				/>
			{/if}
		</div>
	{:else if part.kind === 'run'}
		<div class="transcript-run-row">
			<span class="transcript-run-label">{part.label}</span>
			<span class="transcript-run-summary">{part.summary}</span>
		</div>
	{/if}
</div>

<style>
	.transcript-part {
		position: relative;
		padding-left: 0;
	}
	.transcript-subagent {
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
		margin: 0.05rem 0;
	}
	.transcript-subagent.error {
		color: var(--red-700, #b91c1c);
	}
	.transcript-subagent-row {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.75rem;
	}
	.transcript-subagent-name {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.transcript-subagent-status {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
	}
	.transcript-subagent-spinner {
		width: 0.5rem;
		height: 0.5rem;
		border: 1.5px solid var(--gray-300, #d1d5db);
		border-top-color: var(--gray-600, #4b5563);
		border-radius: 9999px;
		animation: transcript-subagent-spin 0.8s linear infinite;
	}
	@keyframes transcript-subagent-spin {
		to {
			transform: rotate(360deg);
		}
	}
	.transcript-subagent-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 9999px;
		font-size: 0.6rem;
		font-weight: 700;
	}
	.transcript-subagent-icon.done {
		background: transparent;
		color: var(--gray-400, #9ca3af);
	}
	.transcript-subagent-icon.error {
		color: var(--red-700, #b91c1c);
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
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.7rem;
		color: var(--gray-500, #6b7280);
	}
	.transcript-run-label {
		font-weight: 500;
	}
</style>
