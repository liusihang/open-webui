<script lang="ts">
	import { getContext } from 'svelte';
	import { toolServers, tools, terminalServers, selectedTerminalId } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Collapsible from '../common/Collapsible.svelte';
	import Modal from '../common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		buildSelectedSystemTerminalTools,
		convertTerminalOpenApiToSpecs,
		type SystemTerminalToolSpec
	} from './MessageInput/systemTerminalTools';

	export let show = false;
	export let selectedToolIds = [];

	let selectedTools: any[] = [];
	let selectedSystemTerminalTools: any[] = [];
	let selectedSystemTerminalSpecs: SystemTerminalToolSpec[] = [];
	let loadedSelectedSystemTerminalId: string | null = null;
	let loadingSelectedSystemTerminalSpecs = false;

	$: selectedTools = ($tools ?? []).filter((tool) => selectedToolIds.includes(tool.id));
	$: selectedSystemTerminal = ($terminalServers ?? []).find(
		(terminal) => terminal.id === $selectedTerminalId
	);
	$: effectiveSelectedSystemTerminalSpecs =
		selectedSystemTerminal?.specs?.length > 0
			? selectedSystemTerminal.specs
			: loadedSelectedSystemTerminalId === selectedSystemTerminal?.id
				? selectedSystemTerminalSpecs
				: [];
	$: selectedSystemTerminalTools = Object.values(
		buildSelectedSystemTerminalTools(
			selectedSystemTerminal
				? [{ ...selectedSystemTerminal, specs: effectiveSelectedSystemTerminalSpecs }]
				: [],
			$selectedTerminalId
		)
	);
	$: if (
		show &&
		selectedSystemTerminal?.id &&
		!(selectedSystemTerminal?.specs?.length > 0) &&
		loadedSelectedSystemTerminalId !== selectedSystemTerminal.id &&
		!loadingSelectedSystemTerminalSpecs
	) {
		void loadSelectedSystemTerminalSpecs(selectedSystemTerminal.id);
	}

	const i18n = getContext('i18n');

	const loadSelectedSystemTerminalSpecs = async (terminalId: string) => {
		loadingSelectedSystemTerminalSpecs = true;
		selectedSystemTerminalSpecs = [];
		loadedSelectedSystemTerminalId = terminalId;

		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/terminals/${terminalId}/openapi.json`, {
				headers: {
					Authorization: `Bearer ${localStorage.token}`
				}
			});

			if (!res.ok) {
				return;
			}

			const openapi = await res.json();
			const specs = convertTerminalOpenApiToSpecs(openapi);
			selectedSystemTerminalSpecs = specs;

			if (specs.length > 0) {
				terminalServers.update((servers) =>
					servers.map((server) => (server.id === terminalId ? { ...server, specs } : server))
				);
			}
		} catch (error) {
			console.error('Failed to load selected system terminal tools', error);
		} finally {
			loadingSelectedSystemTerminalSpecs = false;
		}
	};
</script>

<Modal bind:show size="md">
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-5 pt-4 pb-0.5">
			<div class=" text-lg font-medium self-center">{$i18n.t('Available Tools')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		{#if selectedTools.length > 0 || selectedSystemTerminalTools.length > 0}
			{#if $toolServers.length > 0 || selectedTools.length > 0 || selectedSystemTerminalTools.length > 0}
				<div class=" flex justify-between dark:text-gray-300 px-5 pb-1">
					<div class=" text-base font-medium self-center">{$i18n.t('Tools')}</div>
				</div>
			{/if}

			<div class="px-5 pb-3 w-full flex flex-col justify-center">
				<div class=" text-sm dark:text-gray-300 mb-1">
					{#each selectedTools as tool}
						<Collapsible buttonClassName="w-full mb-0.5">
							<div class="truncate">
								<div class="text-sm font-medium dark:text-gray-100 text-gray-800 truncate">
									{tool?.name}
								</div>

								{#if tool?.meta?.description}
									<div class="text-xs text-gray-500">
										{tool?.meta?.description}
									</div>
								{/if}
							</div>

							<!-- <div slot="content">
							{JSON.stringify(tool, null, 2)}
						</div> -->
						</Collapsible>
					{/each}
					{#each selectedSystemTerminalTools as tool}
						<Collapsible buttonClassName="w-full mb-0.5">
							<div class="truncate">
								<div class="text-sm font-medium dark:text-gray-100 text-gray-800 truncate">
									{tool?.name}
								</div>

								{#if tool?.description}
									<div class="text-xs text-gray-500">
										{tool?.description}
									</div>
								{/if}
							</div>
						</Collapsible>
					{/each}
					{#if loadingSelectedSystemTerminalSpecs && selectedSystemTerminalTools.length === 0}
						<div class="px-3 py-2 text-xs text-gray-500">
							{$i18n.t('Loading terminal tools...')}
						</div>
					{/if}
				</div>
			</div>
		{/if}

		{#if $toolServers.length > 0}
			<div class=" flex justify-between dark:text-gray-300 px-5 pb-0.5">
				<div class=" text-base font-medium self-center">{$i18n.t('Tool Servers')}</div>
			</div>

			<div class="px-5 pb-5 w-full flex flex-col justify-center">
				<div class=" text-xs text-gray-600 dark:text-gray-300 mb-2">
					{$i18n.t('Open WebUI can use tools provided by any OpenAPI server.')} <br /><a
						class="underline"
						href="https://github.com/open-webui/openapi-servers"
						target="_blank">{$i18n.t('Learn more about OpenAPI tool servers.')}</a
					>
				</div>
				<div class=" text-sm dark:text-gray-300 mb-1">
					{#each $toolServers as toolServer}
						<Collapsible buttonClassName="w-full" chevron>
							<div>
								<div class="text-sm font-medium dark:text-gray-100 text-gray-800">
									{toolServer?.openapi?.info?.title} - v{toolServer?.openapi?.info?.version}
								</div>

								<div class="text-xs text-gray-500">
									{toolServer?.openapi?.info?.description}
								</div>

								<div class="text-xs text-gray-500">
									{toolServer?.url}
								</div>
							</div>

							<div slot="content">
								{#each toolServer?.specs ?? [] as tool_spec}
									<div class="my-1">
										<div class="font-medium text-gray-800 dark:text-gray-100">
											{tool_spec?.name}
										</div>

										<div>
											{tool_spec?.description}
										</div>
									</div>
								{/each}
							</div>
						</Collapsible>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</Modal>
