<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount, tick, onDestroy } from 'svelte';
	import { config } from '$lib/stores';

	import PyodideWorker from '$lib/workers/pyodide.worker?worker';
	import { executeCode } from '$lib/apis/utils';
	import {
		copyToClipboard,
		initMermaid,
		renderMermaidDiagram,
		renderVegaVisualization
	} from '$lib/utils';

	// svelte-highlight imports
	import { HighlightAuto } from 'svelte-highlight';
	import vs2015 from 'svelte-highlight/styles/vs2015';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import SvgPanZoom from '$lib/components/common/SVGPanZoom.svelte';
	import ChevronUpDown from '$lib/components/icons/ChevronUpDown.svelte';

	const i18n = getContext('i18n');

	export let id = '';
	export let edit = true;

	export let onSave = (e) => {};
	export let onUpdate = (e) => {};
	export let onPreview = (e) => {};

	export let save = false;
	export let run = true;
	export let preview = false;
	export let collapsed = false;

	export let token;
	export let lang = '';
	export let code = '';
	export let attributes = {};

	export let className = '';
	export let editorClassName = '';
	export let stickyButtonsClassName = 'top-0';

	let pyodideWorker = null;

	let _code = '';
	$: if (code) {
		updateCode();
	}

	const updateCode = () => {
		_code = code;
	};

	let _token = null;

	let renderHTML = null;
	let renderError = null;

	let executing = false;

	let stdout = null;
	let stderr = null;
	let result = null;
	let files = null;

	let copied = false;
	let saved = false;

	const collapseCodeBlock = () => {
		collapsed = !collapsed;
	};

	const saveCode = () => {
		saved = true;
		code = _code;
		onSave(code);
		setTimeout(() => {
			saved = false;
		}, 1000);
	};

	const copyCode = async () => {
		copied = true;
		await copyToClipboard(_code);
		setTimeout(() => {
			copied = false;
		}, 1000);
	};

	const previewCode = () => {
		onPreview(code);
	};

	const checkPythonCode = (str) => {
		const pythonSyntax = [
			'def ',
			'else:',
			'elif ',
			'try:',
			'except:',
			'finally:',
			'yield ',
			'lambda ',
			'assert ',
			'nonlocal ',
			'del ',
			'True',
			'False',
			'None',
			' and ',
			' or ',
			' not ',
			' in ',
			' is ',
			' with '
		];
		for (let syntax of pythonSyntax) {
			if (str.includes(syntax)) return true;
		}
		return false;
	};

	const executePython = async (code) => {
		result = null;
		stdout = null;
		stderr = null;
		executing = true;

		if ($config?.code?.engine === 'jupyter') {
			const output = await executeCode(localStorage.token, code).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (output) {
				if (output['stdout']) {
					stdout = output['stdout'];
					const stdoutLines = stdout.split('\n');
					for (const [idx, line] of stdoutLines.entries()) {
						if (line.startsWith('data:image/png;base64')) {
							if (files) files.push({ type: 'image/png', data: line });
							else files = [{ type: 'image/png', data: line }];

							if (stdout.includes(`${line}\n`)) stdout = stdout.replace(`${line}\n`, ``);
							else if (stdout.includes(`${line}`)) stdout = stdout.replace(`${line}`, ``);
						}
					}
				}
				if (output['result']) {
					result = output['result'];
					const resultLines = result.split('\n');
					for (const [idx, line] of resultLines.entries()) {
						if (line.startsWith('data:image/png;base64')) {
							if (files) files.push({ type: 'image/png', data: line });
							else files = [{ type: 'image/png', data: line }];

							if (result.includes(`${line}\n`)) result = result.replace(`${line}\n`, ``);
							else if (result.includes(`${line}`)) result = result.replace(`${line}`, ``);
						}
					}
				}
				output['stderr'] && (stderr = output['stderr']);
			}
			executing = false;
		} else {
			executePythonAsWorker(code);
		}
	};

	const executePythonAsWorker = async (code) => {
		let packages = [
			/\bimport\s+requests\b|\bfrom\s+requests\b/.test(code) ? 'requests' : null,
			/\bimport\s+bs4\b|\bfrom\s+bs4\b/.test(code) ? 'beautifulsoup4' : null,
			/\bimport\s+numpy\b|\bfrom\s+numpy\b/.test(code) ? 'numpy' : null,
			/\bimport\s+pandas\b|\bfrom\s+pandas\b/.test(code) ? 'pandas' : null,
			/\bimport\s+matplotlib\b|\bfrom\s+matplotlib\b/.test(code) ? 'matplotlib' : null,
			/\bimport\s+seaborn\b|\bfrom\s+seaborn\b/.test(code) ? 'seaborn' : null,
			/\bimport\s+sklearn\b|\bfrom\s+sklearn\b/.test(code) ? 'scikit-learn' : null,
			/\bimport\s+scipy\b|\bfrom\s+scipy\b/.test(code) ? 'scipy' : null,
			/\bimport\s+re\b|\bfrom\s+re\b/.test(code) ? 'regex' : null,
			/\bimport\s+seaborn\b|\bfrom\s+seaborn\b/.test(code) ? 'seaborn' : null,
			/\bimport\s+sympy\b|\bfrom\s+sympy\b/.test(code) ? 'sympy' : null,
			/\bimport\s+tiktoken\b|\bfrom\s+tiktoken\b/.test(code) ? 'tiktoken' : null,
			/\bimport\s+pytz\b|\bfrom\s+pytz\b/.test(code) ? 'pytz' : null
		].filter(Boolean);

		console.log(packages);

		pyodideWorker = new PyodideWorker();
		pyodideWorker.postMessage({ id: id, code: code, packages: packages });

		setTimeout(() => {
			if (executing) {
				executing = false;
				stderr = 'Execution Time Limit Exceeded';
				pyodideWorker.terminate();
			}
		}, 60000);

		pyodideWorker.onmessage = (event) => {
			console.log('pyodideWorker.onmessage', event);
			const { id, ...data } = event.data;
			console.log(id, data);

			if (data['stdout']) {
				stdout = data['stdout'];
				const stdoutLines = stdout.split('\n');
				for (const [idx, line] of stdoutLines.entries()) {
					if (line.startsWith('data:image/png;base64')) {
						if (files) files.push({ type: 'image/png', data: line });
						else files = [{ type: 'image/png', data: line }];

						if (stdout.includes(`${line}\n`)) stdout = stdout.replace(`${line}\n`, ``);
						else if (stdout.includes(`${line}`)) stdout = stdout.replace(`${line}`, ``);
					}
				}
			}

			if (data['result']) {
				result = data['result'];
				const resultLines = result.split('\n');
				for (const [idx, line] of resultLines.entries()) {
					if (line.startsWith('data:image/png;base64')) {
						if (files) files.push({ type: 'image/png', data: line });
						else files = [{ type: 'image/png', data: line }];

						if (result.startsWith(`${line}\n`)) result = result.replace(`${line}\n`, ``);
						else if (result.startsWith(`${line}`)) result = result.replace(`${line}`, ``);
					}
				}
			}

			data['stderr'] && (stderr = data['stderr']);
			data['result'] && (result = data['result']);
			executing = false;
		};

		pyodideWorker.onerror = (event) => {
			console.log('pyodideWorker.onerror', event);
			executing = false;
		};
	};

	let mermaid = null;
	const renderMermaid = async (code) => {
		if (!mermaid) {
			mermaid = await initMermaid();
		}
		return await renderMermaidDiagram(mermaid, code);
	};

	const render = async () => {
		onUpdate(token);
		if (lang === 'mermaid' && (token?.raw ?? '').slice(-4).includes('```')) {
			try {
				renderHTML = await renderMermaid(code);
			} catch (error) {
				console.error('Failed to render mermaid diagram:', error);
				const errorMsg = error instanceof Error ? error.message : String(error);
				renderError = $i18n.t('Failed to render diagram') + `: ${errorMsg}`;
				renderHTML = null;
			}
		} else if (
			(lang === 'vega' || lang === 'vega-lite') &&
			(token?.raw ?? '').slice(-4).includes('```')
		) {
			try {
				renderHTML = await renderVegaVisualization(code);
			} catch (error) {
				console.error('Failed to render Vega visualization:', error);
				const errorMsg = error instanceof Error ? error.message : String(error);
				renderError = $i18n.t('Failed to render visualization') + `: ${errorMsg}`;
				renderHTML = null;
			}
		}
	};

	$: if (token) {
		if (JSON.stringify(token) !== JSON.stringify(_token)) {
			_token = token;
		}
	}

	$: if (_token) {
		render();
	}

	$: if (attributes) {
		onAttributesUpdate();
	}

	const onAttributesUpdate = () => {
		if (attributes?.output) {
			const unescapeHtml = (html) => {
				const textArea = document.createElement('textarea');
				textArea.innerHTML = html;
				return textArea.value;
			};
			try {
				const unescapedOutput = unescapeHtml(attributes.output);
				const output = JSON.parse(unescapedOutput);
				stdout = output.stdout;
				stderr = output.stderr;
				result = output.result;
			} catch (error) {
				console.error('Error:', error);
			}
		}
	};

	onMount(async () => {
		if (token) {
			onUpdate(token);
		}
	});

	onDestroy(() => {
		if (pyodideWorker) {
			pyodideWorker.terminate();
		}
	});
