<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-note-part" data-block-id={part.blockId}>
	<div class="agent-note-text">{part.text}</div>
	{#if part.participantId || part.phase}
		<AgentDetailSection
			label="Context"
			payload={{ participant: part.participantId, phase: part.phase }}
			dense={true}
		/>
	{/if}
</div>

<style>
	.agent-note-part {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.2rem 0;
		margin: 0;
		background: transparent;
	}
	.agent-note-text {
		font-size: 0.78rem;
		color: var(--gray-700, #374151);
		line-height: 1.4;
		white-space: pre-wrap;
	}
</style>
