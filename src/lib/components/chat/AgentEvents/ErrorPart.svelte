<script lang="ts">
	import type { AgentTranscriptErrorPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptErrorPart;
</script>

<div class="agent-error-part" data-default-expanded={part.defaultExpanded}>
	<div class="agent-error-row">
		<span class="agent-error-icon" aria-hidden="true">!</span>
		<span class="agent-error-label">{part.label}</span>
	</div>
	<div class="agent-error-summary">{part.summary}</div>
	<AgentDetailSection
		label="Error details"
		payload={part.details}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-error-part {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.45rem 0.55rem;
		border-radius: 0.4rem;
		margin: 0.15rem 0;
		background: var(--red-50, #fef2f2);
		border: 1px solid var(--red-100, #fee2e2);
	}
	.agent-error-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.78rem;
		color: var(--red-700, #b91c1c);
		font-weight: 500;
	}
	.agent-error-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 0.9rem;
		height: 0.9rem;
		border-radius: 9999px;
		background: var(--red-100, #fee2e2);
		color: var(--red-700, #b91c1c);
		font-size: 0.65rem;
		font-weight: 700;
	}
	.agent-error-label {
		letter-spacing: 0.02em;
	}
	.agent-error-summary {
		font-size: 0.75rem;
		color: var(--gray-700, #374151);
		line-height: 1.4;
	}
</style>