</script>

<svelte:head>
	{@html vs2015}
</svelte:head>

<div class="my-2 group">
	<div
		class="code-block-container relative {className} flex flex-col rounded-xl border border-gray-200 dark:border-[#3c3c3c] shadow-sm overflow-hidden"
		dir="ltr"
	>
		{#if ['mermaid', 'vega', 'vega-lite'].includes(lang)}
			{#if renderHTML}
				<SvgPanZoom
					className="code-block-surface rounded-xl max-h-fit overflow-hidden"
					svg={renderHTML}
					content={_token.text}
				/>
			{:else}
				<div class="code-block-surface p-3">
					{#if renderError}
						<div
							class="flex gap-2.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-xl mb-2"
						>
							{renderError}
						</div>
					{/if}
					<pre>{code}</pre>
				</div>
			{/if}
		{:else}
			<!-- Mac-style Header -->
			<div
				class="code-block-surface flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-[#3c3c3c] text-gray-500 dark:text-[#c5c5c5] select-none {stickyButtonsClassName}"
			>
				<span class="text-xs font-medium lowercase opacity-75 macos-code-font">
					{lang || 'text'}
				</span>

				<div
					class="flex items-center gap-2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-opacity duration-200"
				>
					<button
						class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md transition text-xs font-medium"
						on:click={collapseCodeBlock}
						title={collapsed ? $i18n.t('Expand') : $i18n.t('Collapse')}
					>
						<ChevronUpDown className="size-3.5" />
						<span class="hidden sm:inline">
							{collapsed ? $i18n.t('Expand') : $i18n.t('Collapse')}
						</span>
					</button>

					{#if ($config?.features?.enable_code_execution ?? true) && (lang.toLowerCase() === 'python' || lang.toLowerCase() === 'py' || (lang === '' && checkPythonCode(code)))}
						{#if executing}
							<div
								class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
							>
								<svg
									class="animate-spin h-3 w-3 text-gray-500"
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
								>
									<circle
										class="opacity-25"
										cx="12"
										cy="12"
										r="10"
										stroke="currentColor"
										stroke-width="4"
									></circle>
									<path
										class="opacity-75"
										fill="currentColor"
										d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
									></path>
								</svg>
								<span>{$i18n.t('Running')}</span>
							</div>
						{:else if run}
							<button
								class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md transition text-xs font-medium"
								on:click={async () => {
									code = _code;
									await tick();
									executePython(code);
								}}
								title={$i18n.t('Run Code')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="currentColor"
									class="size-3.5"
								>
									<path
										fill-rule="evenodd"
										d="M4.5 5.653c0-1.426 1.529-2.33 2.779-1.643l11.54 6.348c1.295.712 1.295 2.573 0 3.285L7.28 19.991c-1.25.687-2.779-.217-2.779-1.643V5.653z"
										clip-rule="evenodd"
									/>
								</svg>
							</button>
						{/if}
					{/if}

					{#if save}
						<button
							class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md transition text-xs font-medium"
							on:click={saveCode}
							title={$i18n.t('Save')}
						>
							{#if saved}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-3.5 text-green-500"
								>
									<path
										fill-rule="evenodd"
										d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
										clip-rule="evenodd"
									/>
								</svg>
							{:else}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="1.5"
									stroke="currentColor"
									class="size-3.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
									/>
								</svg>
							{/if}
						</button>
					{/if}

					<button
						class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md transition text-xs font-medium"
						on:click={copyCode}
						title={$i18n.t('Copy')}
					>
						{#if copied}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-3.5 text-green-500"
							>
								<path
									fill-rule="evenodd"
									d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
									clip-rule="evenodd"
								/>
							</svg>
						{:else}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-3.5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M15.666 3.888A2.25 2.25 0 0 0 13.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 0 1-.75.75H9a.75.75 0 0 1-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 0 1 1.927-.184"
								/>
							</svg>
						{/if}
					</button>

					{#if preview && ['html', 'svg'].includes(lang)}
						<button
							class="code-block-action-btn flex items-center gap-1.5 px-2 py-1 rounded-md transition text-xs font-medium"
							on:click={previewCode}
							title={$i18n.t('Preview')}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-3.5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"
								/>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
								/>
							</svg>
						</button>
					{/if}
				</div>
			</div>

			<!-- Code Area -->
			<div class="code-block-surface relative overflow-hidden {editorClassName}">
				{#if !collapsed}
					{#if edit}
						<div class="code-editor-no-gutters">
							<CodeEditor
								value={code}
								{id}
								{lang}
								onSave={() => {
									saveCode();
								}}
								onChange={(value) => {
									_code = value;
								}}
							/>
						</div>
					{:else}
						<div class="code-block-pre overflow-x-auto codeblock-scroll">
							<HighlightAuto {code} class="!bg-transparent !p-4 text-sm macos-code-font" />
						</div>
					{/if}
				{:else}
					<button
						type="button"
						class="code-block-surface w-full py-2 px-4 flex items-center justify-center hover:opacity-90 transition"
						on:click={collapseCodeBlock}
					>
						<span class="text-gray-500 italic text-xs">
							{$i18n.t('{{COUNT}} hidden lines', { COUNT: code.split('\n').length })}
						</span>
						<ChevronUpDown className="size-3 ml-2 text-gray-500" />
					</button>
				{/if}
			</div>

			{#if !collapsed}
				<div
					id="plt-canvas-{id}"
					class="code-block-output-surface max-w-full overflow-x-auto scrollbar-hidden"
				></div>
			{/if}

			<!-- Output Area -->
			{#if !collapsed && (executing || stdout || stderr || result || files)}
				<div
					class="code-block-output-surface border-t border-gray-200 dark:border-gray-800/60 p-4 text-sm macos-code-font overflow-x-auto codeblock-scroll"
				>
					{#if executing}
						<div class="text-gray-500">{$i18n.t('Running...')}</div>
					{:else}
						{#if stdout || stderr}
							<div class="mb-2">
								<div class="text-xs text-gray-400 uppercase tracking-wider mb-1">
									{$i18n.t('STDOUT/STDERR')}
								</div>
								<div
									class="whitespace-pre-wrap codeblock-scroll {stdout?.split('\n')?.length > 100
										? `max-h-96 overflow-y-auto`
										: ''}"
								>
									{stdout || stderr}
								</div>
							</div>
						{/if}
						{#if result || files}
							<div>
								<div class="text-xs text-gray-400 uppercase tracking-wider mb-1">
									{$i18n.t('RESULT')}
								</div>
								{#if result}
									<div class="whitespace-pre-wrap">{`${JSON.stringify(result)}`}</div>
								{/if}
								{#if files}
									<div class="flex flex-col gap-2 mt-2">
										{#each files as file}
											{#if file.type.startsWith('image')}
												<img
													src={file.data}
													alt="Output"
													class="w-full max-w-lg rounded-lg border border-gray-200 dark:border-gray-800"
												/>
											{/if}
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					{/if}
				</div>
			{/if}
		{/if}
	</div>
</div>

<style>
	.macos-code-font {
		font-family: 'SF Mono', 'SFMono-Regular', Menlo, Monaco, Consolas, 'Liberation Mono',
			'Courier New', monospace;
	}

	.code-editor-no-gutters :global(.cm-gutters) {
		display: none !important;
	}

	.code-editor-no-gutters :global(.cm-editor) {
		font-family: 'SF Mono', 'SFMono-Regular', Menlo, Monaco, Consolas, 'Liberation Mono',
			'Courier New', monospace;
		background: transparent !important;
		color: inherit !important;
	}

	.code-editor-no-gutters :global(.cm-scroller) {
		background: transparent !important;
		color: inherit !important;
	}

	.code-editor-no-gutters :global(.cm-content) {
		padding-left: 0.75rem;
	}

	/* Refined scrollbar */
	:global(.codeblock-scroll::-webkit-scrollbar) {
		width: 8px;
		height: 8px;
	}
	:global(.codeblock-scroll::-webkit-scrollbar-track) {
		background: transparent;
	}
	:global(.codeblock-scroll::-webkit-scrollbar-thumb) {
		background: rgba(156, 163, 175, 0.5);
		border-radius: 4px;
	}
	:global(.codeblock-scroll::-webkit-scrollbar-thumb:hover) {
		background: rgba(156, 163, 175, 0.8);
	}
</style>
