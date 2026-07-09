<script lang="ts">
	import ContentRenderer from '$lib/components/chat/Messages/ContentRenderer.svelte';

	export let agentRunId: string;
	export let content = '';
	export let done = false;
	export let history: any;
	export let messageId: string;
</script>

<ContentRenderer
	id={`agent-run-final-${agentRunId}`}
	{content}
	{done}
	{history}
	{messageId}
	floatingButtons={false}
	save={false}
	preview={false}
/>
