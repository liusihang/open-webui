import { describe, expect, it } from 'vitest';

import {
	buildConversationModeReasoningPayload,
	buildModelReasoningPayload,
	isAgentModeCapabilityEnabled,
	normalizeConversationMode,
	resolveConversationModeRequestModels,
	resolveModelReasoningEfforts
} from './agentModeRequest';

describe('conversation mode request shaping', () => {
	it('detects Agent capability without selecting Agent mode', () => {
		expect(
			isAgentModeCapabilityEnabled({
				features: { enable_agent_mode: true }
			})
		).toBe(true);
		expect(isAgentModeCapabilityEnabled({ enable_agent_mode: false })).toBe(false);
	});

	it('uses one non-empty model for every conversation mode', () => {
		expect(resolveConversationModeRequestModels(['', 'model-a', 'model-b'], 'chat')).toEqual([
			'model-a'
		]);
		expect(resolveConversationModeRequestModels(['', 'model-a', 'model-b'], 'agent')).toEqual([
			'model-a'
		]);
		expect(resolveConversationModeRequestModels([''], 'chat')).toEqual(['']);
		expect(resolveConversationModeRequestModels([''], 'agent')).toEqual(['']);
	});

	it.each(['low', 'medium', 'high', 'xhigh'] as const)(
		'emits an effort-only reasoning payload for %s',
		(effort) => {
			expect(buildConversationModeReasoningPayload('chat', effort)).toEqual({
				enabled: true,
				effort
			});
			expect(buildConversationModeReasoningPayload('agent', effort)).toEqual({
				enabled: true,
				effort
			});
		}
	);

	it('normalizes unknown reasoning effort to medium', () => {
		expect(buildConversationModeReasoningPayload('chat', 'unexpected' as never)).toEqual({
			enabled: true,
			effort: 'medium'
		});
	});

	it('resolves configurable effort values from model metadata or the Bifrost function family', () => {
		expect(resolveModelReasoningEfforts({ id: 'bifrostapi.Cliproxy/gpt-5.6' })).toEqual([
			'low',
			'medium',
			'high',
			'xhigh'
		]);
		expect(
			resolveModelReasoningEfforts({
				id: 'custom-model',
				info: {
					meta: {
						capabilities: {
							reasoning_effort: ['high', 'low', 'invalid']
						}
					}
				}
			})
		).toEqual(['high', 'low']);
		expect(resolveModelReasoningEfforts({ id: 'legacy-model' })).toEqual([]);
	});

	it('omits reasoning for unsupported models and clamps effort to the model allowance', () => {
		expect(
			buildModelReasoningPayload({ id: 'bifrostapi.Cliproxy/gpt-5.6' }, 'xhigh')
		).toEqual({
			enabled: true,
			effort: 'xhigh'
		});
		expect(
			buildModelReasoningPayload(
				{
					id: 'custom-model',
					info: {
						meta: { capabilities: { reasoning_effort: ['high', 'low'] } }
					}
				},
				'xhigh'
			)
		).toEqual({ enabled: true, effort: 'high' });
		expect(buildModelReasoningPayload({ id: 'legacy-model' }, 'high')).toBeUndefined();
	});

	it('normalizes unknown persisted values to Chat for safe rendering', () => {
		expect(normalizeConversationMode('agent')).toBe('agent');
		expect(normalizeConversationMode('chat')).toBe('chat');
		expect(normalizeConversationMode('work')).toBe('chat');
		expect(normalizeConversationMode(undefined)).toBe('chat');
	});
});
