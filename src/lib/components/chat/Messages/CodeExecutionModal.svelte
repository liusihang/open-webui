<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import CodeBlock from './CodeBlock.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { copyToClipboard } from '$lib/utils';

	type I18nValue = {
		t: (key: string, options?: Record<string, unknown>) => string;
	};
	type CodeExecutionFile = {
		name?: string;
		url?: string;
	};
	type CodeExecutionResult = {
		error?: string | null;
		output?: string | null;
		files?: CodeExecutionFile[] | null;
	};
	type CodeExecution = {
		id?: string;
		name?: string;
		language?: string;
		code?: string;
		result?: CodeExecutionResult | null;
	};

	const i18n = getContext<Readable<I18nValue>>('i18n');

	export let show = false;
	export let codeExecution: CodeExecution | null = null;

	let showFullOutput = false;
	let copiedOutput = false;
	let previousExecutionId: string | null = null;

	$: executionId = codeExecution?.id ?? null;
	$: if (executionId !== previousExecutionId) {
		previousExecutionId = executionId;
		showFullOutput = false;
		copiedOutput = false;
	}
	$: result = codeExecution?.result ?? null;
	$: resultFiles = result?.files ?? [];
	$: outputText = [
		result?.error ? `[error]\n${result.error}` : null,
		result?.output ? `[output]\n${result.output}` : null
	]
		.filter(Boolean)
		.join('\n\n');
	$: outputLineCount = outputText ? outputText.split('\n').length : 0;
	$: isLongOutput = outputText.length > 1800 || outputLineCount > 18;

	const copyOutput = async () => {
		if (!outputText) return;
		copiedOutput = true;
		await copyToClipboard(outputText);
		setTimeout(() => {
			copiedOutput = false;
		}, 1500);
	};
</script>

