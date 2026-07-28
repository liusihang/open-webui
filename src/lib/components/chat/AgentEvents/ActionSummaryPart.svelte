<script lang="ts">
	import type { AgentTranscriptTextPart } from './types';

	export let part: AgentTranscriptTextPart;
</script>

<div class="agent-action-part" data-block-id={part.blockId}>
	<div class="agent-action-text">{part.text}</div>
</div>

<style>
	.agent-action-part {
		margin: 0.1rem 0;
	}
	.agent-action-text {
		font-size: 0.82rem;
		color: var(--agent-transcript-body-color, var(--tw-prose-body, var(--color-gray-800, #1f2937)));
		line-height: 1.5;
		white-space: pre-wrap;
	}
</style>
