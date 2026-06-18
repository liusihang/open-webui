<script lang="ts">
	import { Collapsible } from 'bits-ui';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Users from '$lib/components/icons/Users.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import AgentApprovalPanel from './AgentApprovalPanel.svelte';
	import AgentDetailSection from './AgentDetailSection.svelte';
	import AgentSubagentPanel from './AgentSubagentPanel.svelte';
	import AgentToolPanel from './AgentToolPanel.svelte';
	import type { AgentRunRenderGroup } from './renderModel';

	export let group: AgentRunRenderGroup;
	export let open = false;
	export let onOpenChange: (open: boolean) => void = () => {};

	const hasExpandableContent = (group: AgentRunRenderGroup) => group.detailSections.length > 0;

	const statusClass = (group: AgentRunRenderGroup) => {
		if (group.status === 'error') {
			return 'border-red-300/70 bg-red-50/70 text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200';
		}
		if (group.status === 'running') {
			return 'border-amber-300/70 bg-amber-50/70 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/30 dark:text-amber-100';
		}
		if (group.status === 'waiting') {
			return 'border-blue-300/70 bg-blue-50/70 text-blue-900 dark:border-blue-700/60 dark:bg-blue-950/30 dark:text-blue-100';
		}

		return 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-200';
	};

	const kindClass = (group: AgentRunRenderGroup) => {
		switch (group.kind) {
			case 'tool':
				return 'bg-blue-500/15 text-blue-700 dark:text-blue-200';
			case 'approval':
				return 'bg-amber-500/20 text-amber-800 dark:text-amber-100';
			case 'artifact':
				return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-200';
			case 'subagent':
				return 'bg-violet-500/15 text-violet-700 dark:text-violet-200';
			case 'model':
				return 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-200';
			case 'step':
				return 'bg-gray-500/15 text-gray-700 dark:text-gray-200';
			case 'run':
			case 'fallback':
			default:
				return 'bg-gray-500/15 text-gray-700 dark:text-gray-200';
		}
	};

	const kindLabel = (group: AgentRunRenderGroup) => {
		switch (group.kind) {
			case 'tool':
				return 'Tool';
			case 'approval':
				return 'Approval';
			case 'artifact':
				return 'Artifact';
			case 'subagent':
				return 'Subagent';
			case 'model':
				return 'Model';
			case 'step':
				return 'Step';
			case 'run':
				return 'Run';
			case 'fallback':
			default:
				return 'Update';
		}
	};
</script>

<Collapsible.Root
	class="border-b border-gray-100 last:border-b-0 dark:border-gray-800/80"
	disabled={!hasExpandableContent(group)}
	{open}
	{onOpenChange}
>
	<Collapsible.Trigger
		class="group flex w-full items-start gap-3 px-3 py-2.5 text-left transition hover:bg-gray-50 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-gray-900/40 dark:disabled:hover:bg-transparent"
	>
		<div class="mt-0.5 flex shrink-0 flex-col items-center">
			<div class="flex size-6 items-center justify-center rounded-full border {statusClass(group)}">
				{#if group.status === 'running'}
					<Spinner className="size-3.5" />
				{:else if group.status === 'error' || group.status === 'cancelled'}
					<XMark className="size-3.5" />
				{:else if group.kind === 'tool'}
					<Wrench className="size-3.5" />
				{:else if group.kind === 'artifact'}
					<Document className="size-3.5" />
				{:else if group.kind === 'subagent'}
					<Users className="size-3.5" />
				{:else if group.kind === 'model'}
					<Sparkles className="size-3.5" />
				{:else}
					<Check className="size-3.5" strokeWidth="2.5" />
				{/if}
			</div>
		</div>

		<div class="min-w-0 flex-1">
			<div class="flex min-w-0 flex-wrap items-center gap-1.5">
				<span
					class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase {kindClass(group)}"
				>
					{kindLabel(group)}
				</span>
				<span class="min-w-0 truncate font-medium text-gray-900 dark:text-gray-100">
					{group.title}
				</span>
			</div>

			{#if group.subtitle || group.metadata.length > 0}
				<div
					class="mt-1 flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400"
				>
					{#if group.subtitle}
						<span class="min-w-0 truncate">{group.subtitle}</span>
					{/if}
					{#each group.metadata as metadata}
						<span class="min-w-0 truncate">
							<span class="text-gray-400 dark:text-gray-500">{metadata.label}</span>
							<span>{metadata.value}</span>
						</span>
					{/each}
				</div>
			{/if}
		</div>

		{#if hasExpandableContent(group)}
			<div
				class="mt-1 shrink-0 text-gray-400 transition group-data-[state=open]:rotate-180 dark:text-gray-500"
			>
				<ChevronDown className="size-3.5" strokeWidth="2.5" />
			</div>
		{/if}
	</Collapsible.Trigger>

	{#if hasExpandableContent(group)}
		<Collapsible.Content class="px-3 pb-3 pl-12 text-xs text-gray-700 dark:text-gray-200">
			{#if group.kind === 'tool'}
				<AgentToolPanel {group} />
			{:else if group.kind === 'approval'}
				<AgentApprovalPanel {group} />
			{:else if group.kind === 'subagent'}
				<AgentSubagentPanel {group} />
			{:else}
				<div class="space-y-3">
					{#each group.detailSections as section (section.id)}
						<AgentDetailSection {section} />
					{/each}
				</div>
			{/if}
		</Collapsible.Content>
	{/if}
</Collapsible.Root>