<Modal size="lg" bind:show>
	<div class="code-execution-shell">
		<div class="code-execution-header">
			<div class="code-execution-title">
				{#if codeExecution?.result}
					<div>
						{#if codeExecution.result?.error}
							<Badge type="error" content="error" />
						{:else if codeExecution.result?.output}
							<Badge type="success" content="success" />
						{:else}
							<Badge type="warning" content="incomplete" />
						{/if}
					</div>
				{/if}

				<div class="code-execution-title-row">
					{#if !codeExecution?.result}
						<Spinner className="size-4" />
					{/if}

					<div class="code-execution-title-text">
						{#if codeExecution?.name}
							{$i18n.t('Code execution')}: {codeExecution?.name}
						{:else}
							{$i18n.t('Code execution')}
						{/if}
					</div>
				</div>
			</div>
			<button
				class="code-execution-close"
				on:click={() => {
					show = false;
					codeExecution = null;
				}}
				aria-label={$i18n.t('Close')}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="code-execution-body">
			<div class="code-execution-content">
				<CodeBlock
					id="code-exec-{codeExecution?.id}-code"
					lang={codeExecution?.language ?? ''}
					code={codeExecution?.code ?? ''}
					token={null}
					className="code-execution-code"
					editorClassName={codeExecution?.result &&
					(codeExecution?.result?.error || codeExecution?.result?.output)
						? 'rounded-b-none'
						: ''}
					run={false}
				/>

				{#if codeExecution?.result && outputText}
					<section class="code-execution-terminal" aria-label={$i18n.t('Execution output')}>
						<div class="code-execution-terminal-header">
							<div class="code-execution-terminal-title">
								<Terminal className="size-3.5" strokeWidth="1.75" />
								<span>{$i18n.t('Output')}</span>
								{#if outputLineCount > 0}
									<span class="code-execution-terminal-count">{outputLineCount} lines</span>
								{/if}
							</div>
							<button class="code-execution-action" on:click={copyOutput}>
								<Clipboard className="size-3.5" strokeWidth="1.75" />
								<span>{copiedOutput ? $i18n.t('Copied') : $i18n.t('Copy')}</span>
							</button>
						</div>
						<div class="code-execution-output-frame">
							<pre
								class="code-execution-terminal-output"
								class:clamped={isLongOutput && !showFullOutput}>{outputText}</pre>
							{#if isLongOutput && !showFullOutput}
								<div class="code-execution-output-fade" aria-hidden="true"></div>
							{/if}
						</div>
						{#if isLongOutput}
							<button
								class="code-execution-show-more"
								on:click={() => (showFullOutput = !showFullOutput)}
							>
								{showFullOutput ? $i18n.t('Show less') : $i18n.t('Show more')}
							</button>
						{/if}
					</section>
				{/if}

				{#if resultFiles.length > 0}
					<section class="code-execution-files">
						<div class="code-execution-files-title">
							<span>{$i18n.t('Generated files')}</span>
							<span class="code-execution-files-count">
								{resultFiles.length} {$i18n.t('files')}
							</span>
						</div>
						<ul class="code-execution-file-list">
							{#each resultFiles as file}
								<li class="code-execution-file-chip">
									<span class="code-execution-file-icon" aria-hidden="true">
										<Document className="size-3.5" strokeWidth="1.75" />
									</span>
									<div class="code-execution-file-copy">
										{#if file.url}
											<a class="code-execution-file-name" href={file.url} target="_blank" rel="noreferrer">
												{file.name ?? file.url}
											</a>
										{:else}
											<span class="code-execution-file-name">
												{file.name ?? $i18n.t('Generated file')}
											</span>
										{/if}
										<span class="code-execution-file-path">
											{file.url ?? file.name ?? $i18n.t('Generated file')}
										</span>
									</div>
								</li>
							{/each}
						</ul>
					</section>
				{/if}
			</div>
		</div>
	</div>
</Modal>

<style>
	.code-execution-shell {
		display: flex;
		flex-direction: column;
		color: var(--gray-900, #111827);
		background: var(--white, #ffffff);
		border-radius: 0.75rem;
		overflow: hidden;
	}
	.code-execution-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 1rem 1.1rem 0.65rem;
		border-bottom: 1px solid var(--gray-100, #f3f4f6);
	}
	.code-execution-title {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		min-width: 0;
		font-weight: 600;
	}
	.code-execution-title-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 0;
	}
	.code-execution-title-text {
		font-size: 0.92rem;
		line-height: 1.25;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		text-transform: none;
	}
	.code-execution-close,
	.code-execution-action,
	.code-execution-show-more {
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--white, #ffffff);
		color: var(--gray-600, #4b5563);
		transition:
			background 120ms ease,
			border-color 120ms ease,
			color 120ms ease;
	}
	.code-execution-close {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border-radius: 0.5rem;
		flex-shrink: 0;
	}
	.code-execution-close:hover,
	.code-execution-action:hover,
	.code-execution-show-more:hover {
		background: var(--gray-50, #f9fafb);
		border-color: var(--gray-300, #d1d5db);
		color: var(--gray-900, #111827);
	}
	.code-execution-body {
		padding: 0.8rem 1rem 1rem;
	}
	.code-execution-content {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		width: 100%;
		max-height: min(70vh, 42rem);
		overflow-y: auto;
		scrollbar-width: thin;
	}
	:global(.code-execution-code) {
		margin: 0;
		border-radius: 0.65rem !important;
	}
	.code-execution-terminal {
		border-radius: 0.65rem;
		background: #0b1020;
		border: 1px solid rgba(148, 163, 184, 0.18);
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
		overflow: hidden;
	}
	.code-execution-terminal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.55rem 0.65rem;
		border-bottom: 1px solid rgba(148, 163, 184, 0.16);
		color: #dbeafe;
	}
	.code-execution-terminal-title {
		display: inline-flex;
		align-items: center;
		gap: 0.42rem;
		min-width: 0;
		font-size: 0.72rem;
		font-weight: 700;
	}
	.code-execution-terminal-count {
		color: #94a3b8;
		font-size: 0.65rem;
		font-weight: 500;
	}
	.code-execution-action {
		display: inline-flex;
		align-items: center;
		gap: 0.32rem;
		border-color: rgba(148, 163, 184, 0.2);
		background: rgba(15, 23, 42, 0.82);
		color: #cbd5e1;
		border-radius: 0.42rem;
		font-size: 0.68rem;
		font-weight: 600;
		line-height: 1;
		padding: 0.35rem 0.45rem;
	}
	.code-execution-action:hover {
		background: rgba(30, 41, 59, 0.9);
		border-color: rgba(148, 163, 184, 0.35);
		color: #f8fafc;
	}
	.code-execution-output-frame {
		position: relative;
	}
	.code-execution-terminal-output {
		margin: 0;
		padding: 0.85rem 0.95rem;
		color: #d7deea;
		font-family: var(--font-mono, monospace);
		font-size: 0.78rem;
		line-height: 1.55;
		white-space: pre-wrap;
		word-break: break-word;
		overflow: auto;
	}
	.code-execution-terminal-output.clamped {
		max-height: 16rem;
		overflow: hidden;
	}
	.code-execution-output-fade {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 4rem;
		pointer-events: none;
		background: linear-gradient(rgba(11, 16, 32, 0), #0b1020);
	}
	.code-execution-show-more {
		display: flex;
		width: 100%;
		align-items: center;
		justify-content: center;
		border-width: 1px 0 0;
		border-color: rgba(148, 163, 184, 0.16);
		border-radius: 0;
		background: rgba(15, 23, 42, 0.96);
		color: #cbd5e1;
		font-size: 0.72rem;
		font-weight: 600;
		padding: 0.55rem;
	}
	.code-execution-show-more:hover {
		background: rgba(30, 41, 59, 0.96);
		border-color: rgba(148, 163, 184, 0.22);
		color: #f8fafc;
	}
	.code-execution-files {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.65rem;
		background: var(--white, #ffffff);
		padding: 0.65rem 0.75rem;
	}
	.code-execution-files-title {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		color: var(--gray-700, #374151);
		font-size: 0.76rem;
		font-weight: 700;
	}
	.code-execution-files-count {
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 9999px;
		background: var(--gray-50, #f9fafb);
		color: var(--gray-500, #6b7280);
		font-size: 0.62rem;
		font-weight: 700;
		line-height: 1;
		padding: 0.22rem 0.38rem;
		white-space: nowrap;
	}
	.code-execution-file-list {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.code-execution-file-chip {
		display: flex;
		align-items: flex-start;
		gap: 0.5rem;
		min-width: 0;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.5rem;
		background: var(--gray-50, #f9fafb);
		padding: 0.5rem 0.55rem;
	}
	.code-execution-file-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.45rem;
		height: 1.45rem;
		flex-shrink: 0;
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 0.4rem;
		background: var(--white, #ffffff);
		color: var(--gray-500, #6b7280);
	}
	.code-execution-file-copy {
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
		min-width: 0;
	}
	.code-execution-file-name,
	.code-execution-file-path {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.code-execution-file-name {
		color: var(--gray-800, #1f2937);
		font-size: 0.75rem;
		font-weight: 650;
		line-height: 1.2;
		text-decoration: none;
	}
	.code-execution-file-path {
		color: var(--blue-700, #1d4ed8);
		font-family: var(--font-mono, monospace);
		font-size: 0.68rem;
		line-height: 1.2;
		text-decoration: none;
	}
	.code-execution-file-name:hover {
		text-decoration: underline;
	}
	:global(.dark) .code-execution-shell {
		background: var(--gray-900, #111827);
		color: var(--gray-100, #f3f4f6);
	}
	:global(.dark) .code-execution-header {
		border-bottom-color: rgba(148, 163, 184, 0.16);
	}
	:global(.dark) .code-execution-close {
		background: rgba(15, 23, 42, 0.82);
		border-color: rgba(148, 163, 184, 0.22);
		color: var(--gray-300, #d1d5db);
	}
	:global(.dark) .code-execution-close:hover {
		background: rgba(30, 41, 59, 0.9);
		color: var(--gray-100, #f3f4f6);
	}
	:global(.dark) .code-execution-files {
		background: rgba(15, 23, 42, 0.62);
		border-color: rgba(148, 163, 184, 0.18);
	}
	:global(.dark) .code-execution-files-title {
		color: var(--gray-200, #e5e7eb);
	}
	:global(.dark) .code-execution-files-count,
	:global(.dark) .code-execution-file-chip,
	:global(.dark) .code-execution-file-icon {
		background: rgba(30, 41, 59, 0.62);
		border-color: rgba(148, 163, 184, 0.22);
	}
	:global(.dark) .code-execution-file-name {
		color: var(--gray-100, #f3f4f6);
	}
	:global(.dark) .code-execution-file-path {
		color: var(--blue-300, #93c5fd);
	}
</style>
