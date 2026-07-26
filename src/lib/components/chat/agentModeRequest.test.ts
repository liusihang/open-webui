import { describe, expect, it } from 'vitest';

import {
	buildConversationModeReasoningPayload,
	isAgentModeCapabilityEnabled,
	normalizeConversationMode,
	resolveConversationModeRequestModels
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

	it('preserves multi-model selection for Chat when Agent capability is enabled', () => {
		expect(resolveConversationModeRequestModels(['model-a', 'model-b'], 'chat')).toEqual([
			'model-a',
			'model-b'
		]);
	});

	it('uses one non-empty leader model for Agent conversations', () => {
		expect(resolveConversationModeRequestModels(['', 'model-a', 'model-b'], 'agent')).toEqual([
			'model-a'
		]);
		expect(resolveConversationModeRequestModels([''], 'agent')).toEqual(['']);
	});

	it('preserves the existing reasoning payload in both conversation modes', () => {
		expect(buildConversationModeReasoningPayload('chat', 'deep')).toEqual({
			enabled: true,
			effort: 'high',
			max_tokens: 8126
		});
		expect(buildConversationModeReasoningPayload('agent', 'deep')).toEqual({
			enabled: true,
			effort: 'high',
			max_tokens: 8126
		});
	});

	it('normalizes unknown persisted values to Chat for safe rendering', () => {
		expect(normalizeConversationMode('agent')).toBe('agent');
		expect(normalizeConversationMode('chat')).toBe('chat');
		expect(normalizeConversationMode('work')).toBe('chat');
		expect(normalizeConversationMode(undefined)).toBe('chat');
	});
});
