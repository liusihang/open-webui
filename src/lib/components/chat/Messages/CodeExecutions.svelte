<script lang="ts">
	import CodeExecutionModal from './CodeExecutionModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';

	type CodeExecution = {
		id: string;
		name?: string;
		result?: {
			error?: string | null;
			output?: string | null;
		} | null;
	};

	export let codeExecutions: CodeExecution[] = [];

	let selectedCodeExecution: CodeExecution | null = null;
	let showCodeExecutionModal = false;

	$: if (codeExecutions) {
		updateSelectedCodeExecution();
	}

	const updateSelectedCodeExecution = () => {
		if (selectedCodeExecution) {
			const selectedId = selectedCodeExecution.id;
			selectedCodeExecution =
				codeExecutions.find((execution) => execution.id === selectedId) ?? null;
		}
	};
</script>

<CodeExecutionModal bind:show={showCodeExecutionModal} codeExecution={selectedCodeExecution} />

{#if codeExecutions.length > 0}
	<div class="code-executions-list">
		{#each codeExecutions as execution (execution.id)}
			<button
				class="code-execution-chip"
				class:pending={!execution?.result}
				class:error={Boolean(execution?.result?.error)}
				class:success={Boolean(
					execution?.result && !execution.result?.error && execution.result?.output
				)}
				on:click={() => {
					selectedCodeExecution = execution;
					showCodeExecutionModal = true;
				}}
			>
				<span class="code-execution-icon">
					{#if execution?.result}
						{#if execution.result?.error}
							<XMark className="size-3" strokeWidth="2.5" />
						{:else if execution.result?.output}
							<Check strokeWidth="3" className="size-3" />
						{:else}
							<EllipsisHorizontal className="size-3" />
						{/if}
					{:else}
						<Spinner className="size-3.5" />
					{/if}
				</span>
				<span class="code-execution-label {execution?.result ? '' : 'pulse'}">
					<Terminal className="size-3.5" strokeWidth="1.75" />
					<span>{execution.name}</span>
				</span>
			</button>
		{/each}
	</div>
{/if}

<style>
	.code-executions-list {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
		width: 100%;
		margin: 0.35rem 0 0.65rem;
	}
	.code-execution-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.42rem;
		max-width: min(24rem, 100%);
		border-radius: 0.55rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: rgba(249, 250, 251, 0.92);
		color: var(--gray-700, #374151);
		padding: 0.32rem 0.5rem 0.32rem 0.38rem;
		font-size: 0.72rem;
		font-weight: 600;
		line-height: 1;
		transition:
			background 120ms ease,
			border-color 120ms ease,
			color 120ms ease;
	}
	.code-execution-chip:hover {
		background: var(--gray-100, #f3f4f6);
		border-color: var(--gray-300, #d1d5db);
		color: var(--gray-900, #111827);
	}
	.code-execution-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.1rem;
		height: 1.1rem;
		border-radius: 9999px;
		background: white;
		color: var(--gray-500, #6b7280);
		flex-shrink: 0;
	}
	.code-execution-chip.success .code-execution-icon {
		background: var(--green-100, #d1fae5);
		color: var(--green-700, #047857);
	}
	.code-execution-chip.error .code-execution-icon {
		background: var(--red-100, #fee2e2);
		color: var(--red-700, #b91c1c);
	}
	.code-execution-label {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		min-width: 0;
	}
	.code-execution-label span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	:global(.dark) .code-execution-chip {
		background: rgba(15, 23, 42, 0.72);
		border-color: rgba(148, 163, 184, 0.24);
		color: var(--gray-300, #d1d5db);
	}
	:global(.dark) .code-execution-chip:hover {
		background: rgba(30, 41, 59, 0.86);
		border-color: rgba(148, 163, 184, 0.34);
		color: var(--gray-100, #f3f4f6);
	}
	:global(.dark) .code-execution-icon {
		background: rgba(30, 41, 59, 0.9);
	}
	@keyframes pulse {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.6;
		}
	}

	.pulse {
		opacity: 1;
		animation: pulse 1.5s ease;
	}
</style>
