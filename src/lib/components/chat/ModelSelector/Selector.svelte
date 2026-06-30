<script lang="ts">
	import { marked } from 'marked';
	import Fuse from 'fuse.js';

	import dayjs from '$lib/dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { flyAndScale } from '$lib/utils/transitions';

	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';
	import { goto } from '$app/navigation';

	import { deleteModel, getOllamaVersion, pullModel } from '$lib/apis/ollama';
	import { unloadModel } from '$lib/apis';

	import {
		user,
		MODEL_DOWNLOAD_POOL,
		models,
		mobile,
		temporaryChatEnabled,
		settings,
		config
	} from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { capitalizeFirstLetter, sanitizeResponseContent, splitStream } from '$lib/utils';
	import { getModels } from '$lib/apis';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';

	import ModelItem from './ModelItem.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let id = '';
	export let value = '';
	export let placeholder = $i18n.t('Select a model');
	export let searchEnabled = true;
	export let searchPlaceholder = $i18n.t('Search a model');

	export let items: {
		label: string;
		value: string;
		model: Model;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		[key: string]: any;
	}[] = [];

	export let className = 'w-[32rem]';
	export let triggerClassName = 'text-lg';

	export let pinModelHandler: (modelId: string) => void = () => {};

	let tagsContainerElement;

	let show = false;
	let triggerElement: HTMLElement | null = null;
	let contentElement: HTMLElement | null = null;
	let dropdownPosition = { top: 0, left: 0, width: 0 };

	const portal = (node: HTMLElement) => {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	};

	const updatePosition = () => {
		if (!show || !triggerElement) return;
		const rect = triggerElement.getBoundingClientRect();
		dropdownPosition = {
			top: rect.bottom + 2,
			left: $mobile ? 8 : rect.left,
			width: $mobile ? window.innerWidth - 16 : 0
		};
	};

	const toggleOpen = () => {
		show = !show;
		if (show) {
			searchValue = '';
			listScrollTop = 0;
			resetView();
			updatePosition();
			window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);
		} else {
			document.getElementById(`model-selector-${id}-button`)?.blur();
		}
	};

	const handlePointerDown = (e: PointerEvent) => {
		if (!show) return;
		const target = e.target as Node;
		if (
			(triggerElement && triggerElement.contains(target)) ||
			(contentElement && contentElement.contains(target))
		) {
			return;
		}
		show = false;
		document.getElementById(`model-selector-${id}-button`)?.blur();
	};

	const handleKeydown = (e: KeyboardEvent) => {
		if (show && e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			show = false;
			document.getElementById(`model-selector-${id}-button`)?.blur();
		}
	};

	let tags = [];

	let selectedModel = '';
	$: selectedModel = items.find((item) => item.value === value) ?? '';

	let searchValue = '';

	let selectedTag = '';
	let selectedConnectionType = '';

	let ollamaVersion = null;
	let selectedModelIdx = 0;

	const fuse = new Fuse(
		items.map((item) => {
			const _item = {
				...item,
				modelName: item.model?.name,
				tags: (item.model?.tags ?? []).map((tag) => tag.name).join(' '),
				desc: item.model?.info?.meta?.description
			};
			return _item;
		}),
		{
			keys: ['value', 'tags', 'modelName'],
			threshold: 0.4
		}
	);

	const updateFuse = () => {
		if (fuse) {
			fuse.setCollection(
				items.map((item) => {
					const _item = {
						...item,
						modelName: item.model?.name,
						tags: (item.model?.tags ?? []).map((tag) => tag.name).join(' '),
						desc: item.model?.info?.meta?.description
					};
					return _item;
				})
			);
		}
	};

	$: if (items) {
		updateFuse();
	}

	$: filteredItems = (
		searchValue
			? fuse
					.search(searchValue)
					.map((e) => {
						return e.item;
					})
					.filter((item) => {
						if (selectedTag === '') {
							return true;
						}

						return (item.model?.tags ?? [])
							.map((tag) => tag.name.toLowerCase())
							.includes(selectedTag.toLowerCase());
					})
					.filter((item) => {
						if (selectedConnectionType === '') {
							return true;
						} else if (selectedConnectionType === 'local') {
							return item.model?.connection_type === 'local';
						} else if (selectedConnectionType === 'external') {
							return item.model?.connection_type === 'external';
						} else if (selectedConnectionType === 'direct') {
							return item.model?.direct;
						}
					})
			: items
					.filter((item) => {
						if (selectedTag === '') {
							return true;
						}
						return (item.model?.tags ?? [])
							.map((tag) => tag.name.toLowerCase())
							.includes(selectedTag.toLowerCase());
					})
					.filter((item) => {
						if (selectedConnectionType === '') {
							return true;
						} else if (selectedConnectionType === 'local') {
							return item.model?.connection_type === 'local';
						} else if (selectedConnectionType === 'external') {
							return item.model?.connection_type === 'external';
						} else if (selectedConnectionType === 'direct') {
							return item.model?.direct;
						}
					})
	).filter((item) => !(item.model?.info?.meta?.hidden ?? false));

	$: if (
		selectedTag !== undefined ||
		selectedConnectionType !== undefined ||
		searchValue !== undefined
	) {
		resetView();
	}

	const resetView = async () => {
		await tick();

		const selectedInFiltered = filteredItems.findIndex((item) => item.value === value);

		if (selectedInFiltered >= 0) {
			// The selected model is visible in the current filter
			selectedModelIdx = selectedInFiltered;
		} else {
			// The selected model is not visible, default to first item in filtered list
			selectedModelIdx = 0;
		}

		// Set the virtual scroll position so the selected item is rendered and centered
		const targetScrollTop = Math.max(0, selectedModelIdx * ITEM_HEIGHT - 144 + ITEM_HEIGHT / 2);
		listScrollTop = targetScrollTop;

		await tick();

		if (listContainer) {
			listContainer.scrollTop = targetScrollTop;
		}

		await tick();
		const item = document.querySelector(`[data-arrow-selected="true"]`);
		item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
	};

	const pullModelHandler = async () => {
		const sanitizedModelTag = searchValue.trim().replace(/^ollama\s+(run|pull)\s+/, '');

		console.log($MODEL_DOWNLOAD_POOL);
		if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag]) {
			toast.error(
				$i18n.t(`Model '{{modelTag}}' is already in queue for downloading.`, {
					modelTag: sanitizedModelTag
				})
			);
			return;
		}
		if (Object.keys($MODEL_DOWNLOAD_POOL).length === 3) {
			toast.error(
				$i18n.t('Maximum of 3 models can be downloaded simultaneously. Please try again later.')
			);
			return;
		}

		const [res, controller] = await pullModel(localStorage.token, sanitizedModelTag, '0').catch(
			(error) => {
				toast.error(`${error}`);
				return null;
			}
		);

		if (res) {
			const reader = res.body
				.pipeThrough(new TextDecoderStream())
				.pipeThrough(splitStream('\n'))
				.getReader();

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL,
				[sanitizedModelTag]: {
					...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
					abortController: controller,
					reader,
					done: false
				}
			});

			while (true) {
				try {
					const { value, done } = await reader.read();
					if (done) break;

					let lines = value.split('\n');

					for (const line of lines) {
						if (line !== '') {
							let data = JSON.parse(line);
							console.log(data);
							if (data.error) {
								throw data.error;
							}
							if (data.detail) {
								throw data.detail;
							}

							if (data.status) {
								if (data.digest) {
									let downloadProgress = 0;
									if (data.completed) {
										downloadProgress = Math.round((data.completed / data.total) * 1000) / 10;
									} else {
										downloadProgress = 100;
									}

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											pullProgress: downloadProgress,
											digest: data.digest
										}
									});
								} else {
									toast.success(data.status);

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											done: data.status === 'success'
										}
									});
								}
							}
						}
					}
				} catch (error) {
					console.log(error);
					if (typeof error !== 'string') {
						error = error.message;
					}

					toast.error(`${error}`);
					// opts.callback({ success: false, error, modelName: opts.modelName });
					break;
				}
			}

			if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag].done) {
				toast.success(
					$i18n.t(`Model '{{modelName}}' has been successfully downloaded.`, {
						modelName: sanitizedModelTag
					})
				);

				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			} else {
				toast.error($i18n.t('Download canceled'));
			}

			delete $MODEL_DOWNLOAD_POOL[sanitizedModelTag];

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
		}
	};

	const setOllamaVersion = async () => {
		ollamaVersion = await getOllamaVersion(localStorage.token).catch((error) => false);
	};

	onMount(async () => {
		if (items) {
			tags = items
				.filter((item) => !(item.model?.info?.meta?.hidden ?? false))
				.flatMap((item) => item.model?.tags ?? [])
				.map((tag) => tag.name.toLowerCase());
			// Remove duplicates and sort
			tags = Array.from(new Set(tags)).sort((a, b) => a.localeCompare(b));
		}
	});

	$: if (show) {
		setOllamaVersion();
	}

	const cancelModelPullHandler = async (model: string) => {
		const { reader, abortController } = $MODEL_DOWNLOAD_POOL[model];
		if (abortController) {
			abortController.abort();
		}
		if (reader) {
			await reader.cancel();
			delete $MODEL_DOWNLOAD_POOL[model];
			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
			await deleteModel(localStorage.token, model);
			toast.success($i18n.t('{{model}} download has been canceled', { model: model }));
		}
	};

	const unloadModelHandler = async (model: string) => {
		const res = await unloadModel(localStorage.token, model).catch((error) => {
			toast.error($i18n.t('Error unloading model: {{error}}', { error }));
		});

		if (res) {
			toast.success($i18n.t('Model unloaded successfully'));
			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
		}
	};

	let showDeleteConfirm = false;
	let deleteModelTarget: any = null;

	const deleteModelHandler = async (model: any) => {
		deleteModelTarget = model;
		showDeleteConfirm = true;
	};

	const confirmDeleteModel = async () => {
		const model = deleteModelTarget;
		if (!model) return;

		const res = await deleteModel(localStorage.token, model.id).catch((error) => {
			toast.error($i18n.t('Error deleting model: {{error}}', { error }));
		});

		if (res) {
			// $i18n.t('Model {{modelId}} not found')
			toast.success(
				$i18n.t('Model {{modelName}} deleted successfully', { modelName: model.name ?? model.id })
			);

			// If the deleted model was selected, clear the selection
			if (value === model.id) {
				value = '';
			}

			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
		}

		deleteModelTarget = null;
	};

	const ITEM_HEIGHT = 56;
	const OVERSCAN = 10;

	let listScrollTop = 0;
	let listContainer;

	$: visibleStart = Math.max(0, Math.floor(listScrollTop / ITEM_HEIGHT) - OVERSCAN);
	$: visibleEnd = Math.min(
		filteredItems.length,
		Math.ceil((listScrollTop + 288) / ITEM_HEIGHT) + OVERSCAN
	);
