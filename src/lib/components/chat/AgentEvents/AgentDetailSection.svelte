<script lang="ts">
	import type { AgentRunEventMetadata, AgentRunEventPayload } from './types';

	export let label = 'Details';
	export let payload: AgentRunEventPayload | null = null;
	export let metadata: AgentRunEventMetadata[] = [];
	export let open = false;
	export let dense = true;

	const formatValue = (value: unknown): string => {
		if (value === null || value === undefined) {
			return '';
		}
		if (typeof value === 'string') {
			return value;
		}
		if (typeof value === 'number' || typeof value === 'boolean') {
			return `${value}`;
		}
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	};

	const entries = (): Array<{ key: string; value: unknown }> => {
		if (!payload || typeof payload !== 'object') {
			return [];
		}
		return Object.entries(payload)
			.filter(
				([key]) =>
					![
						'chain_of_thought',
						'debug',
						'private',
						'raw',
						'raw_reasoning',
						'reasoning',
						'thought'
					].includes(key.toLowerCase())
			)
			.map(([key, value]) => ({ key, value }));
	};
</script>

{#if entries().length > 0 || metadata.length > 0}
	<details class="agent-detail-section" {open}>
		<summary class="agent-detail-summary" on:click|preventDefault={() => (open = !open)}>
			<span class="agent-detail-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
			<span class="agent-detail-label">{label}</span>
		</summary>
		<div class="agent-detail-body" class:dense>
			{#if metadata.length > 0}
				<dl class="agent-detail-metadata">
					{#each metadata as entry}
						<div class="agent-detail-row">
							<dt>{entry.label}</dt>
							<dd>{entry.value}</dd>
						</div>
					{/each}
				</dl>
			{/if}
			{#if entries().length > 0}
				<div class="agent-detail-payload">
					{#each entries() as entry}
						<div class="agent-detail-payload-row">
							<span class="agent-detail-payload-key">{entry.key}</span>
							<pre class="agent-detail-payload-value">{formatValue(entry.value)}</pre>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</details>
{/if}

<style>
	.agent-detail-section {
		margin-top: 0.25rem;
	}
	.agent-detail-summary {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		cursor: pointer;
		list-style: none;
		color: var(--gray-500, #6b7280);
		font-size: 0.7rem;
		user-select: none;
	}
	.agent-detail-summary::-webkit-details-marker {
		display: none;
	}
	.agent-detail-chevron {
		font-size: 0.6rem;
	}
	.agent-detail-label {
		letter-spacing: 0.02em;
	}
	.agent-detail-body {
		margin-top: 0.25rem;
		padding: 0.5rem;
		background: var(--gray-50, #f9fafb);
		border-radius: 0.25rem;
		border: 1px solid var(--gray-100, #f3f4f6);
		font-size: 0.7rem;
		color: var(--gray-600, #4b5563);
	}
	.agent-detail-body.dense {
		padding: 0.4rem 0.5rem;
	}
	.agent-detail-metadata {
		margin: 0 0 0.25rem;
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.15rem 0.5rem;
	}
	.agent-detail-row {
		display: contents;
	}
	.agent-detail-row dt {
		font-weight: 500;
		color: var(--gray-500, #6b7280);
	}
	.agent-detail-row dd {
		margin: 0;
		color: var(--gray-700, #374151);
		word-break: break-word;
	}
	.agent-detail-payload {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.agent-detail-payload-row {
		display: grid;
		grid-template-columns: 8rem 1fr;
		gap: 0.5rem;
		align-items: start;
	}
	.agent-detail-payload-key {
		color: var(--gray-500, #6b7280);
		font-family: var(--font-mono, monospace);
		font-size: 0.65rem;
	}
	.agent-detail-payload-value {
		margin: 0;
		white-space: pre-wrap;
		word-break: break-word;
		font-family: var(--font-mono, monospace);
		font-size: 0.65rem;
		color: var(--gray-700, #374151);
		max-height: 12rem;
		overflow: auto;
	}
</style>
