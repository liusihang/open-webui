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
		type ConversationModeProfileConflict,
		type ConversationModeProfileValidationIssue
	} from '$lib/apis/configs';
	import ConversationModeProfileEditor from './ConversationModeProfileEditor.svelte';
	import {
		contentFromDraft,
		createConversationModeProfileController,
		detailPresentation,
		modeForTabKey,
		normalizeProfileError,
		type ModeProfileRequest
	} from './conversationModeProfileState';

	const modes: { mode: ConversationMode; label: string }[] = [
		{ mode: 'chat', label: 'Chat' },
		{ mode: 'agent', label: 'Agent' }
	];

	const controller = createConversationModeProfileController();
	let activeMode: ConversationMode = 'chat';
	let initialLoading = true;
	let profilesRequest = 0;
	let pendingRestore: { mode: ConversationMode; revisionId: string } | null = null;
	let showRestoreConfirmation = false;
	let activeState = controller.state(activeMode);

	$: activeProfile = activeState.profile;
	$: activeHistory = activeState.history;
	$: activeDetail = activeState.detail;

	const token = () => localStorage.token;
	const touch = () => {
		activeState = controller.state(activeMode);
	};

	const loadProfiles = async () => {
		const request = ++profilesRequest;
		const response = await getConversationModeProfiles(token());
		if (request !== profilesRequest) return;
		for (const profile of response.profiles) controller.applyRevision(profile.mode, profile);
		touch();
	};

	const loadHistory = async (mode: ConversationMode) => {
		const request = controller.begin(mode, 'history');
		touch();
		try {
			const history = await getConversationModeProfileHistory(token(), request.mode);
			controller.completeHistory(request, history);
		} catch (error) {
			await setFailure(request, error);
		} finally {
			touch();
		}
	};

	const refresh = async (mode: ConversationMode) => {
		await Promise.all([loadProfiles(), loadHistory(mode)]);
	};

	const setFailure = async (request: ModeProfileRequest, error: unknown) => {
		if (!controller.accepts(request)) return;
		const state = controller.state(request.mode);
		if (error instanceof ModeProfileApiError) {
			const detail: unknown = error.detail;
			controller.fail(request, detail);
			const objectDetail = detail && typeof detail === 'object' ? detail : null;
			if (
				error.status === 409 &&
				objectDetail &&
				'code' in objectDetail &&
				objectDetail.code === 'mode_profile_revision_conflict'
			) {
				const conflict = objectDetail as ConversationModeProfileConflict;
				state.conflict = `Current revision is ${conflict.current_revision.revision_number} (${conflict.current_revision.revision_id}).`;
				state.error =
					'This profile changed while you were editing. Current metadata was refreshed; your draft is preserved.';
				touch();
				await refresh(request.mode);
				return;
			}
			state.validationIssues =
				objectDetail && 'issues' in objectDetail && Array.isArray(objectDetail.issues)
					? (objectDetail.issues as ConversationModeProfileValidationIssue[])
					: [];
			state.error =
				error.status >= 500
					? 'The profile service could not complete this request. No private prompt content was exposed.'
					: normalizeProfileError(detail);
		} else {
			controller.fail(request, error);
		}
		touch();
	};

	const save = async (mode: ConversationMode) => {
		const request = controller.begin(mode, 'save');
		if (!request.draft || !request.revisionId) {
			controller.fail(request, 'A current revision is required before saving.');
			touch();
			return;
		}
		touch();
		try {
			const revision = await saveConversationModeProfile(
				token(),
				request.mode,
				request.revisionId,
				contentFromDraft(request.draft)
			);
			if (controller.completeSave(request, revision)) {
				touch();
				await loadHistory(request.mode);
				toast.success('Saved a new conversation mode revision');
			}
		} catch (error) {
			await setFailure(request, error);
		} finally {
			touch();
		}
	};

	const selectMode = (mode: ConversationMode) => {
		activeMode = mode;
		controller.clearFeedback(mode);
		touch();
		if (!controller.state(mode).history) void loadHistory(mode);
	};

	const handleTabKeydown = (event: KeyboardEvent) => {
		const nextMode = modeForTabKey(activeMode, event.key);
		if (!nextMode) return;
		event.preventDefault();
		selectMode(nextMode);
		setTimeout(() => document.getElementById(`conversation-mode-tab-${nextMode}`)?.focus());
	};

	const loadRevisionDetail = async (mode: ConversationMode, revisionId: string) => {
		const request = controller.begin(mode, 'detail');
		touch();
		try {
			const detail = await getConversationModeProfileRevision(token(), request.mode, revisionId);
			controller.completeDetail(request, detail);
		} catch (error) {
			await setFailure(request, error);
		} finally {
			touch();
		}
	};

	const restore = async () => {
		if (!pendingRestore) return;
		const request = controller.begin(pendingRestore.mode, 'restore');
		const revisionId = pendingRestore.revisionId;
		pendingRestore = null;
		if (!request.revisionId) {
			controller.fail(request, 'A current revision is required before restoring.');
			touch();
			return;
		}
		touch();
		try {
			const revision = await restoreConversationModeProfile(
				token(),
				request.mode,
				revisionId,
				request.revisionId
			);
			if (controller.completeSave(request, revision)) {
				touch();
				await loadHistory(request.mode);
				toast.success('Restored content as a new revision');
			}
		} catch (error) {
			await setFailure(request, error);
		} finally {
			touch();
		}
	};

	onMount(async () => {
		try {
			await refresh('chat');
		} catch (error) {
			controller.state('chat').error = normalizeProfileError(error);
		} finally {
			initialLoading = false;
			touch();
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
		tabindex="-1"
		on:keydown={handleTabKeydown}
	>
		{#each modes as item (item.mode)}
			<button
				id="conversation-mode-tab-{item.mode}"
				class="rounded-lg px-3 py-1.5 text-sm {activeMode === item.mode
					? 'bg-white shadow-sm dark:bg-gray-700'
					: 'text-gray-500'}"
				type="button"
				role="tab"
				aria-selected={activeMode === item.mode}
				aria-controls="conversation-mode-panel-{item.mode}"
				tabindex={activeMode === item.mode ? 0 : -1}
				on:click={() => selectMode(item.mode)}>{item.label}</button
			>
		{/each}
	</div>

	<div
		id="conversation-mode-panel-{activeMode}"
		role="tabpanel"
		aria-labelledby="conversation-mode-tab-{activeMode}"
	>
		{#if activeState.conflict}
			<div
				class="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
			>
				<div class="font-medium">Revision conflict</div>
				<div>{activeState.conflict} Your draft remains local for comparison and retry.</div>
			</div>
		{/if}
		{#if activeState.error}
			<div
				class="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
			>
				{activeState.error}
			</div>
		{/if}

		{#if initialLoading}
			<div class="py-8 text-center text-sm text-gray-500">Loading conversation mode defaults…</div>
		{:else if activeProfile && activeState.draft}
			<ConversationModeProfileEditor
				mode={activeMode}
				draft={activeState.draft}
				dirty={activeState.dirty}
				saving={activeState.loading.save || activeState.loading.restore}
				validationIssues={activeState.validationIssues}
				warnings={activeState.warnings}
				onDraftChange={(updater) => {
					controller.updateDraft(activeMode, updater);
					touch();
				}}
				onSave={save}
			/>

			<div class="mt-6 border-t border-gray-100 pt-4 dark:border-gray-800">
				{#if activeDetail}
					{@const detail = detailPresentation(activeDetail)}
					<div class="rounded-xl border border-gray-100 p-3 text-xs dark:border-gray-800">
						<div class="mb-3 flex items-center justify-between gap-2">
							<h3 class="text-sm font-medium">Revision detail</h3>
							<button
								type="button"
								class="underline"
								on:click={() => {
									controller.clearDetail(activeMode);
									touch();
								}}>Back to history</button
							>
						</div>
						<h4 class="font-medium">Enforced System Prompt</h4>
						<pre
							class="mt-1 whitespace-pre-wrap rounded-lg bg-gray-50 p-2 font-sans dark:bg-gray-850">{detail.systemPrompt}</pre>
						<h4 class="mt-3 font-medium">Defaults</h4>
						<ul class="mt-1 list-disc space-y-1 pl-4">
							{#each detail.defaults as line}<li>{line}</li>{/each}
						</ul>
						<h4 class="mt-3 font-medium">Revision metadata</h4>
						<ul class="mt-1 list-disc space-y-1 pl-4">
							{#each detail.metadata as line}<li>{line}</li>{/each}
						</ul>
					</div>
				{:else}
					<h3 class="mb-2 text-sm font-medium">Revision history</h3>
					{#if activeState.loading.history}
						<div class="text-xs text-gray-500">Loading revision history…</div>
					{:else}
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
											disabled={activeState.loading.detail}
											on:click={() => loadRevisionDetail(activeMode, revision.revision_id)}
											>View detail</button
										>
										{#if !revision.is_current}<button
												type="button"
												class="underline"
												disabled={activeState.loading.restore}
												on:click={() => {
													pendingRestore = { mode: activeMode, revisionId: revision.revision_id };
													showRestoreConfirmation = true;
												}}>Restore as new revision</button
											>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					{/if}
				{/if}
			</div>
		{:else}
			<div class="py-8 text-center text-sm text-gray-500">
				Conversation mode defaults are unavailable.
			</div>
		{/if}
	</div>
</section>

<ConfirmDialog
	bind:show={showRestoreConfirmation}
	title="Restore this revision?"
	message="Restore creates a new revision. It does not mutate the historical revision."
	confirmLabel="Restore as new revision"
	onConfirm={restore}
/>
