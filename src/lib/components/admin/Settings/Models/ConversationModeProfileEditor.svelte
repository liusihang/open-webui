<script lang="ts">
	import { onMount } from 'svelte';
	import { getFunctions } from '$lib/apis/functions';
	import type {
		ConversationMode,
		ConversationModeProfileValidationIssue,
		ConversationModeProfileWarning
	} from '$lib/apis/configs';
	import { getSkills } from '$lib/apis/skills';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { getTools } from '$lib/apis/tools';
	import {
		catalogItems,
		type ConversationModeProfileDraft,
		type TriState
	} from './conversationModeProfileState';

	type CollectionKey = 'tools' | 'skills' | 'filters';

	export let mode: ConversationMode = 'chat';
	export let draft: ConversationModeProfileDraft | null = null;
	export let dirty = false;
	export let saving = false;
	export let validationIssues: ConversationModeProfileValidationIssue[] = [];
	export let warnings: ConversationModeProfileWarning[] = [];
	export let onDraftChange: (
		updater: (draft: ConversationModeProfileDraft) => ConversationModeProfileDraft
	) => void = () => {};
	export let onSave: (mode: ConversationMode) => Promise<void> = async () => {};

	let rawTerminals: unknown = [];
	let rawTools: unknown = [];
	let rawSkills: unknown = [];
	let rawFilters: unknown = [];

	const featureOptions = [
		{ id: 'web_search', label: 'Web Search' },
		{ id: 'code_interpreter', label: 'Code Interpreter' },
		{ id: 'image_generation', label: 'Image Generation' }
	];

	$: terminals = catalogItems(rawTerminals, draft?.terminal.value ? [draft.terminal.value] : []);
	$: tools = catalogItems(rawTools, draft?.tools.ids ?? []);
	$: skills = catalogItems(rawSkills, draft?.skills.ids ?? []);
	$: filters = catalogItems(rawFilters, draft?.filters.ids ?? []);

	const change = (
		updater: (current: ConversationModeProfileDraft) => ConversationModeProfileDraft
	) => {
		if (draft) onDraftChange(updater);
	};

	const toggle = (ids: string[], id: string) =>
		ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id];

	const setCollectionState = (key: CollectionKey, state: TriState) =>
		change((current) => ({ ...current, [key]: { ...current[key], state } }));

	const toggleCollection = (key: CollectionKey, id: string) =>
		change((current) => ({
			...current,
			[key]: { ...current[key], ids: toggle(current[key].ids, id) }
		}));

	onMount(async () => {
		const [terminalResponse, toolResponse, skillResponse, functionResponse] = await Promise.all([
			getTerminalServers(localStorage.token).catch(() => []),
			getTools(localStorage.token).catch(() => []),
			getSkills(localStorage.token).catch(() => []),
			getFunctions(localStorage.token).catch(() => [])
		]);
		rawTerminals = terminalResponse;
		rawTools = toolResponse;
		rawSkills = skillResponse;
		rawFilters = Array.isArray(functionResponse)
			? functionResponse.filter((item) => item?.type === 'filter')
			: Object.values((functionResponse ?? {}) as Record<string, unknown>).filter(
					(item) =>
						!!item && typeof item === 'object' && (item as { type?: string }).type === 'filter'
				);
	});
</script>

