import { describe, expect, it } from 'vitest';

import {
	isAgentModeRequestConstraintEnabled,
	resolveAgentModeRequestModels
} from './agentModeRequest';

describe('resolveAgentModeRequestModels', () => {
	it('keeps multi-model compare unchanged when Agent Mode is not visible to the frontend', () => {
		expect(resolveAgentModeRequestModels(['model-a', 'model-b'], undefined)).toEqual([
			'model-a',
			'model-b'
		]);
		expect(
			resolveAgentModeRequestModels(['model-a', 'model-b'], {
				features: { enable_agent_mode: false }
			})
		).toEqual(['model-a', 'model-b']);
	});

	it('uses the first valid selected model as the Agent Mode leader', () => {
		expect(
			resolveAgentModeRequestModels(['', 'model-a', 'model-b'], {
				features: { enable_agent_mode: true }
			})
		).toEqual(['model-a']);
	});
});

describe('isAgentModeRequestConstraintEnabled', () => {
	it('requires an explicit frontend-visible Agent Mode flag', () => {
		expect(isAgentModeRequestConstraintEnabled(undefined)).toBe(false);
		expect(isAgentModeRequestConstraintEnabled({ features: { enable_agent_memory: true } })).toBe(
			false
		);
		expect(isAgentModeRequestConstraintEnabled({ features: { enable_agent_mode: true } })).toBe(
			true
		);
	});
});
