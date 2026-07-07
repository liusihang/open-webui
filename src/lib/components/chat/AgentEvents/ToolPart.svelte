<script lang="ts">
	import type { AgentTranscriptToolPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptToolPart;

	const statusLabel = ($status: AgentTranscriptToolPart['status']): string => {
		if ($status === 'error') return 'failed';
		if ($status === 'done') return 'done';
		return 'running';
	};
</script>

<div
	class="agent-tool-part"
	class:running={part.status === 'running'}
	class:error={part.status === 'error'}
	class:done={part.status === 'done'}
	data-tool-call-id={part.toolCallId}
>
	<div class="agent-tool-row">
		{#if part.status === 'running'}
			<span class="agent-tool-spinner" aria-hidden="true"></span>
		{:else if part.status === 'error'}
			<span class="agent-tool-icon error" aria-hidden="true">!</span>
		{:else}
			<span class="agent-tool-icon done" aria-hidden="true">✓</span>
		{/if}
		<span class="agent-tool-name">{part.toolName ?? 'tool'}</span>
		<span class="agent-tool-status">{statusLabel(part.status)}</span>
	</div>
	<div class="agent-tool-summary">{part.summary}</div>
	<AgentDetailSection
		label="Debug details"
		payload={part.details}
		metadata={part.metadata}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-tool-part {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		padding: 0.3rem 0.4rem;
		border-radius: 0.3rem;
		margin: 0.15rem 0;
	}
	.agent-tool-part.error {
		background: var(--red-50, #fef2f2);
		border-left: 2px solid var(--red-400, #f87171);
	}
	.agent-tool-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-tool-name {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.agent-tool-status {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		text-transform: lowercase;
	}
	.agent-tool-summary {
		font-size: 0.72rem;
		color: var(--gray-600, #4b5563);
	}
	.agent-tool-spinner {
		width: 0.55rem;
		height: 0.55rem;
		border: 1.5px solid var(--gray-300, #d1d5db);
		border-top-color: var(--gray-600, #4b5563);
		border-radius: 9999px;
		animation: agent-tool-spin 0.8s linear infinite;
	}
	@keyframes agent-tool-spin {
		to {
			transform: rotate(360deg);
		}
	}
	.agent-tool-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 9999px;
		font-size: 0.6rem;
		font-weight: 700;
	}
	.agent-tool-icon.done {
		background: var(--green-100, #d1fae5);
		color: var(--green-700, #047857);
	}
	.agent-tool-icon.error {
		background: var(--red-100, #fee2e2);
		color: var(--red-700, #b91c1c);
	}
</style>
