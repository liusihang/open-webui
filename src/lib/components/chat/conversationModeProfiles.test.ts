import { describe, expect, it } from 'vitest';

import {
	createConversationModeCapabilityAuthorityController,
	createConversationModeProfileDraftController,
	isDirectToolServersPermitted,
	resolveConversationModeProfile,
	sanitizeConversationModeSelectedToolIds,
	serializeConversationModeCapabilityRequest,
	type ConversationModeProfileResolutionInput
} from './conversationModeProfiles';

const emptyCapabilities = {
	selectedToolIds: [],
	selectedSkillIds: [],
	selectedFilterIds: [],
	webSearchEnabled: false,
	codeInterpreterEnabled: false,
	imageGenerationEnabled: false
};

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

describe('conversation mode capability authority', () => {
	it('initializes a new draft as authoritative and serializes explicit empty selections', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: false });

		expect(controller.snapshot()).toBe('initialized');
		expect(
			serializeConversationModeCapabilityRequest({
				authority: controller.snapshot(),
				selections: {
					terminalId: null,
					toolIds: [],
					skillIds: [],
					filterIds: []
				},
				features: {
					voice: false,
					memory: true,
					web_search: false,
					code_interpreter: false,
					image_generation: false
				},
				directToolServersPermitted: true,
				directTerminalIds: [],
				functionCallingEnabled: true,
				terminalEnabled: true
			})
		).toEqual({
			request: {
				tool_ids: [],
				skill_ids: [],
				filter_ids: [],
				terminal_id: null,
				features: {
					voice: false,
					memory: true,
					web_search: false,
					code_interpreter: false,
					image_generation: false
				}
			},
			toolServerIds: [],
			emitToolServers: true
		});
	});

	it('loads any existing unmarked chat as inherit-bound and omits only controlled request fields', () => {
		const controller = createConversationModeCapabilityAuthorityController({
			existingChat: true,
			persistedAuthority: undefined
		});

		expect(controller.snapshot()).toBe('inherit_bound');
		expect(
			serializeConversationModeCapabilityRequest({
				authority: controller.snapshot(),
				selections: {
					terminalId: 'terminal-a',
					toolIds: ['tool-a'],
					skillIds: ['skill-a'],
					filterIds: ['filter-a']
				},
				features: {
					voice: true,
					memory: true,
					web_search: false,
					code_interpreter: false,
					image_generation: false
				},
				directToolServersPermitted: true,
				directTerminalIds: [],
				functionCallingEnabled: true,
				terminalEnabled: true
			})
		).toEqual({
			request: { features: { voice: true, memory: true } },
			toolServerIds: [],
			emitToolServers: false
		});
	});

	it.each(['initialized', 'inherit_bound', 'explicit'] as const)(
		'restores a persisted %s authority marker',
		(authority) => {
			expect(
				createConversationModeCapabilityAuthorityController({
					existingChat: true,
					persistedAuthority: authority
				}).snapshot()
			).toBe(authority);
		}
	);

	it('ignores prompt, file, and reasoning-only changes while preserving inherit-bound authority', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: true });
		controller.observe({ ...emptyCapabilities, prompt: '', files: [], reasoningEffort: 'medium' });

		expect(
			controller.observe({
				...emptyCapabilities,
				prompt: 'typed text',
				files: [{ id: 'file-1' }],
				reasoningEffort: 'high'
			})
		).toBe('inherit_bound');
	});

	it.each([
		['selectedToolIds', ['tool-a']],
		['selectedSkillIds', ['skill-a']],
		['selectedFilterIds', ['filter-a']],
		['webSearchEnabled', true],
		['codeInterpreterEnabled', true],
		['imageGenerationEnabled', true]
	] as const)(
		'latches explicit authority for a real %s change and remains explicit after clearing',
		(field, value) => {
			const controller = createConversationModeCapabilityAuthorityController({
				existingChat: true
			});
			controller.observe(emptyCapabilities);

			expect(controller.observe({ ...emptyCapabilities, [field]: value })).toBe('explicit');
			expect(controller.observe(emptyCapabilities)).toBe('explicit');
		}
	);

	it('latches explicit authority for Terminal changes', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: true });

		expect(controller.markExplicit()).toBe('explicit');
		expect(controller.snapshot()).toBe('explicit');
	});

	it('gates restored direct tool selections and all direct request routing when permission is denied', () => {
		expect(
			sanitizeConversationModeSelectedToolIds(
				['tool-a', 'direct_server:2', 'direct_server:stale'],
				false
			)
		).toEqual(['tool-a']);

		expect(
			serializeConversationModeCapabilityRequest({
				authority: 'explicit',
				selections: {
					terminalId: 'https://direct-terminal.example',
					toolIds: ['tool-a', 'direct_server:2', 'direct_server:stale'],
					skillIds: [],
					filterIds: []
				},
				features: {},
				directToolServersPermitted: false,
				directTerminalIds: ['https://direct-terminal.example'],
				functionCallingEnabled: true,
				terminalEnabled: true
			})
		).toEqual({
			request: {
				tool_ids: ['tool-a'],
				skill_ids: [],
				filter_ids: [],
				terminal_id: null,
				features: {}
			},
			toolServerIds: [],
			emitToolServers: true
		});
	});

	it('keeps direct server IDs for permitted explicit requests and clears function capabilities when unsupported', () => {
		expect(
			serializeConversationModeCapabilityRequest({
				authority: 'explicit',
				selections: {
					terminalId: 'system-terminal',
					toolIds: ['tool-a', 'direct_server:2', 'direct_server:server-a'],
					skillIds: ['skill-a'],
					filterIds: ['filter-a']
				},
				features: { web_search: false },
				directToolServersPermitted: true,
				directTerminalIds: [],
				functionCallingEnabled: false,
				terminalEnabled: false
			})
		).toEqual({
			request: {
				tool_ids: [],
				skill_ids: [],
				filter_ids: [],
				terminal_id: null,
				features: { web_search: false }
			},
			toolServerIds: ['2', 'server-a'],
			emitToolServers: true
		});
	});
});
