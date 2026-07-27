import { describe, expect, it } from 'vitest';

import * as conversationModeProfiles from './conversationModeProfiles';

import {
	createConversationModeCapabilityAuthorityController,
	createConversationModeProfileDraftController,
	getConversationModeAvailableToolIds,
	getConversationModeDraftCapabilitySnapshot,
	getNewConversationModeDraftCapabilityAuthority,
	isDirectToolServersPermitted,
	parseConversationModeDraft,
	resolveConversationModeProfile,
	sanitizeConversationModeSelectedToolIds,
	serializeConversationModeCapabilityRequest,
	serializeConversationModeToolServers,
	type ConversationModeDraft,
	type ConversationModeProfileResolutionInput
} from './conversationModeProfiles';

const requiredHelper = <T>(name: string): T => {
	expect(conversationModeProfiles).toHaveProperty(name);
	return (conversationModeProfiles as unknown as Record<string, unknown>)[name] as T;
};

const emptyCapabilities = {
	selectedToolIds: [],
	selectedSkillIds: [],
	selectedFilterIds: [],
	webSearchEnabled: false,
	codeInterpreterEnabled: false,
	imageGenerationEnabled: false
};

const completeDraft = (overrides: Record<string, unknown> = {}): ConversationModeDraft => ({
	prompt: 'draft prompt',
	files: [{ id: 'draft-file' }],
	modeProfileCapabilitySnapshotVersion: 1,
	modeProfileCapabilityAuthority: 'explicit',
	selectedToolIds: ['profile-tool'],
	selectedSkillIds: ['profile-skill'],
	selectedFilterIds: ['profile-filter'],
	webSearchEnabled: true,
	codeInterpreterEnabled: false,
	imageGenerationEnabled: false,
	selectedTerminalId: 'profile-terminal',
	...overrides
});

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

	it('removes configured direct terminal candidates when permission is revoked', () => {
		const filterTerminalCandidates = requiredHelper<
			(input: {
				candidateIds: readonly string[];
				configuredDirectTerminalIds: readonly string[];
				directToolServersPermitted: boolean;
			}) => string[]
		>('filterConversationModeTerminalCandidateIds');
		const input = {
			candidateIds: ['https://direct-terminal.example', 'system-terminal'],
			configuredDirectTerminalIds: ['https://direct-terminal.example']
		};

		expect(filterTerminalCandidates({ ...input, directToolServersPermitted: false })).toEqual([
			'system-terminal'
		]);
		expect(
			filterTerminalCandidates({
				candidateIds: input.candidateIds,
				configuredDirectTerminalIds: [],
				directToolServersPermitted: false
			})
		).toEqual(['system-terminal']);
		expect(filterTerminalCandidates({ ...input, directToolServersPermitted: true })).toEqual([
			'https://direct-terminal.example',
			'system-terminal'
		]);
	});

	it('resolves request-local selections against the actual send model without mutating UI input', () => {
		expect(conversationModeProfiles).toHaveProperty('resolveConversationModeRequestCapabilities');
		const resolveRequest = (conversationModeProfiles as any)
			.resolveConversationModeRequestCapabilities;
		const currentSelections = {
			terminalId: null,
			toolIds: ['profile-tool'],
			skillIds: ['profile-skill'],
			filterIds: ['profile-filter'],
			featureIds: ['web_search', 'code_interpreter']
		};
		const originalSelections = structuredClone(currentSelections);
		const input = {
			authority: 'explicit',
			mode: 'agent',
			profile: null,
			available,
			currentSelections
		};

		expect(resolveRequest({ ...input, model: model() }).effective).toEqual(currentSelections);
		expect(
			resolveRequest({
				...input,
				model: model({
					capabilities: {
						function_calling: false,
						web_search: false,
						code_interpreter: false
					}
				})
			}).effective
		).toEqual({ terminalId: null, toolIds: [], skillIds: [], filterIds: [], featureIds: [] });
		expect(currentSelections).toEqual(originalSelections);
		expect(
			resolveRequest({
				...input,
				authority: 'inherit_bound',
				model: model({ capabilities: { function_calling: false } })
			})
		).toMatchObject({ effective: currentSelections, warnings: [] });
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
	it('accepts only structurally valid stored drafts', () => {
		expect(parseConversationModeDraft(null)).toBeNull();
		expect(parseConversationModeDraft('{malformed')).toBeNull();
		expect(parseConversationModeDraft('[]')).toBeNull();
		expect(parseConversationModeDraft('{}')).toBeNull();
		expect(
			parseConversationModeDraft(
				JSON.stringify({ prompt: '', files: [], modeProfileCapabilityAuthority: 'explicit' })
			)
		).toEqual({ prompt: '', files: [], modeProfileCapabilityAuthority: 'explicit' });
	});

	it('restores legacy draft content without granting capability authority', () => {
		const legacyDraft = parseConversationModeDraft(
			JSON.stringify({
				prompt: 'legacy prompt',
				files: [{ id: 'legacy-file' }],
				reasoningEffort: 'high'
			})
		);

		expect(legacyDraft).toMatchObject({
			prompt: 'legacy prompt',
			files: [{ id: 'legacy-file' }],
			reasoningEffort: 'high'
		});
		expect(getNewConversationModeDraftCapabilityAuthority(legacyDraft)).toBeNull();
		expect(
			getNewConversationModeDraftCapabilityAuthority({
				prompt: '',
				files: [],
				modeProfileCapabilityAuthority: 'inherit_bound'
			})
		).toBeNull();
		expect(
			getNewConversationModeDraftCapabilityAuthority(
				completeDraft({ modeProfileCapabilityAuthority: 'initialized' })
			)
		).toBe('initialized');
		expect(getNewConversationModeDraftCapabilityAuthority(completeDraft())).toBe('explicit');
	});

	it('requires a complete typed capability snapshot before honoring its marker', () => {
		const rootSnapshot = getConversationModeDraftCapabilitySnapshot(
			completeDraft({ modeProfileCapabilityAuthority: 'initialized' }),
			{ existingChat: false }
		);
		expect(rootSnapshot).toEqual({
			authority: 'initialized',
			selections: {
				terminalId: 'profile-terminal',
				toolIds: ['profile-tool'],
				skillIds: ['profile-skill'],
				filterIds: ['profile-filter'],
				featureIds: ['web_search']
			}
		});
		expect(
			getConversationModeDraftCapabilitySnapshot(completeDraft(), { existingChat: true })
		).toMatchObject({ authority: 'explicit' });
		expect(
			getConversationModeDraftCapabilitySnapshot(
				completeDraft({ modeProfileCapabilitySnapshotVersion: undefined }),
				{ existingChat: false }
			)
		).toBeNull();
		expect(
			getConversationModeDraftCapabilitySnapshot(
				completeDraft({ modeProfileCapabilitySnapshotVersion: 2 }),
				{ existingChat: false }
			)
		).toBeNull();
		expect(
			getConversationModeDraftCapabilitySnapshot(
				completeDraft({ modeProfileCapabilityAuthority: 'initialized' }),
				{ existingChat: true }
			)
		).toBeNull();
		expect(
			getConversationModeDraftCapabilitySnapshot(
				completeDraft({ modeProfileCapabilityAuthority: 'inherit_bound' }),
				{ existingChat: false }
			)
		).toBeNull();

		for (const field of [
			'selectedToolIds',
			'selectedSkillIds',
			'selectedFilterIds',
			'webSearchEnabled',
			'codeInterpreterEnabled',
			'imageGenerationEnabled',
			'selectedTerminalId'
		]) {
			const incomplete = completeDraft();
			delete incomplete[field];
			expect(
				getConversationModeDraftCapabilitySnapshot(incomplete, { existingChat: false })
			).toBeNull();
		}
		expect(
			getConversationModeDraftCapabilitySnapshot(
				completeDraft({ selectedToolIds: ['tool-a', 3] }),
				{ existingChat: false }
			)
		).toBeNull();
		expect(
			getConversationModeDraftCapabilitySnapshot(completeDraft({ selectedTerminalId: undefined }), {
				existingChat: false
			})
		).toBeNull();
	});

	it('revalidates a hydrated authoritative snapshot through model-change rules', () => {
		const snapshot = getConversationModeDraftCapabilitySnapshot(
			completeDraft({
				selectedToolIds: ['profile-tool', 'missing-tool'],
				selectedTerminalId: 'missing-terminal',
				codeInterpreterEnabled: true
			}),
			{ existingChat: true }
		);
		expect(snapshot).not.toBeNull();

		expect(
			resolve({
				phase: 'model_change',
				currentSelections: snapshot!.selections,
				available
			})
		).toMatchObject({
			effective: {
				terminalId: null,
				toolIds: ['profile-tool'],
				skillIds: ['profile-skill'],
				filterIds: ['profile-filter'],
				featureIds: ['web_search', 'code_interpreter']
			},
			warnings: expect.arrayContaining([
				{ field: 'terminal_id', resourceIds: ['missing-terminal'] },
				{ field: 'tool_ids', resourceIds: ['missing-tool'] }
			])
		});
	});

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

	it('rejects a stale inherit-bound marker when restoring a new-chat draft', () => {
		expect(
			createConversationModeCapabilityAuthorityController({
				existingChat: false,
				persistedAuthority: 'inherit_bound'
			}).snapshot()
		).toBe('initialized');
	});

	it('ignores prompt, file, and reasoning-only changes while preserving inherit-bound authority', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: true });
		const initialInput = {
			...emptyCapabilities,
			prompt: '',
			files: [],
			reasoningEffort: 'medium'
		};
		const changedInput = {
			...emptyCapabilities,
			prompt: 'typed text',
			files: [{ id: 'file-1' }],
			reasoningEffort: 'high'
		};
		controller.observe(initialInput);

		expect(controller.observe(changedInput)).toBe('inherit_bound');
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

	it('rebases resolver-owned changes without treating them as user overrides', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: true });
		controller.observe(emptyCapabilities);

		expect(controller).toHaveProperty('rebase');
		expect(
			(controller as any).rebase({ ...emptyCapabilities, selectedToolIds: ['resolved-tool'] })
		).toBe('inherit_bound');
		expect(controller.observe({ ...emptyCapabilities, selectedToolIds: ['resolved-tool'] })).toBe(
			'inherit_bound'
		);
		expect(controller.observe({ ...emptyCapabilities, selectedToolIds: ['user-tool'] })).toBe(
			'explicit'
		);
	});

	it('reports real user changes so a legacy partial override can promote to a full explicit snapshot', () => {
		const controller = createConversationModeCapabilityAuthorityController({ existingChat: true });
		const withDetails = (controller as any).observeWithChange;

		expect(withDetails).toBeTypeOf('function');
		expect(withDetails(emptyCapabilities)).toEqual({ authority: 'inherit_bound', changed: false });
		expect(withDetails({ ...emptyCapabilities, selectedToolIds: ['user-tool'] })).toEqual({
			authority: 'explicit',
			changed: true
		});
	});

	it('serializes only legacy-positive fields so an existing bound revision keeps every other default', () => {
		expect(
			serializeConversationModeCapabilityRequest({
				authority: 'explicit',
				overrideFields: ['tool_ids', 'web_search'],
				selections: {
					terminalId: null,
					toolIds: ['legacy-tool'],
					skillIds: [],
					filterIds: []
				},
				features: {
					voice: true,
					memory: true,
					web_search: true,
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
				tool_ids: ['legacy-tool'],
				features: { voice: true, memory: true, web_search: true }
			},
			toolServerIds: [],
			emitToolServers: true
		});
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

		expect(
			serializeConversationModeCapabilityRequest({
				authority: 'explicit',
				selections: {
					terminalId: 'https://deleted-direct-terminal.example',
					toolIds: [],
					skillIds: [],
					filterIds: []
				},
				features: {},
				directToolServersPermitted: false,
				directTerminalIds: [],
				functionCallingEnabled: true,
				terminalEnabled: true
			}).request.terminal_id
		).toBeNull();
	});

	it('suppresses direct server emission when function calling is unsupported', () => {
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
			toolServerIds: [],
			emitToolServers: false
		});
	});

	it('omits unsupported tool_servers, represents explicit clears, and selects one direct terminal', () => {
		const toolServers = [
			{ id: 'server-a', name: 'A' },
			{ id: 'server-b', name: 'B' }
		];
		const terminalServers = [
			{ url: 'https://terminal-a.example', name: 'Terminal A' },
			{ url: 'https://terminal-b.example', name: 'Terminal B' },
			{ id: 'system-terminal', name: 'System Terminal' }
		];

		expect(
			serializeConversationModeToolServers({
				emitToolServers: false,
				toolServerIds: ['server-a'],
				terminalId: 'https://terminal-a.example',
				directToolServersPermitted: true,
				toolServers,
				terminalServers
			})
		).toEqual({});
		expect(
			serializeConversationModeToolServers({
				emitToolServers: true,
				toolServerIds: [],
				terminalId: null,
				directToolServersPermitted: true,
				toolServers,
				terminalServers
			})
		).toEqual({ tool_servers: [] });
		expect(
			serializeConversationModeToolServers({
				emitToolServers: true,
				toolServerIds: ['server-b'],
				terminalId: 'https://terminal-b.example',
				directToolServersPermitted: true,
				toolServers,
				terminalServers
			})
		).toEqual({
			tool_servers: [toolServers[1], terminalServers[1]]
		});
		expect(
			serializeConversationModeToolServers({
				emitToolServers: true,
				toolServerIds: [],
				terminalId: null,
				directToolServersPermitted: true,
				toolServers,
				terminalServers
			})
		).toEqual({ tool_servers: [] });
	});

	it('retains only permitted live direct tool IDs across model changes', () => {
		const toolServers = [
			{ info: { title: 'Direct A' } },
			{ url: 'https://missing-info.example' },
			{ info: { title: 'Direct C' } }
		];
		const permittedToolIds = getConversationModeAvailableToolIds({
			tools: [{ id: 'tool-a' }, { id: 'tool-denied', authenticated: false }],
			toolServers,
			directToolServersPermitted: true
		});

		expect(permittedToolIds).toEqual([
			'tool-a',
			'tool-denied',
			'direct_server:0',
			'direct_server:2'
		]);
		expect(
			resolve({
				phase: 'model_change',
				currentSelections: {
					terminalId: null,
					toolIds: ['direct_server:2'],
					skillIds: [],
					filterIds: [],
					featureIds: []
				},
				available: { ...available, toolIds: permittedToolIds }
			}).effective.toolIds
		).toEqual(['direct_server:2']);

		for (const toolIds of [
			getConversationModeAvailableToolIds({
				tools: [],
				toolServers,
				directToolServersPermitted: false
			}),
			getConversationModeAvailableToolIds({
				tools: [],
				toolServers: [{ info: { title: 'Only current server' } }],
				directToolServersPermitted: true
			})
		]) {
			expect(
				resolve({
					phase: 'model_change',
					currentSelections: {
						terminalId: null,
						toolIds: ['direct_server:2'],
						skillIds: [],
						filterIds: [],
						featureIds: []
					},
					available: { ...available, toolIds }
				}).effective.toolIds
			).toEqual([]);
		}
	});

	it('defers destructive direct-tool filtering to configured indices until the live catalog is ready', () => {
		expect(
			getConversationModeAvailableToolIds({
				tools: [],
				toolServers: [],
				configuredToolServers: [
					{ config: { enable: true } },
					{ config: { enable: false } },
					{ config: { enable: true } }
				],
				directToolServerCatalogReady: false,
				directToolServersPermitted: true
			})
		).toEqual(['direct_server:0', 'direct_server:2']);

		expect(
			getConversationModeAvailableToolIds({
				tools: [],
				toolServers: [],
				configuredToolServers: [{ config: { enable: true } }],
				directToolServerCatalogReady: false,
				directToolServersPermitted: false
			})
		).toEqual([]);

		expect(
			getConversationModeAvailableToolIds({
				tools: [],
				toolServers: [{ info: { title: 'Live server' } }],
				configuredToolServers: [{ config: { enable: true } }, { config: { enable: true } }],
				directToolServerCatalogReady: true,
				directToolServersPermitted: true
			})
		).toEqual(['direct_server:0']);
	});

	it('uses a stable external-catalog fingerprint across object-key and candidate ordering', () => {
		type FingerprintInput = {
			userId: unknown;
			directToolServersPermitted: boolean;
			configuredToolServers: readonly unknown[];
			configuredTerminalServers: readonly unknown[];
			terminalCandidateIds: readonly string[];
		};
		const fingerprint = requiredHelper<(input: FingerprintInput) => string>(
			'getConversationModeExternalCatalogFingerprint'
		);
		const first = fingerprint({
			userId: 'user-a',
			directToolServersPermitted: true,
			configuredToolServers: [
				{ url: 'https://tool.example', key: 'secret', config: { enable: true } }
			],
			configuredTerminalServers: [
				{ url: 'https://terminal.example', auth_type: 'bearer', enabled: true }
			],
			terminalCandidateIds: ['terminal-b', 'terminal-a']
		});
		const same = fingerprint({
			terminalCandidateIds: ['terminal-a', 'terminal-b'],
			configuredTerminalServers: [
				{ enabled: true, auth_type: 'bearer', url: 'https://terminal.example' }
			],
			configuredToolServers: [
				{ config: { enable: true }, key: 'secret', url: 'https://tool.example' }
			],
			directToolServersPermitted: true,
			userId: 'user-a'
		});

		expect(same).toBe(first);
		expect(
			fingerprint({
				userId: 'user-a',
				directToolServersPermitted: false,
				configuredToolServers: [],
				configuredTerminalServers: [],
				terminalCandidateIds: ['terminal-a', 'terminal-b']
			})
		).not.toBe(first);
	});

	it('tracks idle/loading/ready/error per catalog fingerprint and retains the last success', () => {
		type Catalog = {
			toolServers: Array<Record<string, unknown>>;
			terminalServers: Array<Record<string, unknown>>;
		};
		type CatalogState = {
			status: 'idle' | 'loading' | 'ready' | 'error';
			fingerprint: string;
			catalog: Catalog | null;
			error: string | null;
		};
		type CatalogCache = {
			snapshot: (fingerprint: string) => CatalogState;
			begin: (fingerprint: string, options?: { force?: boolean; now?: number }) => boolean;
			succeed: (fingerprint: string, catalog: Catalog, options?: { now?: number }) => CatalogState;
			fail: (fingerprint: string, error: unknown) => CatalogState;
			shouldRefresh: (
				fingerprint: string,
				options: { maxAgeMs: number; retryAfterMs: number; now?: number }
			) => boolean;
		};
		const createCache = requiredHelper<() => CatalogCache>(
			'createConversationModeExternalCatalogCache'
		);
		const cache = createCache();
		const successfulCatalog = {
			toolServers: [{ id: 'tool-server-a' }],
			terminalServers: [{ id: 'terminal-a' }]
		};

		expect(cache.snapshot('config-a')).toEqual({
			status: 'idle',
			fingerprint: 'config-a',
			catalog: null,
			error: null
		});
		expect(cache.begin('config-a')).toBe(true);
		expect(cache.snapshot('config-a').status).toBe('loading');
		expect(cache.begin('config-a')).toBe(false);
		expect(cache.succeed('config-a', successfulCatalog)).toMatchObject({
			status: 'ready',
			catalog: successfulCatalog,
			error: null
		});
		expect(cache.begin('config-a')).toBe(false);
		expect(cache.begin('config-a', { force: true })).toBe(true);
		expect(cache.snapshot('config-a')).toMatchObject({
			status: 'loading',
			catalog: successfulCatalog
		});
		expect(cache.fail('config-a', new Error('discovery failed'))).toMatchObject({
			status: 'error',
			catalog: successfulCatalog,
			error: 'discovery failed'
		});

		expect(cache.begin('config-b')).toBe(true);
		expect(cache.fail('config-b', 'timeout')).toEqual({
			status: 'error',
			fingerprint: 'config-b',
			catalog: null,
			error: 'timeout'
		});

		expect(cache.begin('drift', { now: 100 })).toBe(true);
		cache.succeed('drift', successfulCatalog, { now: 100 });
		expect(cache.shouldRefresh('drift', { maxAgeMs: 60, retryAfterMs: 10, now: 159 })).toBe(false);
		expect(cache.shouldRefresh('drift', { maxAgeMs: 60, retryAfterMs: 10, now: 160 })).toBe(true);
		expect(cache.begin('drift', { force: true, now: 160 })).toBe(true);
		cache.fail('drift', 'remote drift probe failed');
		expect(cache.shouldRefresh('drift', { maxAgeMs: 60, retryAfterMs: 10, now: 169 })).toBe(false);
		expect(cache.shouldRefresh('drift', { maxAgeMs: 60, retryAfterMs: 10, now: 170 })).toBe(true);
	});

	it('captures an immutable mode-profile request context including feature UI state', () => {
		type RequestContext = {
			mode: 'chat' | 'agent';
			revisionHint: string | null;
			authority: 'initialized' | 'inherit_bound' | 'explicit';
			profile: Record<string, unknown> | null;
			model: Record<string, unknown>;
			selections: {
				terminalId: string | null;
				toolIds: string[];
				skillIds: string[];
				filterIds: string[];
				featureIds: string[];
			};
			featureState: {
				availableFeatureIds: string[];
				voice: boolean;
				memory: boolean;
				webSearchAlways: boolean;
				imageGenerationUserOverride: boolean | null;
				imageGenerationGloballyEnabled: boolean;
				imageGenerationAllowed: boolean;
			};
			directToolServersPermitted: boolean;
			directTerminalIds: string[];
		};
		const capture = requiredHelper<(input: RequestContext) => RequestContext>(
			'captureConversationModeRequestContext'
		);
		const input: RequestContext = {
			mode: 'agent',
			revisionHint: 'revision-a',
			authority: 'explicit',
			profile: {
				mode: 'agent',
				current_revision_id: 'profile-revision',
				schema_version: 1,
				defaults: { tool_ids: ['profile-tool'] }
			},
			model: {
				id: 'model-a',
				info: { meta: { capabilities: { function_calling: true } } }
			},
			selections: {
				terminalId: 'terminal-a',
				toolIds: ['tool-a'],
				skillIds: ['skill-a'],
				filterIds: ['filter-a'],
				featureIds: ['web_search']
			},
			featureState: {
				availableFeatureIds: ['web_search', 'image_generation'],
				voice: true,
				memory: true,
				webSearchAlways: false,
				imageGenerationUserOverride: null,
				imageGenerationGloballyEnabled: true,
				imageGenerationAllowed: true
			},
			directToolServersPermitted: true,
			directTerminalIds: ['terminal-a']
		};
		const captured = capture(input);

		input.mode = 'chat';
		input.revisionHint = 'revision-b';
		input.selections.toolIds.push('tool-b');
		input.featureState.availableFeatureIds.length = 0;
		(input.profile?.defaults as { tool_ids: string[] }).tool_ids.push('profile-tool-b');
		(
			input.model.info as { meta: { capabilities: Record<string, boolean> } }
		).meta.capabilities.function_calling = false;

		expect(captured).toMatchObject({
			mode: 'agent',
			revisionHint: 'revision-a',
			selections: { toolIds: ['tool-a'] },
			featureState: { availableFeatureIds: ['web_search', 'image_generation'] },
			profile: { defaults: { tool_ids: ['profile-tool'] } },
			model: { info: { meta: { capabilities: { function_calling: true } } } }
		});
	});

	it('migrates only unambiguous positive legacy capabilities over initialized defaults', () => {
		type Migration = {
			authority: 'explicit';
			selections: {
				terminalId: string | null;
				toolIds: string[];
				skillIds: string[];
				filterIds: string[];
				featureIds: string[];
			};
		};
		const migrate = requiredHelper<
			(
				draft: ConversationModeDraft | null,
				initialized: Migration['selections']
			) => Migration | null
		>('migrateConversationModeLegacyDraftCapabilities');
		const initialized = {
			terminalId: 'default-terminal',
			toolIds: ['default-tool'],
			skillIds: ['default-skill'],
			filterIds: ['default-filter'],
			featureIds: ['image_generation']
		};

		expect(
			migrate(
				{
					prompt: 'legacy',
					files: [],
					selectedToolIds: ['legacy-tool'],
					selectedSkillIds: [],
					selectedFilterIds: ['legacy-filter'],
					webSearchEnabled: true,
					codeInterpreterEnabled: false,
					selectedTerminalId: 'legacy-terminal'
				},
				initialized
			)
		).toEqual({
			authority: 'explicit',
			overrideFields: ['terminal_id', 'tool_ids', 'filter_ids', 'web_search'],
			selections: {
				terminalId: 'legacy-terminal',
				toolIds: ['legacy-tool'],
				skillIds: ['default-skill'],
				filterIds: ['legacy-filter'],
				featureIds: ['image_generation', 'web_search']
			}
		});
		expect(
			migrate(
				{
					prompt: 'legacy content only',
					files: [{ id: 'ordinary-file' }],
					selectedToolIds: [],
					selectedSkillIds: ['valid-skill', 3],
					selectedFilterIds: [],
					webSearchEnabled: false,
					codeInterpreterEnabled: false,
					imageGenerationEnabled: false,
					selectedTerminalId: null
				},
				initialized
			)
		).toBeNull();
	});

	it('keeps unauthenticated OAuth tools available while partitioning them out of requests', () => {
		type Tool = { id?: unknown; name?: unknown; authenticated?: unknown };
		type Partition = {
			selectedToolIds: string[];
			pendingOAuthTools: Array<{
				id: string;
				name: string;
				serverId: string;
				authType: string | null;
			}>;
		};
		const partition = requiredHelper<
			(selectedToolIds: unknown, tools: readonly Tool[]) => Partition
		>('partitionConversationModeOAuthTools');
		const tools = [
			{ id: 'tool-a', name: 'Tool A', authenticated: true },
			{ id: 'server:oauth:needs-auth', name: 'OAuth Tool', authenticated: false }
		];

		expect(
			getConversationModeAvailableToolIds({
				tools,
				toolServers: [],
				directToolServersPermitted: false
			})
		).toEqual(['tool-a', 'server:oauth:needs-auth']);
		expect(
			partition(['tool-a', 'server:oauth:needs-auth', 'direct_server:0', 'missing-tool'], tools)
		).toEqual({
			selectedToolIds: ['tool-a', 'direct_server:0', 'missing-tool'],
			pendingOAuthTools: [
				{
					id: 'server:oauth:needs-auth',
					name: 'OAuth Tool',
					serverId: 'needs-auth',
					authType: 'oauth'
				}
			]
		});
	});
});
