import { describe, expect, it } from 'vitest';
import {
	ModeProfileApiError,
	type ConversationModeProfileConflict,
	type ConversationModeProfileRevision
} from '$lib/apis/configs';
import {
	createConversationModeProfileController,
	catalogItems,
	decodeDefaults,
	detailPresentation,
	modeForTabKey,
	normalizeProfileError,
	setProfileOperationFailure
} from './conversationModeProfileState';

const revision = (
	mode: 'chat' | 'agent',
	revisionId: string,
	defaults: ConversationModeProfileRevision['defaults'] = {}
): ConversationModeProfileRevision => ({
	revision_id: revisionId,
	mode,
	revision_number: 1,
	schema_version: 1,
	created_at: 0,
	created_by: 'admin',
	restored_from_revision_id: null,
	is_current: true,
	system_prompt: `${mode} prompt`,
	defaults,
	warnings: []
});

const deferred = <T>() => {
	let resolve: (value: T) => void = () => {};
	const promise = new Promise<T>((nextResolve) => {
		resolve = nextResolve;
	});
	return { promise, resolve };
};

describe('conversation mode profile state', () => {
	it('decodes omitted, null, and string terminal defaults distinctly', () => {
		expect(decodeDefaults({}).terminal).toMatchObject({ state: 'inherit', value: '' });
		expect(decodeDefaults({ terminal_id: null }).terminal).toMatchObject({
			state: 'disabled',
			value: ''
		});
		expect(decodeDefaults({ terminal_id: 'terminal-a' }).terminal).toMatchObject({
			state: 'override',
			value: 'terminal-a'
		});
	});

	it('uses the same property-presence semantics for collections and feature defaults', () => {
		const decoded = decodeDefaults({
			tool_ids: [],
			skill_ids: ['skill-a'],
			filter_ids: [],
			feature_ids: ['web_search']
		});

		expect(decoded.tools).toMatchObject({ state: 'disabled', ids: [] });
		expect(decoded.skills).toMatchObject({ state: 'override', ids: ['skill-a'] });
		expect(decoded.filters).toMatchObject({ state: 'disabled', ids: [] });
		expect(decoded.features).toMatchObject({ state: 'override', ids: ['web_search'] });
		expect(decodeDefaults({}).features.state).toBe('inherit');
	});

	it('keeps an unsaved draft and dirty state separate for Chat and Agent', () => {
		const controller = createConversationModeProfileController();
		controller.applyRevision('chat', revision('chat', 'chat-1'));
		controller.applyRevision('agent', revision('agent', 'agent-1'));

		controller.updateDraft('chat', (draft) => ({ ...draft, systemPrompt: 'chat local draft' }));
		controller.updateDraft('agent', (draft) => ({ ...draft, systemPrompt: 'agent local draft' }));

		expect(controller.state('chat').draft?.systemPrompt).toBe('chat local draft');
		expect(controller.state('chat').dirty).toBe(true);
		expect(controller.state('agent').draft?.systemPrompt).toBe('agent local draft');
		expect(controller.state('agent').dirty).toBe(true);
	});

	it('applies a deferred Chat save response only to the captured Chat request after tab changes', async () => {
		const controller = createConversationModeProfileController();
		controller.applyRevision('chat', revision('chat', 'chat-1'));
		controller.applyRevision('agent', revision('agent', 'agent-1'));
		const request = controller.begin('chat', 'save');
		if (!request) throw new Error('Expected a Chat save request');
		const response = deferred<ConversationModeProfileRevision>();

		const pending = response.promise.then((next) => controller.completeSave(request, next));
		response.resolve(revision('chat', 'chat-2'));
		await pending;

		expect(controller.state('chat').profile?.revision_id).toBe('chat-2');
		expect(controller.state('agent').profile?.revision_id).toBe('agent-1');
		expect(controller.state('agent').loading.save).toBe(false);
	});

	it('keeps the local draft while a conflict refresh updates the current revision metadata', () => {
		const controller = createConversationModeProfileController();
		controller.applyRevision('chat', revision('chat', 'chat-1'));
		controller.updateDraft('chat', (draft) => ({ ...draft, systemPrompt: 'do not discard' }));

		controller.applyRevision('chat', revision('chat', 'chat-2'), { preserveDraft: true });

		expect(controller.state('chat').profile?.revision_id).toBe('chat-2');
		expect(controller.state('chat').draft?.systemPrompt).toBe('do not discard');
		expect(controller.state('chat').dirty).toBe(true);
	});

	it('keeps conflict feedback visible while the same mode refreshes history or detail', () => {
		const controller = createConversationModeProfileController();
		controller.state('chat').error = 'Current metadata was refreshed; your draft is preserved.';
		controller.state('chat').conflict = 'Current revision is 2 (chat-2).';

		controller.begin('chat', 'history');
		expect(controller.state('chat').error).toContain('draft is preserved');
		expect(controller.state('chat').conflict).toContain('chat-2');

		controller.begin('chat', 'save');
		expect(controller.state('chat').error).toBe('');
		expect(controller.state('chat').conflict).toBeNull();
	});

	it('retains a conflicted draft and reports a failed refresh without rejecting', async () => {
		const controller = createConversationModeProfileController();
		controller.applyRevision('chat', revision('chat', 'chat-1'));
		controller.updateDraft('chat', (draft) => ({ ...draft, systemPrompt: 'keep this draft' }));
		const request = controller.begin('chat', 'save');
		if (!request) throw new Error('Expected a Chat save request');
		const conflict: ConversationModeProfileConflict = {
			code: 'mode_profile_revision_conflict',
			mode: 'chat',
			expected_current_revision_id: 'chat-1',
			current_revision: {
				revision_id: 'chat-2',
				revision_number: 2,
				schema_version: 1,
				created_at: 0
			}
		};

		await expect(
			setProfileOperationFailure({
				controller,
				request,
				error: new ModeProfileApiError(409, conflict),
				refresh: async () => {
					throw { detail: { message: 'metadata refresh unavailable' } };
				}
			})
		).resolves.toBeUndefined();

		expect(controller.state('chat').conflict).toContain('chat-2');
		expect(controller.state('chat').draft?.systemPrompt).toBe('keep this draft');
		expect(controller.state('chat').dirty).toBe(true);
		expect(controller.state('chat').loading.save).toBe(false);
		expect(controller.state('chat').error).toContain('This profile changed while you were editing');
		expect(controller.state('chat').error).toContain(
			'Refresh failed: metadata refresh unavailable'
		);
	});

	it('gates save and restore for the same mode until the active mutation finishes', () => {
		const controller = createConversationModeProfileController();
		controller.applyRevision('chat', revision('chat', 'chat-1'));
		const save = controller.begin('chat', 'save');
		if (!save) throw new Error('Expected a Chat save request');

		expect(controller.begin('chat', 'restore')).toBeNull();
		controller.fail(save, 'save did not complete');
		expect(controller.begin('chat', 'restore')).toMatchObject({
			mode: 'chat',
			operation: 'restore'
		});
	});

	it('normalizes string and object errors and exposes private detail only through a local presentation model', () => {
		expect(normalizeProfileError('temporarily unavailable')).toBe('temporarily unavailable');
		expect(normalizeProfileError({ detail: { reason: 'invalid terminal' } })).toBe(
			'invalid terminal'
		);

		const presentation = detailPresentation({
			...revision('chat', 'chat-2', { terminal_id: null, feature_ids: ['web_search'] }),
			content_hash: 'abcdef1234567890',
			restored_from_revision_id: 'chat-1'
		});
		expect(presentation.systemPrompt).toBe('chat prompt');
		expect(presentation.defaults).toEqual(
			expect.arrayContaining(['Terminal: Disabled', 'Feature defaults: Override (Web Search)'])
		);
		expect(presentation.metadata).toEqual(
			expect.arrayContaining(['Content hash: abcdef123456…', 'Restored from: chat-1'])
		);
	});

	it('keeps inactive selected resources visibly disabled and supports keyboard tab navigation', () => {
		expect(
			catalogItems(
				[
					{ id: 'active-tool', name: 'Active tool', is_active: true },
					{ id: 'retired-tool', name: 'Retired tool', is_active: false }
				],
				['retired-tool']
			)
		).toEqual([
			{
				id: 'active-tool',
				name: 'Active tool',
				active: true,
				disabled: false,
				label: 'Active tool'
			},
			{
				id: 'retired-tool',
				name: 'Retired tool',
				active: false,
				disabled: true,
				label: 'Retired tool (inactive)'
			}
		]);
		expect(catalogItems([], ['terminal-no-longer-available'])).toContainEqual({
			id: 'terminal-no-longer-available',
			name: 'terminal-no-longer-available',
			active: false,
			disabled: true,
			label: 'terminal-no-longer-available (Unavailable)'
		});
		expect(modeForTabKey('chat', 'ArrowRight')).toBe('agent');
		expect(modeForTabKey('agent', 'Home')).toBe('chat');
		expect(modeForTabKey('chat', 'End')).toBe('agent');
	});
});
