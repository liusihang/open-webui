<script>
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';
	import StatusHistory from '$lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte';

	const reasoningRunning = `<details type="reasoning" done="false">
<summary>Thinking...</summary>
1. Compare schema paths and existing state handling.
2. Design a unified status timeline layout.
3. Validate edge cases for hidden and in-progress steps.
</details>`;

	const reasoningDone = `<details type="reasoning" done="true" duration="18">
<summary>Thought for 18 seconds</summary>
- Converted status events into a normalized visual timeline.
- Applied compact step cards with clear running/completed states.
- Kept backward compatibility with existing message payloads.
</details>`;

	const previewStatusHistory = [
		{
			action: 'web_search_queries_generated',
			description: 'Searching',
			queries: ['openwebui status history ui', 'deerflow chain of thought'],
			done: true
		},
		{
			action: 'web_search',
			description: 'Searched {{count}} sites',
			urls: [
				{ title: 'Open WebUI docs', url: 'https://docs.openwebui.com' },
				{ title: 'DeerFlow repo', url: 'https://github.com/bytedance/deer-flow' }
			],
			done: true
		},
		{
			action: 'knowledge_search',
			description: 'Searching Knowledge for "{{searchQuery}}"',
			query: 'status timeline visual design',
			done: false
		}
	];

	const toolCallRunning = {
		type: 'tool_calls',
		id: 'tool-call-running',
		name: 'web_search',
		arguments: JSON.stringify(
			JSON.stringify({
				query: 'deerflow reasoning ui',
				limit: 3
			})
		),
		done: 'false'
	};

	const toolCallDone = {
		type: 'tool_calls',
		id: 'tool-call-done',
		name: 'web_search',
		arguments: JSON.stringify(
			JSON.stringify({
				query: 'openwebui tool call display',
				limit: 3
			})
		),
		result: JSON.stringify(
			JSON.stringify({
				hits: 3,
				top: ['Open WebUI docs', 'Tooling guide', 'Repo examples']
			})
		),
		done: 'true'
	};
</script>

<svelte:head>
	<title>Style Preview</title>
</svelte:head>

<div class="mx-auto max-w-3xl px-4 py-10 space-y-8">
	<div>
		<h1 class="text-2xl font-semibold mb-1">DeerFlow Style Preview</h1>
		<p class="text-sm text-gray-500 dark:text-gray-400">
			This page previews the modified reasoning and status timeline UI.
		</p>
	</div>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Reasoning (Running)
		</h2>
		<Markdown id="preview-reasoning-running" content={reasoningRunning} done={false} />
	</section>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Reasoning (Completed)
		</h2>
		<Markdown id="preview-reasoning-completed" content={reasoningDone} done={true} />
	</section>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Status Timeline
		</h2>
		<StatusHistory statusHistory={previewStatusHistory} expand={true} />
	</section>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Tool Call (Running)
		</h2>
		<ToolCallDisplay id="preview-tool-running" attributes={toolCallRunning} open={true} className="w-full" />
	</section>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Tool Call (Done)
		</h2>
		<ToolCallDisplay id="preview-tool-done" attributes={toolCallDone} open={false} className="w-full" />
	</section>
</div>
