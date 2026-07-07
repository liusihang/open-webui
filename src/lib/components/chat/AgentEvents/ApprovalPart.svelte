<script lang="ts">
	import type { AgentTranscriptApprovalPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptApprovalPart;

	const statusText = ($status: AgentTranscriptApprovalPart['status']): string => {
		if ($status === 'pending') return 'awaiting approval';
		if ($status === 'rejected') return 'rejected';
		return 'approved';
	};
</script>

<div
	class="agent-approval-part"
	class:pending={part.status === 'pending'}
	class:approved={part.status === 'approved'}
	class:rejected={part.status === 'rejected'}
	data-approval-id={part.approvalId}
>
	<div class="agent-approval-row">
		<span class="agent-approval-icon" aria-hidden="true">{part.status === 'pending' ? '◆' : '✓'}</span>
		<span class="agent-approval-action">{part.action ?? part.description}</span>
		<span class="agent-approval-status">{statusText(part.status)}</span>
	</div>
	{#if part.description && part.description !== part.action}
		<div class="agent-approval-description">{part.description}</div>
	{/if}
	<AgentDetailSection
		label="Approval details"
		payload={part.details}
		metadata={part.metadata}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-approval-part {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.35rem 0.5rem;
		border-radius: 0.3rem;
		margin: 0.15rem 0;
		background: var(--amber-50, #fffbeb);
		border-left: 2px solid var(--amber-300, #fcd34d);
	}
	.agent-approval-part.approved {
		background: var(--gray-50, #f9fafb);
		border-left-color: var(--gray-200, #e5e7eb);
	}
	.agent-approval-part.rejected {
		background: var(--red-50, #fef2f2);
		border-left-color: var(--red-300, #fca5a5);
	}
	.agent-approval-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-approval-icon {
		color: var(--amber-500, #f59e0b);
		font-size: 0.7rem;
	}
	.agent-approval-part.approved .agent-approval-icon {
		color: var(--gray-400, #9ca3af);
	}
	.agent-approval-action {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.agent-approval-status {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
	}
	.agent-approval-description {
		font-size: 0.72rem;
		color: var(--gray-600, #4b5563);
	}
</style>
