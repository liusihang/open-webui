<script lang="ts">
	import { onMount } from 'svelte';
	import { getFunctions } from '$lib/apis/functions';
	import {
		type ConversationMode,
		type ConversationModeProfileContent,
		type ConversationModeProfileDefault,
		type ConversationModeProfileRevision,
		type ConversationModeProfileValidationIssue,
		type ConversationModeProfileWarning
	} from '$lib/apis/configs';
	import { getSkills } from '$lib/apis/skills';
	import { getTerminalServers, type TerminalServer } from '$lib/apis/terminal';
	import { getTools } from '$lib/apis/tools';

	type TriState = 'inherit' | 'disabled' | 'override';
	type CatalogItem = { id: string; name?: string; type?: string };

	export let mode: ConversationMode = 'chat';
	export let profile: ConversationModeProfileRevision | null = null;
	export let currentRevisionId = '';
	export let resetKey = 0;
	export let validationIssues: ConversationModeProfileValidationIssue[] = [];
	export let warnings: ConversationModeProfileWarning[] = [];
	export let onSave: (
		content: ConversationModeProfileContent,
		expectedCurrentRevisionId: string
	) => Promise<void> = async () => {};

	let expectedRevisionId = '';
	let draftMode: ConversationMode = mode;
	let seenResetKey = -1;
	let systemPrompt = '';
	let terminalState: TriState = 'inherit';
	let terminalId = '';
	let toolsState: TriState = 'inherit';
	let toolIds: string[] = [];
	let skillsState: TriState = 'inherit';
	let skillIds: string[] = [];
	let filtersState: TriState = 'inherit';
	let filterIds: string[] = [];
	let featuresState: TriState = 'inherit';
	let featureIds: string[] = [];
	let dirty = false;
	let saving = false;

	let terminals: TerminalServer[] = [];
	let tools: CatalogItem[] = [];
	let skills: CatalogItem[] = [];
	let filters: CatalogItem[] = [];

	const featureOptions = [
		{ id: 'web_search', label: 'Web Search' },
		{ id: 'code_interpreter', label: 'Code Interpreter' },
		{ id: 'image_generation', label: 'Image Generation' }
	];

	const toCatalog = (value: unknown): CatalogItem[] => {
		const entries = Array.isArray(value)
			? value
			: Object.values((value ?? {}) as Record<string, unknown>);
		return entries
			.filter(
				(entry): entry is CatalogItem => !!entry && typeof entry === 'object' && 'id' in entry
			)
			.map((entry) => ({ id: String(entry.id), name: entry.name, type: entry.type }));
	};

	const stateFor = (
		value: ConversationModeProfileDefault<string | string[] | null> | undefined
	): TriState => {
		if (value === undefined) return 'inherit';
		if (value === 'inherit') return 'inherit';
		if (value === null || (Array.isArray(value) && value.length === 0)) return 'disabled';
		return 'override';
	};

	const idsFor = (value: ConversationModeProfileDefault<string[]> | undefined) =>
		Array.isArray(value) ? [...value] : [];

	const applyProfile = (next: ConversationModeProfileRevision | null) => {
		const defaults = next?.defaults ?? {};
		draftMode = mode;
		expectedRevisionId = next?.revision_id ?? currentRevisionId;
		systemPrompt = next?.system_prompt ?? '';
		terminalState = stateFor(defaults.terminal_id ?? 'inherit');
		terminalId =
			typeof defaults.terminal_id === 'string' && defaults.terminal_id !== 'inherit'
				? defaults.terminal_id
				: '';
		toolsState = stateFor(defaults.tool_ids ?? 'inherit');
		toolIds = idsFor(defaults.tool_ids ?? 'inherit');
		skillsState = stateFor(defaults.skill_ids ?? 'inherit');
		skillIds = idsFor(defaults.skill_ids ?? 'inherit');
		filtersState = stateFor(defaults.filter_ids ?? 'inherit');
		filterIds = idsFor(defaults.filter_ids ?? 'inherit');
		featuresState = stateFor(defaults.feature_ids ?? 'inherit');
		featureIds = idsFor(defaults.feature_ids ?? 'inherit');
		dirty = false;
	};

	$: if (profile && (draftMode !== mode || seenResetKey !== resetKey)) {
		seenResetKey = resetKey;
		applyProfile(profile);
	}

	$: if (currentRevisionId && currentRevisionId !== expectedRevisionId) {
		// A conflict refresh changes the next optimistic token but keeps this local draft intact.
		expectedRevisionId = currentRevisionId;
	}

	const markDirty = () => {
		dirty = true;
	};

	const toggle = (ids: string[], id: string) =>
		ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id];

	const collectionValue = (
		state: TriState,
		ids: string[]
	): ConversationModeProfileDefault<string[]> => {
		if (state === 'inherit') return 'inherit';
		return state === 'disabled' ? [] : ids;
	};

	const profileContent = (): ConversationModeProfileContent => ({
		schema_version: profile?.schema_version ?? 1,
		system_prompt: systemPrompt,
		defaults: {
			terminal_id:
				terminalState === 'inherit' ? 'inherit' : terminalState === 'disabled' ? null : terminalId,
			tool_ids: collectionValue(toolsState, toolIds),
			skill_ids: collectionValue(skillsState, skillIds),
			filter_ids: collectionValue(filtersState, filterIds),
			feature_ids: collectionValue(featuresState, featureIds)
		}
	});

	const save = async () => {
		if (!expectedRevisionId) return;
		saving = true;
		try {
			await onSave(profileContent(), expectedRevisionId);
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		const [terminalResponse, toolResponse, skillResponse, functionResponse] = await Promise.all([
			getTerminalServers(localStorage.token).catch(() => []),
			getTools(localStorage.token).catch(() => []),
			getSkills(localStorage.token).catch(() => []),
			getFunctions(localStorage.token).catch(() => [])
		]);
		terminals = terminalResponse;
		tools = toCatalog(toolResponse);
		skills = toCatalog(skillResponse);
		filters = toCatalog(functionResponse).filter((item) => item.type === 'filter');
	});
