<script lang="ts">
	import type { AgentTranscriptApprovalPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptApprovalPart;

	const statusText = ($status: AgentTranscriptApprovalPart['status']): string => {
		if ($status === 'pending') return 'Awaiting approval';
		if ($status === 'rejected') return 'Rejected';
		return 'Approved';
	};

	const titleText = ($status: AgentTranscriptApprovalPart['status']): string => {
		if ($status === 'pending') return 'Confirmation required';
		if ($status === 'rejected') return 'Tool rejected';
		return 'Tool approved';
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
		<div class="agent-approval-title-block">
			<span class="agent-approval-icon" aria-hidden="true"></span>
			<span class="agent-approval-title">{titleText(part.status)}</span>
		</div>
		<span class="agent-approval-status">{statusText(part.status)}</span>
	</div>
	<div class="agent-approval-action">{part.action ?? part.description}</div>
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
		gap: 0.45rem;
		padding: 0.65rem 0.7rem;
		border-radius: 0.5rem;
		margin: 0;
		background: var(--amber-50, #fffbeb);
		border: 1px solid var(--amber-200, #fde68a);
	}
	.agent-approval-part.approved {
		background: var(--green-50, #f0fdf4);
		border-color: var(--green-200, #bbf7d0);
	}
	.agent-approval-part.rejected {
		background: var(--red-50, #fef2f2);
		border-color: var(--red-200, #fecaca);
	}
	.agent-approval-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		font-size: 0.78rem;
	}
	.agent-approval-title-block {
		min-width: 0;
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
	}
	.agent-approval-icon {
		flex: none;
		width: 0.72rem;
		height: 0.72rem;
		border-radius: 9999px;
		border: 2px solid var(--amber-500, #f59e0b);
	}
	.agent-approval-part.approved .agent-approval-icon {
		border-color: var(--green-600, #16a34a);
		background: var(--green-600, #16a34a);
	}
	.agent-approval-part.rejected .agent-approval-icon {
		border-color: var(--red-600, #dc2626);
		background: var(--red-600, #dc2626);
	}
	.agent-approval-title {
		color: var(--gray-950, #030712);
		font-weight: 650;
	}
	.agent-approval-action {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
		font-size: 0.76rem;
		line-height: 1.4;
	}
	.agent-approval-status {
		flex: none;
		border: 1px solid var(--amber-200, #fde68a);
		border-radius: 9999px;
		background: var(--white, #ffffff);
		color: var(--amber-700, #b45309);
		font-size: 0.66rem;
		font-weight: 600;
		line-height: 1;
		padding: 0.2rem 0.45rem;
	}
	.agent-approval-part.approved .agent-approval-status {
		border-color: var(--green-200, #bbf7d0);
		color: var(--green-700, #15803d);
	}
	.agent-approval-part.rejected .agent-approval-status {
		border-color: var(--red-200, #fecaca);
		color: var(--red-700, #b91c1c);
	}
	.agent-approval-description {
		font-size: 0.72rem;
		color: var(--gray-600, #4b5563);
	}
</style>
