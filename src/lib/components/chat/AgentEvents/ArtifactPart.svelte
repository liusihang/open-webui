<script lang="ts">
	import type { AgentTranscriptArtifactPart } from './types';

	export let part: AgentTranscriptArtifactPart;

	const shortPath = ($path: string | null): string => {
		if (!$path) return '';
		const segments = $path.split('/');
		if (segments.length <= 3) return $path;
		return `.../${segments.slice(-2).join('/')}`;
	};
</script>

<div class="agent-artifact-part" data-artifact-id={part.artifactId}>
	<div class="agent-artifact-row">
		<span class="agent-artifact-icon" aria-hidden="true">▣</span>
		<span class="agent-artifact-name">{part.name ?? part.summary}</span>
		{#if part.mimeType}
			<span class="agent-artifact-meta">{part.mimeType}</span>
		{/if}
	</div>
	{#if part.path}
		<div class="agent-artifact-path">{shortPath(part.path)}</div>
	{/if}
</div>

<style>
	.agent-artifact-part {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		padding: 0.2rem 0;
		margin: 0.15rem 0;
	}
	.agent-artifact-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-artifact-icon {
		color: var(--gray-500, #6b7280);
	}
	.agent-artifact-name {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.agent-artifact-meta {
		color: var(--gray-400, #9ca3af);
		font-size: 0.65rem;
	}
	.agent-artifact-path {
		font-size: 0.7rem;
		color: var(--gray-500, #6b7280);
		font-family: var(--font-mono, monospace);
	}
</style>
