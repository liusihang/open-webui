import { describe, expect, it } from 'vitest';

import {
	isAgentModeRequestConstraintEnabled,
	resolveAgentModeRequestModels
} from './agentModeRequest';

describe('resolveAgentModeRequestModels', () => {
	it('uses a single selected model when Agent Mode is not visible to the frontend', () => {
		expect(resolveAgentModeRequestModels(['model-a', 'model-b'], undefined)).toEqual(['model-a']);
		expect(
			resolveAgentModeRequestModels(['model-a', 'model-b'], {
				features: { enable_agent_mode: false }
			})
		).toEqual(['model-a']);
	});

	it('uses the first valid selected model as the Agent Mode leader', () => {
		expect(
			resolveAgentModeRequestModels(['', 'model-a', 'model-b'], {
				features: { enable_agent_mode: true }
			})
		).toEqual(['model-a']);
	});

	it('collapses multiple empty model placeholders to one empty selection', () => {
		expect(resolveAgentModeRequestModels(['', ''], undefined)).toEqual(['']);
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
