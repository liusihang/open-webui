<script lang="ts">
	import { getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import OnlyOfficeViewer from '$lib/components/common/OnlyOfficeViewer.svelte';

	const i18n = getContext('i18n');

	const normalizeMode = (rawMode: string | null) =>
		rawMode?.toLowerCase() === 'view' ? 'view' : 'edit';

	$: fileId = $page.url.searchParams.get('file_id')?.trim() ?? '';
	$: terminalServerId = $page.url.searchParams.get('terminal_server_id')?.trim() ?? '';
	$: terminalFilePath = $page.url.searchParams.get('terminal_file_path') ?? '';
	$: mode = normalizeMode($page.url.searchParams.get('mode'));
	$: readOnly = mode !== 'edit';
	$: hasValidSource = Boolean(fileId || (terminalServerId && terminalFilePath));
	$: displayName =
		(terminalFilePath ? terminalFilePath.split('/').pop() : '') ||
		fileId ||
		$i18n.t('Office Document');
</script>

<svelte:head>
	<title>{displayName} • {$i18n.t('OnlyOffice')}</title>
</svelte:head>

<div class="h-screen max-h-[100dvh] flex flex-col bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-100">
	<header
		class="shrink-0 h-12 border-b border-gray-100 dark:border-gray-800 px-3 sm:px-4 flex items-center justify-between gap-3"
	>
		<div class="min-w-0">
			<div class="text-[11px] uppercase tracking-wide text-gray-400 dark:text-gray-500">
				{$i18n.t('OnlyOffice')}
			</div>
			<div class="text-sm truncate">{displayName}</div>
		</div>
		<button
			class="shrink-0 px-2.5 py-1 rounded-md text-xs border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
			on:click={() => goto('/')}
		>
			{$i18n.t('Back')}
		</button>
	</header>

	<main class="flex-1 min-h-0 p-2 sm:p-3">
		{#if hasValidSource}
			<div class="w-full h-full rounded-lg border border-gray-100 dark:border-gray-800 overflow-hidden">
				<OnlyOfficeViewer
					{fileId}
					{terminalServerId}
					{terminalFilePath}
					{readOnly}
					className="w-full h-full"
				/>
			</div>
		{:else}
			<div class="h-full w-full flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('Missing OnlyOffice preview context. Please reopen from FileNav.')}
			</div>
		{/if}
	</main>
</div>
