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
		color: var(--gray-600, #4b5563);
		line-height: 1.5;
		white-space: pre-wrap;
	}
</style>
