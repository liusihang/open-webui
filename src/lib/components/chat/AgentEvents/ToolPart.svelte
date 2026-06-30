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
		<div class="agent-tool-header-actions">
			<div class="agent-tool-view-switch" aria-label="Tool view">
				<span class="agent-tool-view-option dev">Dev</span>
				<span class="agent-tool-view-option active">User view</span>
			</div>
			<span class="agent-tool-status">{statusLabel(part.status)}</span>
		</div>
	</div>
	<div class="agent-tool-user-pane">
		<span class="agent-tool-summary-icon" aria-hidden="true"></span>
		<div class="agent-tool-summary-copy">
			<span class="agent-tool-summary-label">User-readable result</span>
			<span class="agent-tool-summary">{readableSummary(part)}</span>
		</div>
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
	.agent-tool-header-actions {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		flex-shrink: 0;
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
	.agent-tool-view-switch {
		display: inline-flex;
		align-items: center;
		overflow: hidden;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.45rem;
		background: var(--white, #ffffff);
	}
	.agent-tool-view-option {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 1.4rem;
		padding: 0 0.45rem;
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		font-weight: 650;
		line-height: 1;
	}
	.agent-tool-view-option + .agent-tool-view-option {
		border-left: 1px solid var(--gray-200, #e5e7eb);
	}
	.agent-tool-view-option.active {
		background: var(--blue-50, #eff6ff);
		color: var(--blue-700, #1d4ed8);
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
	.agent-tool-user-pane {
		display: flex;
		gap: 0.55rem;
		align-items: flex-start;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.5rem;
		background: linear-gradient(180deg, rgba(249, 250, 251, 0.98), rgba(255, 255, 255, 0.98));
		padding: 0.58rem 0.65rem;
	}
	.agent-tool-summary-icon {
		width: 0.5rem;
		height: 0.5rem;
		margin-top: 0.32rem;
		border-radius: 9999px;
		background: var(--blue-500, #3b82f6);
		box-shadow: 0 0 0 3px var(--blue-50, #eff6ff);
		flex-shrink: 0;
	}
	.agent-tool-part.done .agent-tool-summary-icon {
		background: var(--green-500, #22c55e);
		box-shadow: 0 0 0 3px var(--green-50, #f0fdf4);
	}
	.agent-tool-part.error .agent-tool-summary-icon {
		background: var(--red-500, #ef4444);
		box-shadow: 0 0 0 3px var(--red-50, #fef2f2);
	}
	.agent-tool-summary-copy {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
	}
	.agent-tool-summary-label {
		color: var(--gray-500, #6b7280);
		font-size: 0.64rem;
		font-weight: 600;
		line-height: 1.2;
	}
	.agent-tool-summary {
		font-size: 0.76rem;
		color: var(--gray-800, #1f2937);
		line-height: 1.42;
		overflow-wrap: anywhere;
	}
	@media (max-width: 520px) {
		.agent-tool-row {
			align-items: flex-start;
			flex-direction: column;
		}
		.agent-tool-header-actions {
			width: 100%;
			justify-content: space-between;
		}
	}
</style>
