<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { submitAgentRunApproval, type AgentRunApprovalDecision } from '$lib/apis/agentRuns';
	import type { AgentTranscriptApprovalPart } from './types';

	export let part: AgentTranscriptApprovalPart;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let submitting: AgentRunApprovalDecision | null = null;
	let submitted = false;

	const statusText = ($status: AgentTranscriptApprovalPart['status']): string => {
		if ($status === 'pending') return $i18n.t('awaiting approval');
		if ($status === 'rejected') return $i18n.t('rejected');
		return $i18n.t('approved');
	};

	const submit = async (decision: AgentRunApprovalDecision) => {
		if (!agentRunId || submitting || submitted || part.status !== 'pending') {
			return;
		}
		submitting = decision;
		try {
			await submitAgentRunApproval(localStorage.getItem('token') ?? '', agentRunId, part.approvalId, {
				decision
			});
			submitted = true;
		} catch (error) {
			toast.error(`${error}`);
			submitting = null;
			return;
		}
		submitting = null;
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
	{#if part.status === 'pending' && agentRunId}
		<div class="agent-approval-actions">
			{#if submitted}
				<span class="agent-approval-submitted" role="status">
					{$i18n.t('Submitted')}. {$i18n.t('Waiting for agent\u2026')}
				</span>
			{:else}
				<button
					type="button"
					class="approve"
					disabled={submitting !== null}
					aria-label={$i18n.t('Approve tool action')}
					on:click={() => void submit('approved')}
				>
					{$i18n.t('Approve')}
				</button>
				<button
					type="button"
					disabled={submitting !== null}
					aria-label={$i18n.t('Reject tool action')}
					on:click={() => void submit('rejected')}
				>
					{$i18n.t('Reject')}
				</button>
			{/if}
		</div>
	{/if}
</div>

<style>
	.agent-approval-part {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.45rem 0.55rem;
		border-radius: 0.4rem;
		margin: 0.15rem 0;
		background: var(--amber-50, #fffbeb);
		border: 1px solid var(--amber-100, #fef3c7);
	}
	.agent-approval-part.approved {
		background: transparent;
		border-color: transparent;
	}
	.agent-approval-part.rejected {
		background: var(--red-50, #fef2f2);
		border-color: var(--red-100, #fee2e2);
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
	.agent-approval-actions {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}
	.agent-approval-actions button {
		border-radius: 0.25rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--white, #ffffff);
		color: var(--gray-700, #374151);
		font-size: 0.68rem;
		font-weight: 500;
		padding: 0.25rem 0.45rem;
	}
	.agent-approval-actions button.approve {
		background: var(--gray-900, #111827);
		border-color: var(--gray-900, #111827);
		color: var(--white, #ffffff);
	}
	.agent-approval-actions button:disabled {
		opacity: 0.55;
	}
	.agent-approval-submitted {
		font-size: 0.68rem;
		color: var(--gray-500, #6b7280);
	}
</style>
