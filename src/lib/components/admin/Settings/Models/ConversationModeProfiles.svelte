<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import {
		getConversationModeProfileHistory,
		getConversationModeProfileRevision,
		getConversationModeProfiles,
		ModeProfileApiError,
		restoreConversationModeProfile,
		saveConversationModeProfile,
		type ConversationMode,
		type ConversationModeProfileApiFailure,
		type ConversationModeProfileConflict,
		type ConversationModeProfileContent,
		type ConversationModeProfileHistory,
		type ConversationModeProfileRevision,
		type ConversationModeProfileValidationIssue,
		type ConversationModeProfileWarning
	} from '$lib/apis/configs';
	import ConversationModeProfileEditor from './ConversationModeProfileEditor.svelte';

	const modes: { mode: ConversationMode; label: string }[] = [
		{ mode: 'chat', label: 'Chat' },
		{ mode: 'agent', label: 'Agent' }
	];

	let activeMode: ConversationMode = 'chat';
	let profiles: Partial<Record<ConversationMode, ConversationModeProfileRevision>> = {};
	let histories: Partial<Record<ConversationMode, ConversationModeProfileHistory>> = {};
	let loading = true;
	let resetKey = 0;
	let validationIssues: ConversationModeProfileValidationIssue[] = [];
	let warnings: ConversationModeProfileWarning[] = [];
	let conflict: ConversationModeProfileConflict | null = null;
	let serviceError = '';
	let selectedRevision: ConversationModeProfileRevision | null = null;
	let pendingRestoreRevisionId: string | null = null;
	let showRestoreConfirmation = false;

	$: activeProfile = profiles[activeMode] ?? null;
	$: activeHistory = histories[activeMode] ?? null;

	const token = () => localStorage.token;

	const loadProfiles = async () => {
		const response = await getConversationModeProfiles(token());
		profiles = response.profiles.reduce(
			(acc, profile) => ({ ...acc, [profile.mode]: profile }),
			{} as Partial<Record<ConversationMode, ConversationModeProfileRevision>>
		);
	};

	const loadHistory = async (mode: ConversationMode) => {
		histories = { ...histories, [mode]: await getConversationModeProfileHistory(token(), mode) };
	};

	const refresh = async (mode = activeMode) => {
		await Promise.all([loadProfiles(), loadHistory(mode)]);
	};

	const refreshAfterConflict = async () => {
		// Deliberately do not increment resetKey: the editor keeps its unsaved local draft for comparison/retry.
		await refresh(activeMode);
	};

	const clearFeedback = () => {
		validationIssues = [];
		warnings = [];
		conflict = null;
		serviceError = '';
	};

	const setFailure = async (error: unknown) => {
		if (!(error instanceof ModeProfileApiError)) {
			serviceError = 'The profile service is unavailable. Please retry.';
			return;
		}

		const detail = error.detail as
			| ConversationModeProfileApiFailure
			| ConversationModeProfileConflict;
		if (error.status === 409 && detail.code === 'mode_profile_revision_conflict') {
			conflict = detail as ConversationModeProfileConflict;
			serviceError =
				'This profile changed while you were editing. Current data was refreshed; your draft is preserved.';
			await refreshAfterConflict();
			return;
		}

		validationIssues = 'issues' in detail ? (detail.issues ?? []) : [];
		serviceError =
			error.status >= 500
				? 'The profile service could not complete this request. No private prompt content was exposed.'
				: (detail.code ?? 'Profile validation failed.');
	};

	const save = async (
		content: ConversationModeProfileContent,
		expectedCurrentRevisionId: string
	) => {
		clearFeedback();
		try {
			const revision = await saveConversationModeProfile(
				token(),
				activeMode,
				expectedCurrentRevisionId,
				content
			);
			profiles = { ...profiles, [activeMode]: revision };
			warnings = revision.warnings;
			resetKey += 1;
			await loadHistory(activeMode);
			toast.success('Saved a new conversation mode revision');
		} catch (error) {
			await setFailure(error);
		}
	};

	const selectMode = async (mode: ConversationMode) => {
		activeMode = mode;
		clearFeedback();
		selectedRevision = null;
		if (!histories[mode]) {
			try {
				await loadHistory(mode);
			} catch (error) {
				await setFailure(error);
			}
		}
	};

	const loadRevisionDetail = async (revisionId: string) => {
		try {
			selectedRevision = await getConversationModeProfileRevision(token(), activeMode, revisionId);
		} catch (error) {
			await setFailure(error);
		}
	};

	const restore = async () => {
		if (!pendingRestoreRevisionId || !activeProfile) return;
		clearFeedback();
		try {
			const revision = await restoreConversationModeProfile(
				token(),
				activeMode,
				pendingRestoreRevisionId,
				activeProfile.revision_id
			);
			profiles = { ...profiles, [activeMode]: revision };
			warnings = revision.warnings;
			resetKey += 1;
			await loadHistory(activeMode);
			toast.success('Restored content as a new revision');
		} catch (error) {
			await setFailure(error);
		} finally {
			pendingRestoreRevisionId = null;
		}
	};

	onMount(async () => {
		try {
			await refresh();
		} catch (error) {
			await setFailure(error);
		} finally {
			loading = false;
		}
	});
