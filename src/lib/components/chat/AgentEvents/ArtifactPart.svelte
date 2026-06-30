<script lang="ts">
	import type { AgentTranscriptArtifactPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import { copyToClipboard } from '$lib/utils';

	export let part: AgentTranscriptArtifactPart;

	let copiedPath = false;

	const shortPath = ($path: string | null): string => {
		if (!$path) return '';
		const segments = $path.split('/');
		if (segments.length <= 3) return $path;
		return `.../${segments.slice(-2).join('/')}`;
	};

	const fileName = ($path: string | null): string => {
		if (!$path) return '';
		return $path.split('/').filter(Boolean).at(-1) ?? $path;
	};

	const copyPath = async () => {
		if (!part.path) return;
		copiedPath = true;
		await copyToClipboard(part.path);
		setTimeout(() => {
			copiedPath = false;
		}, 1500);
	};
</script>

<div class="agent-artifact-card" data-artifact-id={part.artifactId}>
	<div class="agent-artifact-header">
		<div class="agent-artifact-title-group">
			<span class="agent-artifact-icon" aria-hidden="true">
				<Document className="size-3.5" strokeWidth="1.75" />
			</span>
			<div class="agent-artifact-copy">
				<span class="agent-artifact-name">{part.name ?? fileName(part.path) ?? part.summary}</span>
				<span class="agent-artifact-summary">{part.summary}</span>
			</div>
		</div>

		<div class="agent-artifact-actions">
			<span class="agent-artifact-status">Registered</span>
			{#if part.mimeType}
				<span class="agent-artifact-meta">{part.mimeType}</span>
			{/if}
			{#if part.path}
				<Tooltip content={copiedPath ? 'Copied path' : 'Copy path'}>
					<button
						type="button"
						class="agent-artifact-action"
						aria-label="Copy artifact path"
						on:click={copyPath}
					>
						<Clipboard className="size-3.5" strokeWidth="1.75" />
					</button>
				</Tooltip>
			{/if}
		</div>
	</div>

	{#if part.path}
		<button type="button" class="agent-artifact-path-chip" title={part.path} on:click={copyPath}>
			<span class="agent-artifact-path-prefix">./</span>{shortPath(part.path)}
		</button>
	{/if}

	<AgentDetailSection
		label="Artifact metadata"
		payload={part.details}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-artifact-card {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0.55rem 0.65rem;
		border-radius: 0.5rem;
		margin: 0.2rem 0;
		background: var(--white, #ffffff);
		border: 1px solid var(--gray-200, #e5e7eb);
		box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
	}
	.agent-artifact-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.65rem;
		min-width: 0;
	}
	.agent-artifact-title-group {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 0;
	}
	.agent-artifact-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.45rem;
		height: 1.45rem;
		border-radius: 0.4rem;
		color: var(--gray-600, #4b5563);
		background: var(--gray-100, #f3f4f6);
		border: 1px solid var(--gray-200, #e5e7eb);
		flex-shrink: 0;
	}
	.agent-artifact-copy {
		display: flex;
		flex-direction: column;
		gap: 0.08rem;
		min-width: 0;
	}
	.agent-artifact-name {
		color: var(--gray-800, #1f2937);
		font-size: 0.76rem;
		font-weight: 600;
		line-height: 1.15;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.agent-artifact-summary {
		color: var(--gray-500, #6b7280);
		font-size: 0.66rem;
		line-height: 1.2;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.agent-artifact-actions {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		flex-shrink: 0;
	}
	.agent-artifact-meta {
		color: var(--gray-500, #6b7280);
		background: var(--gray-50, #f9fafb);
		border: 1px solid var(--gray-200, #e5e7eb);
		border-radius: 9999px;
		font-size: 0.61rem;
		font-weight: 600;
		line-height: 1;
		padding: 0.25rem 0.4rem;
		text-transform: uppercase;
	}
	.agent-artifact-status {
		color: var(--green-700, #15803d);
		background: var(--green-50, #f0fdf4);
		border: 1px solid var(--green-200, #bbf7d0);
		border-radius: 9999px;
		font-size: 0.61rem;
		font-weight: 700;
		line-height: 1;
		padding: 0.25rem 0.4rem;
	}
	.agent-artifact-action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.45rem;
		height: 1.45rem;
		border-radius: 0.4rem;
		color: var(--gray-500, #6b7280);
		border: 1px solid var(--gray-200, #e5e7eb);
		background: rgba(255, 255, 255, 0.82);
		transition:
			background 120ms ease,
			color 120ms ease,
			border-color 120ms ease;
	}
	.agent-artifact-action:hover {
		color: var(--gray-900, #111827);
		background: var(--gray-50, #f9fafb);
		border-color: var(--gray-300, #d1d5db);
	}
	.agent-artifact-path-chip {
		display: inline-flex;
		align-items: center;
		width: fit-content;
		max-width: 100%;
		gap: 0.15rem;
		border-radius: 0.4rem;
		border: 1px solid rgba(96, 165, 250, 0.38);
		background: rgba(239, 246, 255, 0.95);
		color: var(--blue-700, #1d4ed8);
		font-size: 0.68rem;
		font-family: var(--font-mono, monospace);
		line-height: 1.2;
		padding: 0.28rem 0.42rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.agent-artifact-path-chip:hover {
		border-color: var(--blue-400, #60a5fa);
		background: var(--blue-50, #eff6ff);
	}
	.agent-artifact-path-prefix {
		color: var(--blue-400, #60a5fa);
	}
	:global(.dark) .agent-artifact-card {
		background: rgba(15, 23, 42, 0.94);
		border-color: rgba(148, 163, 184, 0.22);
		box-shadow: none;
	}
	:global(.dark) .agent-artifact-icon,
	:global(.dark) .agent-artifact-action,
	:global(.dark) .agent-artifact-meta,
	:global(.dark) .agent-artifact-status {
		background: rgba(30, 41, 59, 0.72);
		border-color: rgba(148, 163, 184, 0.22);
	}
	:global(.dark) .agent-artifact-name {
		color: var(--gray-100, #f3f4f6);
	}
	:global(.dark) .agent-artifact-summary,
	:global(.dark) .agent-artifact-icon,
	:global(.dark) .agent-artifact-action,
	:global(.dark) .agent-artifact-meta {
		color: var(--gray-400, #9ca3af);
	}
	:global(.dark) .agent-artifact-status {
		color: var(--green-300, #86efac);
	}
	:global(.dark) .agent-artifact-action:hover {
		color: var(--gray-100, #f3f4f6);
		background: rgba(51, 65, 85, 0.8);
	}
	:global(.dark) .agent-artifact-path-chip {
		background: rgba(37, 99, 235, 0.14);
		border-color: rgba(96, 165, 250, 0.35);
		color: var(--blue-200, #bfdbfe);
	}
</style>
