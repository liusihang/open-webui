<script lang="ts">
	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	import type { AgentRunFinalPart } from './renderModel';

	export let agentRunId: string;
	export let finalAnswer: AgentRunFinalPart;
</script>

<div
	class="agent-run-final-answer border-t border-gray-100 bg-gray-50/70 px-3 py-3 dark:border-gray-800/80 dark:bg-gray-900/30"
>
	<div class="mb-2 flex items-center gap-2 text-xs font-medium text-gray-500 dark:text-gray-400">
		<Sparkles className="size-3.5" />
		<span>{finalAnswer.title}</span>
	</div>
	<ContentRenderer
		id={`agent-run-final-${agentRunId}`}
		content={finalAnswer.content}
		done={finalAnswer.status === 'done'}
		floatingButtons={false}
		save={false}
		preview={false}
	/>
</div>
