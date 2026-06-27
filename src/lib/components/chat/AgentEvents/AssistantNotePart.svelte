<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-note-part" data-block-id={part.blockId}>
	<div class="agent-note-header">
		<span class="agent-note-kind" aria-hidden="true">Note</span>
		{#if part.status === 'running'}
			<span class="agent-note-pulse" aria-hidden="true"></span>
		{/if}
	</div>
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
		padding: 0.35rem 0.5rem;
		border-left: 2px solid var(--gray-200, #e5e7eb);
		margin: 0.2rem 0;
		background: transparent;
	}
	.agent-note-header {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.agent-note-pulse {
		width: 0.4rem;
		height: 0.4rem;
		border-radius: 9999px;
		background: var(--gray-400, #9ca3af);
		animation: agent-note-blink 1.2s ease-in-out infinite;
	}
	@keyframes agent-note-blink {
		0%,
		100% {
			opacity: 0.4;
		}
		50% {
			opacity: 1;
		}
	}
	.agent-note-text {
		font-size: 0.78rem;
		color: var(--gray-700, #374151);
		line-height: 1.4;
		white-space: pre-wrap;
	}
</style>
