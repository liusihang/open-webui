<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-note-part" class:running={part.status === 'running'} data-block-id={part.blockId}>
	{#if part.status === 'running'}
		<span class="agent-note-pulse" aria-hidden="true"></span>
	{/if}
	<div class="agent-note-text">{part.text}</div>
</div>

<style>
	.agent-note-part {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: center;
		column-gap: 0.45rem;
		margin: 0.1rem 0;
	}
	.agent-note-pulse {
		width: 0.42rem;
		height: 0.42rem;
		border-radius: 9999px;
		background: var(--agent-transcript-muted-color, var(--color-gray-500, #6b7280));
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
		grid-column: 1 / -1;
		font-size: 0.82rem;
		color: var(--agent-transcript-body-color, var(--tw-prose-body, var(--color-gray-800, #1f2937)));
		line-height: 1.5;
		white-space: pre-wrap;
	}
	.agent-note-part.running .agent-note-text {
		grid-column: 2;
	}
</style>
