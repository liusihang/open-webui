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
			<div class="agent-approval-title-copy">
				<span class="agent-approval-title">{titleText(part.status)}</span>
				<span class="agent-approval-subtitle">Review before continuing</span>
			</div>
		</div>
		<span class="agent-approval-status">{statusText(part.status)}</span>
	</div>
	<div class="agent-approval-request-shell">
		<div class="agent-approval-request-row">
			<span class="agent-approval-request-label">Request</span>
			<span class="agent-approval-action">{part.action ?? part.description}</span>
		</div>
		{#if part.description && part.description !== part.action}
			<div class="agent-approval-request-row">
				<span class="agent-approval-request-label">Risk</span>
				<span class="agent-approval-description">{part.description}</span>
			</div>
		{/if}
	</div>
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
		align-items: flex-start;
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
	.agent-approval-title-copy {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.08rem;
	}
	.agent-approval-title {
		color: var(--gray-950, #030712);
		font-weight: 650;
	}
	.agent-approval-subtitle {
		color: var(--gray-600, #4b5563);
		font-size: 0.68rem;
		font-weight: 500;
		line-height: 1.25;
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
	.agent-approval-request-shell {
		display: flex;
		flex-direction: column;
		gap: 0;
		overflow: hidden;
		border: 1px solid rgba(245, 158, 11, 0.28);
		border-radius: 0.45rem;
		background: rgba(255, 255, 255, 0.78);
	}
	.agent-approval-part.approved .agent-approval-request-shell {
		border-color: rgba(34, 197, 94, 0.24);
	}
	.agent-approval-part.rejected .agent-approval-request-shell {
		border-color: rgba(239, 68, 68, 0.24);
	}
	.agent-approval-request-row {
		display: grid;
		grid-template-columns: 4.6rem minmax(0, 1fr);
		gap: 0.55rem;
		align-items: start;
		padding: 0.48rem 0.55rem;
		font-size: 0.74rem;
		line-height: 1.38;
	}
	.agent-approval-request-row + .agent-approval-request-row {
		border-top: 1px solid rgba(245, 158, 11, 0.16);
	}
	.agent-approval-part.approved .agent-approval-request-row + .agent-approval-request-row {
		border-top-color: rgba(34, 197, 94, 0.14);
	}
	.agent-approval-part.rejected .agent-approval-request-row + .agent-approval-request-row {
		border-top-color: rgba(239, 68, 68, 0.14);
	}
	.agent-approval-request-label {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		font-weight: 700;
		line-height: 1.5;
		text-transform: uppercase;
	}
	.agent-approval-action {
		color: var(--gray-900, #111827);
		font-weight: 600;
		overflow-wrap: anywhere;
	}
	.agent-approval-description {
		color: var(--gray-600, #4b5563);
		overflow-wrap: anywhere;
	}
	@media (max-width: 520px) {
		.agent-approval-row {
			align-items: flex-start;
			flex-direction: column;
		}
		.agent-approval-request-row {
			grid-template-columns: 1fr;
			gap: 0.18rem;
		}
	}
</style>
