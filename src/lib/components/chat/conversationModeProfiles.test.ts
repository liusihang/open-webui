import { describe, expect, it } from 'vitest';

import {
	createConversationModeProfileDraftController,
	isDirectToolServersPermitted,
	resolveConversationModeProfile,
	type ConversationModeProfileResolutionInput
} from './conversationModeProfiles';

const model = (overrides: Record<string, unknown> = {}) => ({
	id: 'system-default-model',
	info: {
		meta: {
			terminalId: 'model-terminal',
			toolIds: ['model-tool'],
			skillIds: ['model-skill'],
			defaultFilterIds: ['model-filter'],
			defaultFeatureIds: ['web_search', 'image_generation'],
			capabilities: {
				terminal: true,
				web_search: true,
				code_interpreter: true,
				image_generation: true
			},
			...overrides
		}
	}
});

const available = {
	terminalIds: ['model-terminal', 'profile-terminal'],
	toolIds: ['model-tool', 'profile-tool'],
	skillIds: ['model-skill', 'profile-skill'],
	filterIds: ['model-filter', 'profile-filter'],
	featureIds: ['web_search', 'code_interpreter', 'image_generation']
};

const resolve = (overrides: Partial<ConversationModeProfileResolutionInput> = {}) =>
	resolveConversationModeProfile({
		mode: 'agent',
		profile: {
			mode: 'agent',
			current_revision_id: 'agent-r7',
			schema_version: 1,
			defaults: {}
		},
		model: model(),
		available,
		phase: 'initialize',
		...overrides
	});

describe('resolveConversationModeProfile', () => {
	it('uses explicit public profile defaults over model defaults without selecting a model', () => {
		const result = resolve({
			profile: {
				mode: 'agent',
				current_revision_id: 'agent-r7',
				schema_version: 1,
				defaults: {
					terminal_id: 'profile-terminal',
					tool_ids: ['profile-tool'],
					skill_ids: ['profile-skill'],
					filter_ids: ['profile-filter'],
					feature_ids: ['web_search']
				}
			}
		});

		expect(result.effective).toEqual({
			terminalId: 'profile-terminal',
			toolIds: ['profile-tool'],
			skillIds: ['profile-skill'],
			filterIds: ['profile-filter'],
			featureIds: ['web_search']
		});
		expect(result.revisionHint).toBe('agent-r7');
		expect(result.effective).not.toHaveProperty('modelId');
		expect(result.effective).not.toHaveProperty('reasoningEffort');
	});

	it('treats explicit empty values as clears and omitted fields as model inheritance', () => {
		const result = resolve({
			profile: {
				mode: 'agent',
				current_revision_id: 'agent-r7',
				schema_version: 1,
				defaults: {
					terminal_id: null,
					tool_ids: [],
					skill_ids: 'inherit'
				}
			}
		});

		expect(result.effective).toEqual({
			terminalId: null,
			toolIds: [],
			skillIds: ['model-skill'],
			filterIds: ['model-filter'],
			featureIds: ['web_search', 'image_generation']
		});
	});

	it('removes unavailable and forbidden selections with safe resource-only warnings', () => {
		const result = resolve({
			profile: {
				mode: 'agent',
				current_revision_id: 'agent-r7',
				schema_version: 1,
				defaults: {
					terminal_id: 'missing-terminal',
					tool_ids: ['profile-tool', 'forbidden-tool'],
					feature_ids: ['web_search', 'image_generation']
				}
			},
			available: { ...available, toolIds: ['profile-tool'], featureIds: ['web_search'] }
		});

		expect(result.effective).toEqual({
			terminalId: null,
			toolIds: ['profile-tool'],
			skillIds: ['model-skill'],
			filterIds: ['model-filter'],
			featureIds: ['web_search']
		});
		expect(result.warnings).toEqual([
			{ field: 'terminal_id', resourceIds: ['missing-terminal'] },
			{ field: 'tool_ids', resourceIds: ['forbidden-tool'] },
			{ field: 'feature_ids', resourceIds: ['image_generation'] }
		]);
	});

	it('keeps current temporary selections across a model change and removes only invalid capabilities', () => {
		const result = resolve({
			phase: 'model_change',
			currentSelections: {
				terminalId: 'profile-terminal',
				toolIds: ['profile-tool'],
				skillIds: ['profile-skill'],
				filterIds: ['profile-filter'],
				featureIds: ['web_search', 'code_interpreter']
			},
			model: model({
				capabilities: {
					terminal: false,
					web_search: true,
					code_interpreter: false,
					image_generation: true
				}
			})
		});

		expect(result.effective).toEqual({
			terminalId: null,
			toolIds: ['profile-tool'],
			skillIds: ['profile-skill'],
			filterIds: ['profile-filter'],
			featureIds: ['web_search']
		});
		expect(result.revisionHint).toBe('agent-r7');
		expect(result.warnings).toEqual([
			{ field: 'terminal_id', resourceIds: ['profile-terminal'] },
			{ field: 'feature_ids', resourceIds: ['code_interpreter'] }
		]);
	});

	it('keeps Terminal and Code Interpreter mutually exclusive when retaining model-change selections', () => {
		const result = resolve({
			phase: 'model_change',
			currentSelections: {
				terminalId: 'profile-terminal',
				toolIds: [],
				skillIds: [],
				filterIds: [],
				featureIds: ['code_interpreter']
			}
		});

		expect(result.effective.terminalId).toBe('profile-terminal');
		expect(result.effective.featureIds).not.toContain('code_interpreter');
	});

	it('treats omitted feature capabilities as supported but removes all function capabilities when explicitly disabled', () => {
		expect(
			resolve({
				profile: {
					mode: 'agent',
					current_revision_id: 'agent-r7',
					schema_version: 1,
					defaults: { feature_ids: ['web_search'] }
				},
				model: model({ capabilities: {} })
			}).effective.featureIds
		).toEqual(['web_search']);

		const disabled = resolve({
			phase: 'model_change',
			currentSelections: {
				terminalId: 'profile-terminal',
				toolIds: ['profile-tool'],
				skillIds: ['profile-skill'],
				filterIds: ['profile-filter'],
				featureIds: ['web_search']
			},
			model: model({ capabilities: { function_calling: false } })
		});

		expect(disabled.effective).toEqual({
			terminalId: null,
			toolIds: [],
			skillIds: [],
			filterIds: [],
			featureIds: ['web_search']
		});
	});

	it('matches TerminalMenu direct-tool permission semantics', () => {
		expect(isDirectToolServersPermitted({ role: 'admin' })).toBe(true);
		expect(isDirectToolServersPermitted({ role: 'user', permissions: { features: {} } })).toBe(
			true
		);
		expect(
			isDirectToolServersPermitted({
				role: 'user',
				permissions: { features: { direct_tool_servers: false } }
			})
		).toBe(false);
	});
});

