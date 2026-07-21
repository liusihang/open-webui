<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { submitAgentRunApproval, type AgentRunApprovalDecision } from '$lib/apis/agentRuns';
	import type { AgentTranscriptApprovalPart } from './types';

	export let part: AgentTranscriptApprovalPart;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let submitting: AgentRunApprovalDecision | null = null;
	let submittedDecision: AgentRunApprovalDecision | null = null;
	let submitError: string | null = null;
	let effectiveStatus: AgentTranscriptApprovalPart['status'];
	const idempotencyKeys: Partial<Record<AgentRunApprovalDecision, string>> = {};

	$: effectiveStatus =
		part.status !== 'pending'
			? part.status
			: submittedDecision === 'approved'
				? 'approved'
				: submittedDecision === 'rejected'
					? 'rejected'
					: 'pending';

	const statusText = ($status: AgentTranscriptApprovalPart['status']): string => {
		if ($status === 'pending') return $i18n.t('awaiting approval');
		if ($status === 'rejected') return $i18n.t('rejected');
		if ($status === 'stale') return $i18n.t('no longer available');
		return $i18n.t('approved');
	};

	const submit = async (decision: AgentRunApprovalDecision) => {
		if (!agentRunId || submitting || submittedDecision || part.status !== 'pending') {
			return;
		}
		submitError = null;
		submitting = decision;
		const idempotencyKey =
			idempotencyKeys[decision] ??
			`approval:${part.approvalId}:${decision}:${createSubmissionNonce()}`;
		idempotencyKeys[decision] = idempotencyKey;
		try {
			await submitAgentRunApproval(
				localStorage.getItem('token') ?? '',
				agentRunId,
				part.approvalId,
				{
					decision,
					idempotencyKey
				}
			);
			submittedDecision = decision;
		} catch (error) {
			submitError = errorMessage(error);
		} finally {
			submitting = null;
		}
	};

	const createSubmissionNonce = (): string =>
		typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
			? crypto.randomUUID()
			: `${Date.now()}-${Math.random().toString(36).slice(2)}`;

	const errorMessage = (error: unknown): string => {
		if (error instanceof Error) return error.message;
		if (typeof error === 'string') return error;
		if (typeof error === 'object' && error !== null && 'message' in error) {
			return String((error as { message: unknown }).message);
		}
		return $i18n.t('Unable to submit the approval decision.');
	};
</script>

<div
	class="agent-approval-part"
	class:pending={effectiveStatus === 'pending'}
	class:approved={effectiveStatus === 'approved'}
	class:rejected={effectiveStatus === 'rejected'}
	class:stale={effectiveStatus === 'stale'}
	data-approval-id={part.approvalId}
>
	<div class="agent-approval-row">
		<span class="agent-approval-icon" aria-hidden="true"
			>{effectiveStatus === 'pending' ? '◆' : '✓'}</span
		>
		<span class="agent-approval-action">{part.action ?? part.description}</span>
		<span class="agent-approval-status">{statusText(effectiveStatus)}</span>
	</div>
	{#if part.description && part.description !== part.action}
		<div class="agent-approval-description">{part.description}</div>
	{/if}
	{#if submitError}
		<p class="agent-approval-error" role="alert">{submitError}</p>
	{/if}
	{#if part.status === 'pending' && agentRunId}
		<div class="agent-approval-actions">
			{#if submittedDecision}
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
		padding: 0.65rem 0.7rem;
		border-radius: 0.75rem;
		margin: 0.15rem 0;
		background: var(--agent-transcript-attention-surface, #faf5ff);
		border: 1px solid var(--agent-transcript-attention-border, #ddd6fe);
	}
	.agent-approval-part.approved {
		background: transparent;
		border-color: transparent;
	}
	.agent-approval-part.stale {
		background: transparent;
		border-color: transparent;
	}
	.agent-approval-part.rejected {
		background: var(--agent-transcript-danger-surface, #fef2f2);
		border-color: var(--agent-transcript-danger-border, #fee2e2);
	}
	.agent-approval-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-approval-icon {
		color: var(--agent-transcript-warning-color, #d97706);
		font-size: 0.7rem;
	}
	.agent-approval-part.approved .agent-approval-icon {
		color: var(--agent-transcript-success-color, #047857);
	}
	.agent-approval-action {
		color: var(--agent-transcript-body-color, #1f2937);
		font-weight: 500;
	}
	.agent-approval-status {
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.65rem;
	}
	.agent-approval-description {
		font-size: 0.72rem;
		color: var(--agent-transcript-muted-color, #4b5563);
	}
	.agent-approval-error {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.4;
		color: var(--agent-transcript-danger-color, #b91c1c);
	}
	.agent-approval-actions {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}
	.agent-approval-actions button {
		border-radius: 0.5rem;
		border: 1px solid var(--agent-transcript-border-color, #e5e7eb);
		background: var(--agent-transcript-surface-color, #f9f9f9);
		color: var(--agent-transcript-body-color, #374151);
		font-size: 0.72rem;
		font-weight: 500;
		padding: 0.34rem 0.58rem;
		transition:
			background-color 160ms ease-out,
			border-color 160ms ease-out;
	}
	.agent-approval-actions button.approve {
		background: var(--agent-transcript-accent-color, #7c3aed);
		border-color: var(--agent-transcript-accent-color, #7c3aed);
		color: #fafafa;
	}
	.agent-approval-actions button:hover:not(:disabled) {
		background: var(--agent-transcript-raised-surface, #f5f5f4);
	}
	.agent-approval-actions button.approve:hover:not(:disabled) {
		filter: brightness(0.94);
	}
	.agent-approval-actions button:focus-visible {
		outline: 2px solid var(--agent-transcript-focus-color, #8b5cf6);
		outline-offset: 2px;
	}
	.agent-approval-actions button:disabled {
		opacity: 0.55;
	}
	.agent-approval-submitted {
		font-size: 0.68rem;
		color: var(--agent-transcript-muted-color, #6b7280);
	}
</style>