</script>

<section
	class="mb-5 rounded-3xl border border-gray-100 bg-white p-4 dark:border-gray-850 dark:bg-gray-900"
	aria-labelledby="conversation-mode-defaults-title"
>
	<div class="mb-4 flex flex-wrap items-start justify-between gap-3">
		<div>
			<h2 id="conversation-mode-defaults-title" class="text-base font-medium">
				Conversation Mode Defaults
			</h2>
			<p class="mt-1 text-xs text-gray-500">
				Administrator templates for future conversations. Users may adjust supported defaults inside
				an individual conversation.
			</p>
		</div>
		{#if activeProfile}
			<div class="text-right text-xs text-gray-500">
				<div>Revision {activeProfile.revision_number} · {activeProfile.revision_id}</div>
				<div>
					{new Date(activeProfile.created_at * 1000).toLocaleString()} · {activeProfile.created_by ??
						'Unknown author'}
				</div>
				<div>
					Content hash: {activeProfile.content_hash
						? `${activeProfile.content_hash.slice(0, 12)}…`
						: 'Not provided by server'}
				</div>
			</div>
		{/if}
	</div>

	<div
		class="mb-4 flex gap-1 rounded-xl bg-gray-50 p-1 dark:bg-gray-850"
		role="tablist"
		aria-label="Conversation mode defaults"
	>
		{#each modes as item (item.mode)}
			<button
				class="rounded-lg px-3 py-1.5 text-sm {activeMode === item.mode
					? 'bg-white shadow-sm dark:bg-gray-700'
					: 'text-gray-500'}"
				type="button"
				role="tab"
				aria-selected={activeMode === item.mode}
				on:click={() => selectMode(item.mode)}>{item.label}</button
			>
		{/each}
	</div>

	{#if conflict}
		<div
			class="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
		>
			<div class="font-medium">Revision conflict</div>
			<div>
				The current revision is {conflict.current_revision.revision_number} ({conflict
					.current_revision.revision_id}). Your draft remains local for comparison and retry.
			</div>
		</div>
	{/if}
	{#if serviceError}
		<div
			class="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
		>
			{serviceError}
		</div>
	{/if}

	{#if loading}
		<div class="py-8 text-center text-sm text-gray-500">Loading conversation mode defaults…</div>
	{:else if activeProfile}
		<ConversationModeProfileEditor
			mode={activeMode}
			profile={activeProfile}
			currentRevisionId={activeProfile.revision_id}
			{resetKey}
			{validationIssues}
			{warnings}
			onSave={save}
		/>

		<div class="mt-6 border-t border-gray-100 pt-4 dark:border-gray-800">
			<h3 class="mb-2 text-sm font-medium">Revision history</h3>
			<div class="space-y-2">
				{#each activeHistory?.revisions ?? [] as revision (revision.revision_id)}
					<div
						class="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-gray-50 px-3 py-2 text-xs dark:bg-gray-850"
					>
						<div>
							Revision {revision.revision_number} · {revision.revision_id}{revision.is_current
								? ' (current)'
								: ''}
						</div>
						<div class="flex gap-2">
							<button
								type="button"
								class="underline"
								on:click={() => loadRevisionDetail(revision.revision_id)}>View detail</button
							>{#if !revision.is_current}<button
									type="button"
									class="underline"
									on:click={() => {
										pendingRestoreRevisionId = revision.revision_id;
										showRestoreConfirmation = true;
									}}>Restore as new revision</button
								>{/if}
						</div>
					</div>
				{/each}
			</div>
			{#if selectedRevision}
				<div class="mt-2 rounded-xl border border-gray-100 p-3 text-xs dark:border-gray-800">
					Private revision detail loaded: revision {selectedRevision.revision_number}. The System
					Prompt remains in this administrator component only.
				</div>
			{/if}
		</div>
	{:else}
		<div class="py-8 text-center text-sm text-gray-500">
			Conversation mode defaults are unavailable.
		</div>
	{/if}
</section>

<ConfirmDialog
	bind:show={showRestoreConfirmation}
	title="Restore this revision?"
	message="Restore creates a new revision. It does not mutate the historical revision."
	confirmLabel="Restore as new revision"
	onConfirm={restore}
/>
