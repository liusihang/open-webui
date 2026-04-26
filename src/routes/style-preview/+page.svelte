<script lang="ts">
	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';

	const sampleCode = `async function replaySurface(commit) {
  const palette = ['code-block-container', 'tool-call-container'];
  return { commit, palette, scope: 'surface-only' };
}`;
	const previewToken = {
		text: sampleCode,
		raw: `\`\`\`typescript\n${sampleCode}\n\`\`\``
	};

	const toolCallRunning = {
		type: 'tool_calls',
		id: 'tool-call-running',
		name: 'web_search',
		arguments: JSON.stringify(
			JSON.stringify({
				query: 'replay code block surface package',
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
				query: 'tool call surface verification',
				limit: 3
			})
		),
		result: JSON.stringify(
			JSON.stringify({
				hits: 3,
				top: ['CodeBlock palette', 'Tool-call card', 'Style preview route']
			})
		),
		done: 'true'
	};
</script>

<svelte:head>
	<title>Style Preview</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-10 space-y-8">
	<div>
		<h1 class="text-2xl font-semibold mb-1">Code Surface Preview</h1>
		<p class="text-sm text-gray-500 dark:text-gray-400">
			This route previews the replayed code-block and tool-call surfaces only.
		</p>
	</div>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Code Block
		</h2>
		<CodeBlock
			id="preview-code-block"
			token={previewToken}
			code={sampleCode}
			lang="typescript"
			edit={false}
			run={false}
			save={false}
		/>
		<CodeBlock
			id="preview-code-block-collapsed"
			token={previewToken}
			code={sampleCode}
			lang="typescript"
			edit={false}
			run={false}
			save={false}
			collapsed={true}
		/>
	</section>

	<section class="rounded-2xl border border-gray-200/70 dark:border-gray-800/80 p-4 space-y-4">
		<h2 class="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
			Tool Call
		</h2>
		<ToolCallDisplay
			id="preview-tool-running"
			attributes={toolCallRunning}
			open={true}
			className="w-full"
		/>
		<ToolCallDisplay
			id="preview-tool-done"
			attributes={toolCallDone}
			open={true}
			className="w-full"
		/>
	</section>
</div>