describe('conversation mode profile draft controller', () => {
	it('initializes every persistent or local draft once, retains its public revision hint, and never mutates reasoning', () => {
		const controller = createConversationModeProfileDraftController();
		const initialization = resolve({
			profile: {
				mode: 'agent',
				current_revision_id: 'agent-r7',
				schema_version: 1,
				defaults: { terminal_id: 'profile-terminal' }
			}
		});

		expect(controller.initialize('persistent:draft-1', initialization)).toMatchObject({
			applied: true,
			revisionHint: 'agent-r7',
			effective: expect.objectContaining({ terminalId: 'profile-terminal' })
		});
		expect(controller.initialize('persistent:draft-1', resolve())).toMatchObject({
			applied: false,
			revisionHint: 'agent-r7',
			effective: expect.objectContaining({ terminalId: 'profile-terminal' })
		});
		expect(controller.initialize('local:draft-2', resolve())).toMatchObject({
			applied: true,
			revisionHint: 'agent-r7'
		});
		expect(controller.snapshot()).not.toHaveProperty('reasoningEffort');
		expect(controller.snapshot()).not.toHaveProperty('systemPrompt');
	});

	it('retains the bound revision across model changes and only replaces it from a canonical chat binding', () => {
		const controller = createConversationModeProfileDraftController();
		controller.initialize('local:draft-1', resolve());
		const modelChange = resolve({
			phase: 'model_change',
			currentSelections: controller.snapshot().effective,
			model: model({ capabilities: { terminal: false, web_search: true, image_generation: true } })
		});

		expect(controller.applyModelChange(modelChange)).toMatchObject({ revisionHint: 'agent-r7' });
		expect(controller.bindCanonicalRevision('agent-bound-r7')).toMatchObject({
			revisionHint: 'agent-bound-r7'
		});
	});

	it('hydrates a restored draft hint before model changes', () => {
		const controller = createConversationModeProfileDraftController();
		controller.hydrateRevisionHint('agent-restored-r7');

		expect(controller.applyModelChange(resolve({ phase: 'model_change' }))).toMatchObject({
			revisionHint: 'agent-restored-r7'
		});
	});
});
