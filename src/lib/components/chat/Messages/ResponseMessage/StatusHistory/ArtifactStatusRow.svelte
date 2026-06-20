<script lang="ts">
	import { slide } from 'svelte/transition';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Document from '$lib/components/icons/Document.svelte';

	type ArtifactDetail = {
		id: string;
		name: string;
		mimeType?: string;
		path?: string;
		size?: string;
	};

	export let description = '';
	export let detail: { artifact?: ArtifactDetail } | undefined;

	let open = false;

	$: artifact = detail?.artifact;
	$: name = artifact?.name ?? description ?? '文件';
	$: meta = [
		artifact?.mimeType,
		artifact?.size ? `${artifact.size} bytes` : null
	].filter(Boolean);
</script>

<div class="flex items-start gap-2 py-0.5 w-full text-left">
	<span class="flex-shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400">
		<Document className="w-3.5 h-3.5" />
	</span>

	<div class="min-w-0 flex-1">
		<button
			type="button"
			class="flex items-center gap-1 min-w-0 w-full text-left"
			on:click={() => (open = !open)}
		>
			<span class="text-gray-700 dark:text-gray-300 text-base line-clamp-1 text-wrap">
				{name}
			</span>
			{#if meta.length > 0}
				<span class="text-[11px] text-gray-400 dark:text-gray-500 shrink-0">
					{meta.join(' · ')}
				</span>
			{/if}
			<ChevronDown
				className="size-3 shrink-0 text-gray-400 transition-transform {open ? 'rotate-180' : ''}"
				strokeWidth="2.5"
			/>
		</button>

		{#if open}
			<div class="mt-1.5 text-xs text-gray-500 dark:text-gray-400" transition:slide={{ duration: 150 }}>
				{#if artifact?.path}
					<div class="break-all font-mono text-[11px]">{artifact.path}</div>
				{/if}
				{#if artifact?.id}
					<div class="mt-0.5 break-all font-mono text-[11px] text-gray-400 dark:text-gray-500">
						{artifact.id}
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>
