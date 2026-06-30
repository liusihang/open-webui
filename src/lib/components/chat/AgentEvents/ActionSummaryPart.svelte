<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-action-part" data-block-id={part.blockId}>
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
		padding: 0.2rem 0;
		margin: 0;
	}
	.agent-action-text {
		font-size: 0.8rem;
		color: var(--gray-700, #374151);
		line-height: 1.4;
		white-space: pre-wrap;
	}
</style>