</script>

<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete Model')}
	message={$i18n.t('Are you sure you want to delete **{{modelName}}**?', {
		modelName: deleteModelTarget?.name ?? deleteModelTarget?.id ?? ''
	})}
	on:confirm={() => {
		confirmDeleteModel();
	}}
/>

<svelte:window
	on:pointerdown={handlePointerDown}
	on:keydown={handleKeydown}
	on:resize={updatePosition}
/>

<div class="relative w-full">
	<button
		bind:this={triggerElement}
		class="relative w-full {($settings?.highContrastMode ?? false)
			? ''
			: 'outline-hidden focus:outline-hidden'}"
		aria-label={selectedModel
			? $i18n.t('Selected model: {{modelName}}', { modelName: selectedModel.label })
			: placeholder}
		aria-haspopup="listbox"
		aria-expanded={show}
		id="model-selector-{id}-button"
		type="button"
		on:click={toggleOpen}
	>
		<div
			class="flex max-w-full items-center justify-between gap-1 rounded-md border border-transparent bg-transparent px-2 py-1 text-left font-medium text-gray-900 transition hover:border-gray-200 hover:bg-white dark:text-gray-100 dark:hover:border-gray-700 dark:hover:bg-gray-900 {triggerClassName} {($settings?.highContrastMode ??
			false)
				? 'dark:placeholder-gray-100 placeholder-gray-800'
				: 'placeholder-gray-400'}"
			on:mouseenter={async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			}}
		>
			{#if selectedModel}
				<span class="truncate">{selectedModel.label}</span>
			{:else}
				<span class="truncate text-gray-500 dark:text-gray-400">{placeholder}</span>
			{/if}
			<ChevronDown className="ml-1 size-3 shrink-0 self-center text-gray-500" strokeWidth="2.5" />
		</div>
	</button>

	{#if show}
		<div
			use:portal
			bind:this={contentElement}
			style="position: fixed; z-index: 9999; top: {dropdownPosition.top}px; left: {dropdownPosition.left}px;{$mobile
				? ` width: ${dropdownPosition.width}px;`
				: ''}"
		>
			<div
				class="z-40 {$mobile
					? `w-full`
					: `${className}`} max-w-[calc(100vw-1rem)] justify-start overflow-hidden rounded-lg border border-gray-200 bg-white text-gray-950 shadow-sm outline-hidden dark:border-gray-800 dark:bg-gray-950 dark:text-white"
				transition:flyAndScale
			>
				<slot>
					{#if searchEnabled}
						<div
							class="flex items-center gap-2.5 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800"
						>
							<Search className="size-4 shrink-0 text-gray-500" strokeWidth="2.5" />

							<input
								id="model-search-input"
								bind:value={searchValue}
								class="w-full bg-transparent text-sm outline-hidden placeholder:text-gray-500 dark:placeholder:text-gray-400"
								placeholder={searchPlaceholder}
								autocomplete="off"
								aria-label={$i18n.t('Search In Models')}
								on:keydown={(e) => {
									if (e.code === 'Enter' && filteredItems.length > 0) {
										value = filteredItems[selectedModelIdx].value;
										show = false;
										return; // dont need to scroll on selection
									} else if (e.code === 'ArrowDown') {
										e.stopPropagation();
										selectedModelIdx = Math.min(selectedModelIdx + 1, filteredItems.length - 1);
									} else if (e.code === 'ArrowUp') {
										e.stopPropagation();
										selectedModelIdx = Math.max(selectedModelIdx - 1, 0);
									} else {
										// if the user types something, reset to the top selection.
										selectedModelIdx = 0;
									}

									const item = document.querySelector(`[data-arrow-selected="true"]`);
									item?.scrollIntoView({
										block: 'center',
										inline: 'nearest',
										behavior: 'instant'
									});
								}}
							/>
						</div>
					{/if}

					<div class="px-2 pt-2">
						{#if tags && items.filter((item) => !(item.model?.info?.meta?.hidden ?? false)).length > 0}
							<div
								class="mb-1 flex w-full overflow-x-auto bg-white font-[450] scrollbar-none dark:bg-gray-950"
								on:wheel={(e) => {
									if (e.deltaY !== 0) {
										e.preventDefault();
										e.currentTarget.scrollLeft += e.deltaY;
									}
								}}
							>
								<div
									class="flex w-fit gap-1 whitespace-nowrap rounded-md bg-gray-50 p-1 text-center text-xs dark:bg-gray-900"
									bind:this={tagsContainerElement}
								>
									{#if items.find((item) => item.model?.connection_type === 'local') || items.find((item) => item.model?.connection_type === 'external') || items.find((item) => item.model?.direct) || tags.length > 0}
										<button
											class="min-w-fit outline-none px-1.5 py-0.5 {selectedTag === '' &&
											selectedConnectionType === ''
												? 'rounded bg-white text-gray-900 shadow-xs dark:bg-gray-800 dark:text-white'
												: 'text-gray-500 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white'} transition capitalize"
											aria-pressed={selectedTag === '' && selectedConnectionType === ''}
											on:click={() => {
												selectedConnectionType = '';
												selectedTag = '';
											}}
										>
											{$i18n.t('All')}
										</button>
									{/if}

									{#if items.find((item) => item.model?.connection_type === 'local')}
										<button
											class="min-w-fit outline-none px-1.5 py-0.5 {selectedConnectionType ===
											'local'
												? 'rounded bg-white text-gray-900 shadow-xs dark:bg-gray-800 dark:text-white'
												: 'text-gray-500 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white'} transition capitalize"
											aria-pressed={selectedConnectionType === 'local'}
											on:click={() => {
												selectedTag = '';
												selectedConnectionType = 'local';
											}}
										>
											{$i18n.t('Local')}
										</button>
									{/if}

									{#if items.find((item) => item.model?.connection_type === 'external')}
										<button
											class="min-w-fit outline-none px-1.5 py-0.5 {selectedConnectionType ===
											'external'
												? 'rounded bg-white text-gray-900 shadow-xs dark:bg-gray-800 dark:text-white'
												: 'text-gray-500 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white'} transition capitalize"
											aria-pressed={selectedConnectionType === 'external'}
											on:click={() => {
												selectedTag = '';
												selectedConnectionType = 'external';
											}}
										>
											{$i18n.t('External')}
										</button>
									{/if}

									{#if items.find((item) => item.model?.direct)}
										<button
											class="min-w-fit outline-none px-1.5 py-0.5 {selectedConnectionType ===
											'direct'
												? 'rounded bg-white text-gray-900 shadow-xs dark:bg-gray-800 dark:text-white'
												: 'text-gray-500 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white'} transition capitalize"
											aria-pressed={selectedConnectionType === 'direct'}
											on:click={() => {
												selectedTag = '';
												selectedConnectionType = 'direct';
											}}
										>
											{$i18n.t('Direct')}
										</button>
									{/if}

									{#each tags as tag}
										<Tooltip content={tag}>
											<button
												class="min-w-fit outline-none px-1.5 py-0.5 {selectedTag === tag
													? 'rounded bg-white text-gray-900 shadow-xs dark:bg-gray-800 dark:text-white'
													: 'text-gray-500 hover:text-gray-900 dark:text-gray-500 dark:hover:text-white'} transition capitalize"
												aria-pressed={selectedTag === tag}
												on:click={() => {
													selectedConnectionType = '';
													selectedTag = tag;
												}}
											>
												{tag.length > 16 ? `${tag.slice(0, 16)}...` : tag}
											</button>
										</Tooltip>
									{/each}
								</div>
							</div>
						{/if}
					</div>

					<div class="group relative px-2.5 pb-2">
						{#if filteredItems.length === 0}
							{#if items.length === 0 && $user?.role === 'admin'}
								<div class="flex flex-col items-start justify-center px-4 py-6 text-start">
									<div class="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">
										{$i18n.t('No models available')}
									</div>
									<div class="text-xs text-gray-500 dark:text-gray-400 mb-4">
										{$i18n.t('Connect to an AI provider to start chatting')}
									</div>
									<a
										href="/admin/settings/connections"
										class="rounded-md bg-gray-900 px-4 py-1.5 text-xs font-medium text-white transition hover:bg-gray-800 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
										on:click={() => {
											show = false;
										}}
									>
										{$i18n.t('Manage Connections')}
									</a>
								</div>
							{:else}
								<div class="">
									<div class="block px-3 py-2 text-sm text-gray-700 dark:text-gray-100">
										{$i18n.t('No results found')}
									</div>
								</div>
							{/if}
						{:else}
							<!-- svelte-ignore a11y-no-static-element-interactions -->
							<div
								class="max-h-72 overflow-y-auto"
								role="listbox"
								aria-label={$i18n.t('Available models')}
								bind:this={listContainer}
								on:scroll={() => {
									listScrollTop = listContainer.scrollTop;
								}}
							>
								<div style="height: {visibleStart * ITEM_HEIGHT}px;"></div>
								{#each filteredItems.slice(visibleStart, visibleEnd) as item, i (item.value)}
									{@const index = visibleStart + i}
									<ModelItem
										{selectedModelIdx}
										{item}
										{index}
										{value}
										{pinModelHandler}
										{unloadModelHandler}
										{deleteModelHandler}
										onClick={() => {
											value = item.value;
											selectedModelIdx = index;

											show = false;
										}}
									/>
								{/each}
								<div style="height: {(filteredItems.length - visibleEnd) * ITEM_HEIGHT}px;"></div>
							</div>
						{/if}

						{#if !(searchValue.trim() in $MODEL_DOWNLOAD_POOL) && searchValue && ollamaVersion && $user?.role === 'admin'}
							<Tooltip
								content={$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
									searchValue: searchValue
								})}
								placement="top-start"
							>
								<button
									class="flex h-14 w-full cursor-pointer select-none items-center rounded-md px-2.5 py-2 text-sm font-medium text-gray-700 outline-hidden transition hover:bg-gray-100 dark:text-gray-100 dark:hover:bg-gray-800"
									on:click={() => {
										pullModelHandler();
									}}
								>
									<div class=" truncate">
										{$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
											searchValue: searchValue
										})}
									</div>
								</button>
							</Tooltip>
						{/if}

						{#each Object.keys($MODEL_DOWNLOAD_POOL) as model}
							<div
								class="flex h-14 w-full cursor-pointer select-none justify-between rounded-md px-2.5 py-2 text-sm font-medium text-gray-700 outline-hidden transition dark:text-gray-100"
							>
								<div class="flex">
									<div class="mr-2.5 translate-y-0.5">
										<Spinner />
									</div>

									<div class="flex flex-col self-start">
										<div class="flex gap-1">
											<div class="line-clamp-1">
												Downloading "{model}"
											</div>

											<div class="shrink-0">
												{'pullProgress' in $MODEL_DOWNLOAD_POOL[model]
													? `(${$MODEL_DOWNLOAD_POOL[model].pullProgress}%)`
													: ''}
											</div>
										</div>

										{#if 'digest' in $MODEL_DOWNLOAD_POOL[model] && $MODEL_DOWNLOAD_POOL[model].digest}
											<div class="-mt-1 h-fit text-[0.7rem] dark:text-gray-500 line-clamp-1">
												{$MODEL_DOWNLOAD_POOL[model].digest}
											</div>
										{/if}
									</div>
								</div>

								<div class="mr-2 ml-1 translate-y-0.5">
									<Tooltip content={$i18n.t('Cancel')}>
										<button
											class="text-gray-800 dark:text-gray-100"
											aria-label={$i18n.t('Cancel download of {{model}}', { model: model })}
											on:click={() => {
												cancelModelPullHandler(model);
											}}
										>
											<svg
												class="w-4 h-4 text-gray-800 dark:text-white"
												aria-hidden="true"
												xmlns="http://www.w3.org/2000/svg"
												width="24"
												height="24"
												fill="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke="currentColor"
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													d="M6 18 17.94 6M18 18 6.06 6"
												/>
											</svg>
										</button>
									</Tooltip>
								</div>
							</div>
						{/each}
					</div>

					<div class="pb-2.5"></div>

					<div class="hidden w-[42rem]"></div>
					<div class="hidden w-[32rem]"></div>
				</slot>
			</div>
		</div>
	{/if}
</div>