</script>

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
			bind:value={systemPrompt}
			on:input={markDirty}
		></textarea>
	</div>

	<div class="grid gap-4 md:grid-cols-2">
		<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
			<label class="mb-2 block text-sm font-medium" for="mode-profile-terminal">Terminal</label>
			<select
				id="mode-profile-terminal"
				class="w-full bg-transparent text-sm"
				bind:value={terminalState}
				on:change={markDirty}
			>
				<option value="inherit">Inherit</option>
				<option value="disabled">Disabled</option>
				<option value="override">Override</option>
			</select>
			{#if terminalState === 'override'}
				<select
					class="mt-2 w-full bg-transparent text-sm"
					bind:value={terminalId}
					on:change={markDirty}
				>
					<option value="">Select a Terminal</option>
					{#each terminals as terminal (terminal.id)}
						<option value={terminal.id}>{terminal.name || terminal.id}</option>
					{/each}
				</select>
			{/if}
		</div>

		{#each [{ label: 'Tools', state: toolsState, catalog: tools, ids: toolIds }, { label: 'Skills', state: skillsState, catalog: skills, ids: skillIds }, { label: 'Filters', state: filtersState, catalog: filters, ids: filterIds }] as field (field.label)}
			<div class="rounded-xl border border-gray-100 p-3 dark:border-gray-800">
				<label class="mb-2 block text-sm font-medium" for="mode-profile-{field.label}"
					>{field.label}</label
				>
				<select
					id="mode-profile-{field.label}"
					class="w-full bg-transparent text-sm"
					value={field.state}
					on:change={(event) => {
						const value = (event.currentTarget as HTMLSelectElement).value as TriState;
						if (field.label === 'Tools') toolsState = value;
						if (field.label === 'Skills') skillsState = value;
						if (field.label === 'Filters') filtersState = value;
						markDirty();
					}}
				>
					<option value="inherit">Inherit</option>
					<option value="disabled">Disabled</option>
					<option value="override">Override</option>
				</select>
				{#if field.state === 'override'}
					<div class="mt-2 max-h-28 space-y-1 overflow-auto text-xs">
						{#each field.catalog as item (item.id)}
							<label class="flex items-center gap-2"
								><input
									type="checkbox"
									checked={field.ids.includes(item.id)}
									on:change={() => {
										if (field.label === 'Tools') toolIds = toggle(toolIds, item.id);
										if (field.label === 'Skills') skillIds = toggle(skillIds, item.id);
										if (field.label === 'Filters') filterIds = toggle(filterIds, item.id);
										markDirty();
									}}
								/>
								{item.name || item.id}</label
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
				bind:value={featuresState}
				on:change={markDirty}
				aria-label="Feature default mode"
			>
				<option value="inherit">Inherit</option>
				<option value="disabled">Disabled</option>
				<option value="override">Override</option>
			</select>
		</div>
		{#if featuresState === 'override'}
			<div class="mt-3 grid gap-2 sm:grid-cols-3">
				{#each featureOptions as feature (feature.id)}
					<label class="flex items-center gap-2 text-sm"
						><input
							type="checkbox"
							checked={featureIds.includes(feature.id)}
							on:change={() => {
								featureIds = toggle(featureIds, feature.id);
								markDirty();
							}}
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
			disabled={!expectedRevisionId || saving}
			on:click={save}>{saving ? 'Saving…' : 'Save new revision'}</button
		>
	</div>
</div>