{#if draft}
	<div class="space-y-5">
		<div>
			<label class="mb-1 block text-sm font-medium" for="mode-profile-system-prompt"
				>Enforced System Prompt</label
			>
			<p class="mb-2 text-xs text-gray-500">
				This administrator-only prompt is enforced server-side and is never exposed in ordinary
				conversation settings.
			</p>
			<textarea
				id="mode-profile-system-prompt"
				class="min-h-28 w-full rounded-xl border border-gray-200 bg-transparent p-3 text-sm outline-hidden dark:border-gray-800"
				value={draft.systemPrompt}
				on:input={(event) =>
					change((current) => ({
						...current,
						systemPrompt: (event.currentTarget as HTMLTextAreaElement).value
					}))}
			></textarea>
		</div>

		<div class="grid gap-4 md:grid-cols-2">
			<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
				<label class="mb-2 block text-sm font-medium" for="mode-profile-terminal">Terminal</label>
				<select
					id="mode-profile-terminal"
					class="w-full bg-transparent text-sm"
					value={draft.terminal.state}
					on:change={(event) =>
						change((current) => ({
							...current,
							terminal: {
								...current.terminal,
								state: (event.currentTarget as HTMLSelectElement).value as TriState
							}
						}))}
				>
					<option value="inherit">Inherit</option>
					<option value="disabled">Disabled</option>
					<option value="override">Override</option>
				</select>
				{#if draft.terminal.state === 'override'}
					<label class="sr-only" for="mode-profile-terminal-select">Terminal selection</label>
					<select
						id="mode-profile-terminal-select"
						class="mt-2 w-full bg-transparent text-sm"
						value={draft.terminal.value}
						on:change={(event) =>
							change((current) => ({
								...current,
								terminal: {
									...current.terminal,
									value: (event.currentTarget as HTMLSelectElement).value
								}
							}))}
					>
						<option value="">Select a terminal</option>
						{#each terminals as terminal (terminal.id)}
							<option value={terminal.id} disabled={terminal.disabled}>{terminal.label}</option>
						{/each}
					</select>
				{/if}
			</div>

			{#each [{ key: 'tools' as CollectionKey, label: 'Tools', value: draft.tools, catalog: tools }, { key: 'skills' as CollectionKey, label: 'Skills', value: draft.skills, catalog: skills }, { key: 'filters' as CollectionKey, label: 'Filters', value: draft.filters, catalog: filters }] as field (field.key)}
				<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
					<label class="mb-2 block text-sm font-medium" for="mode-profile-{field.key}"
						>{field.label}</label
					>
					<select
						id="mode-profile-{field.key}"
						class="w-full bg-transparent text-sm"
						value={field.value.state}
						on:change={(event) =>
							setCollectionState(
								field.key,
								(event.currentTarget as HTMLSelectElement).value as TriState
							)}
					>
						<option value="inherit">Inherit</option>
						<option value="disabled">Disabled</option>
						<option value="override">Override</option>
					</select>
					{#if field.value.state === 'override'}
						<div class="mt-2 max-h-28 space-y-1 overflow-auto text-xs">
							{#each field.catalog as item (item.id)}
								<label class="flex items-center gap-2" class:opacity-60={item.disabled}
									><input
										type="checkbox"
										checked={field.value.ids.includes(item.id)}
										disabled={item.disabled}
										on:change={() => toggleCollection(field.key, item.id)}
									/>
									{item.label}</label
								>
							{/each}
							{#if field.catalog.length === 0}<p class="text-gray-500">
									No active {field.label.toLowerCase()} found.
								</p>{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>

		<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
			<div class="flex items-center justify-between gap-3">
				<div>
					<div class="text-sm font-medium">Feature defaults</div>
					<p class="text-xs text-gray-500">
						Override makes the checked list explicit; unchecked features are explicitly disabled.
					</p>
				</div>
				<select
					class="bg-transparent text-sm"
					value={draft.features.state}
					on:change={(event) =>
						change((current) => ({
							...current,
							features: {
								...current.features,
								state: (event.currentTarget as HTMLSelectElement).value as TriState
							}
						}))}
					aria-label="Feature default mode"
				>
					<option value="inherit">Inherit</option>
					<option value="disabled">Disabled</option>
					<option value="override">Override</option>
				</select>
			</div>
			{#if draft.features.state === 'override'}
				<div class="mt-3 grid gap-2 sm:grid-cols-3">
					{#each featureOptions as feature (feature.id)}
						<label class="flex items-center gap-2 text-sm"
							><input
								type="checkbox"
								checked={draft.features.ids.includes(feature.id)}
								on:change={() =>
									change((current) => ({
										...current,
										features: {
											...current.features,
											ids: toggle(current.features.ids, feature.id)
										}
									}))}
							/>
							{feature.label}</label
						>
					{/each}
				</div>
			{/if}
		</div>

		{#if validationIssues.length > 0}
			<div
				class="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
			>
				<div class="font-medium">Validation errors</div>
				{#each validationIssues as issue}<div>
						{issue.resource_type}: {issue.resource_id} ({issue.reason})
					</div>{/each}
			</div>
		{/if}
		{#if warnings.length > 0}
			<div
				class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
			>
				<div class="font-medium">Warnings</div>
				{#each warnings as warning}<div>{warning.code}: {warning.field}</div>{/each}
			</div>
		{/if}

		<div class="flex items-center justify-between gap-3">
			{#if dirty}<span class="text-xs text-amber-700 dark:text-amber-300">Unsaved changes</span
				>{:else}<span class="text-xs text-gray-500">No unsaved changes</span>{/if}
			<button
				class="rounded-xl bg-black px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
				type="button"
				disabled={saving}
				on:click={() => onSave(mode)}>{saving ? 'Saving…' : 'Save new revision'}</button
			>
		</div>
	</div>
{/if}
