<script lang="ts">
	import type { AgentTranscriptToolPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptToolPart;

	const statusLabel = ($status: AgentTranscriptToolPart['status']): string => {
		if ($status === 'error') return 'Error';
		if ($status === 'done') return 'Completed';
		return 'Running';
	};

	const toolTitle = ($part: AgentTranscriptToolPart): string => {
		return $part.toolName ?? 'Tool';
	};

	const readableSummary = ($part: AgentTranscriptToolPart): string => {
		if ($part.summary) return $part.summary;
		if ($part.status === 'running') return `${toolTitle($part)} is running.`;
		if ($part.status === 'error') return `${toolTitle($part)} failed.`;
		return `${toolTitle($part)} completed.`;
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
		<div class="agent-tool-heading">
			<span class="agent-tool-kind">Tool</span>
			<span class="agent-tool-name">{toolTitle(part)}</span>
		</div>
		<span class="agent-tool-status">{statusLabel(part.status)}</span>
	</div>
	<div class="agent-tool-user-summary">
		<span class="agent-tool-view-label">User summary</span>
		<span class="agent-tool-summary">{readableSummary(part)}</span>
	</div>
	<AgentDetailSection
		label="Dev details (raw JSON)"
		payload={part.details}
		metadata={part.metadata}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-tool-part {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0;
		border-radius: 0;
		margin: 0;
	}
	.agent-tool-part.error {
		color: var(--red-700, #b91c1c);
	}
	.agent-tool-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.agent-tool-heading {
		min-width: 0;
		display: flex;
		align-items: baseline;
		gap: 0.45rem;
	}
	.agent-tool-kind {
		color: var(--gray-500, #6b7280);
		font-size: 0.68rem;
	}
	.agent-tool-name {
		min-width: 0;
		color: var(--gray-800, #1f2937);
		font-size: 0.78rem;
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.agent-tool-status {
		flex: none;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 9999px;
		background: var(--gray-50, #f9fafb);
		color: var(--gray-600, #4b5563);
		font-size: 0.66rem;
		font-weight: 600;
		line-height: 1;
		padding: 0.2rem 0.45rem;
	}
	.agent-tool-part.running .agent-tool-status {
		border-color: var(--blue-200, #bfdbfe);
		background: var(--blue-50, #eff6ff);
		color: var(--blue-700, #1d4ed8);
	}
	.agent-tool-part.done .agent-tool-status {
		border-color: var(--green-200, #bbf7d0);
		background: var(--green-50, #f0fdf4);
		color: var(--green-700, #15803d);
	}
	.agent-tool-part.error .agent-tool-status {
		border-color: var(--red-200, #fecaca);
		background: var(--red-50, #fef2f2);
		color: var(--red-700, #b91c1c);
	}
	.agent-tool-user-summary {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: 0.45rem;
		align-items: start;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.4rem;
		background: var(--gray-50, #f9fafb);
		padding: 0.5rem 0.6rem;
	}
	.agent-tool-view-label {
		color: var(--gray-500, #6b7280);
		font-size: 0.66rem;
		font-weight: 600;
		line-height: 1.35;
		white-space: nowrap;
	}
	.agent-tool-summary {
		font-size: 0.74rem;
		color: var(--gray-600, #4b5563);
		line-height: 1.45;
		overflow-wrap: anywhere;
	}
</style>
