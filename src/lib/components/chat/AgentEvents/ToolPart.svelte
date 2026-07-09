<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { AgentTranscriptToolPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptToolPart;

	const i18n = getContext<Writable<i18nType>>('i18n');

	const actionLabel = ($part: AgentTranscriptToolPart): string => {
		const name = $part.toolName ?? $i18n.t('tool');
		if ($part.status === 'error') return `${$i18n.t('Failed')} ${name}`;
		if ($part.status === 'done') return `${$i18n.t('Ran')} ${name}`;
		return `${$i18n.t('Running')} ${name}`;
	};
</script>

<div
	class="agent-tool-part"
	class:running={part.status === 'running'}
	class:error={part.status === 'error'}
	class:done={part.status === 'done'}
	data-tool-call-id={part.toolCallId}
>
	<div class="agent-tool-row">
		{#if part.status === 'running'}
			<span class="agent-tool-spinner" aria-hidden="true"></span>
		{:else if part.status === 'error'}
			<span class="agent-tool-icon error" aria-hidden="true">!</span>
		{:else}
			<span class="agent-tool-icon done" aria-hidden="true">✓</span>
		{/if}
		<span class="agent-tool-name">{actionLabel(part)}</span>
	</div>
	{#if part.status === 'error' && part.summary}
		<div class="agent-tool-summary">{part.summary}</div>
	{/if}
	{#if part.status === 'error'}
		<AgentDetailSection
			label={$i18n.t('Details')}
			payload={part.details}
			metadata={part.metadata}
			open={part.defaultExpanded}
		/>
	{/if}
</div>

<style>
	.agent-tool-part {
		display: flex;
		flex-direction: column;
		gap: 0.12rem;
		margin: 0.08rem 0;
	}
	.agent-tool-part.error {
		color: var(--red-700, #b91c1c);
	}
	.agent-tool-row {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.75rem;
	}
	.agent-tool-name {
		color: var(
			--agent-transcript-tool-color,
			var(--tw-prose-captions, var(--color-gray-400, #9ca3af))
		);
		font-weight: 500;
	}
	.agent-tool-summary {
		font-size: 0.72rem;
		color: var(
			--agent-transcript-tool-color,
			var(--tw-prose-captions, var(--color-gray-400, #9ca3af))
		);
	}
	.agent-tool-spinner {
		width: 0.5rem;
		height: 0.5rem;
		border: 1.5px solid var(--color-gray-300, #d1d5db);
		border-top-color: var(
			--agent-transcript-tool-color,
			var(--tw-prose-captions, var(--color-gray-400, #9ca3af))
		);
		border-radius: 9999px;
		animation: agent-tool-spin 0.8s linear infinite;
	}
	@keyframes agent-tool-spin {
		to {
			transform: rotate(360deg);
		}
	}
	.agent-tool-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 0.8rem;
		height: 0.8rem;
		border-radius: 9999px;
		font-size: 0.6rem;
		font-weight: 700;
	}
	.agent-tool-icon.done {
		background: transparent;
		color: var(
			--agent-transcript-tool-color,
			var(--tw-prose-captions, var(--color-gray-400, #9ca3af))
		);
	}
	.agent-tool-icon.error {
		color: var(--red-700, #b91c1c);
	}
</style>
