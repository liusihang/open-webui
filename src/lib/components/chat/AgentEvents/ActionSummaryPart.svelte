<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-action-part" data-block-id={part.blockId}>
	<div class="agent-action-header">
		<span class="agent-action-kind" aria-hidden="true">Action</span>
	</div>
	<div class="agent-action-text">{part.text}</div>
	{#if part.participantId || part.phase}
		<AgentDetailSection
			label="Context"
			payload={{ participant: part.participantId, phase: part.phase }}
			dense={true}
		/>
	{/if}
</div>

<style>
	.agent-action-part {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.3rem 0.5rem;
		margin: 0.2rem 0;
	}
	.agent-action-header {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.agent-action-text {
		font-size: 0.8rem;
		color: var(--gray-700, #374151);
		line-height: 1.4;
		white-space: pre-wrap;
	}
</style>
