import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import {
	buildPipeReasoningPayload,
	chatUploadMetadata,
	getPipeReasoningEfforts,
	getRetainedPipeId
} from './pipeReasoning';

describe('retained Pipe reasoning controls', () => {
	it('leaves native models, removed Pipes and unknown models untouched', () => {
		for (const id of ['gpt-test', 'anthropic_pipe.test', 'agentscope_alias.test', 'other.test']) {
			const model = { id, info: { meta: { capabilities: { reasoning_effort: ['high'] } } } };
			expect(getPipeReasoningEfforts(model)).toEqual([]);
			expect(buildPipeReasoningPayload(model, 'high')).toEqual({});
		}
	});
	it('preserves Bifrost object semantics and explicit capability restrictions', () => {
		expect(buildPipeReasoningPayload({ id: 'bifrostapi.test' }, 'xhigh')).toEqual({
			reasoning: { enabled: true, effort: 'xhigh' }
		});
		const restricted = {
			id: 'bifrostapi.test',
			info: { meta: { capabilities: { reasoning_effort: ['low', 'high', 'high', 'invalid'] } } }
		};
		expect(getPipeReasoningEfforts(restricted)).toEqual(['low', 'high']);
		expect(buildPipeReasoningPayload(restricted, 'xhigh')).toEqual({});
		expect(buildPipeReasoningPayload(restricted, '')).toEqual({});
	});
	it('requires an explicit Sub2API contract and uses reasoning_effort, not Bifrost reasoning', () => {
		expect(getPipeReasoningEfforts({ id: 'sub2api.test' })).toEqual([]);
		const supported = {
			id: 'custom-alias',
			info: {
				base_model_id: 'sub2api.test',
				meta: { capabilities: { reasoning_effort: ['medium', 'high'] } }
			}
		};
		expect(getRetainedPipeId(supported)).toBe('sub2api');
		expect(buildPipeReasoningPayload(supported, 'high')).toEqual({ reasoning_effort: 'high' });
	});
	it('does not carry preferences across model IDs in a multi-model request', () => {
		const preferences: Record<string, string> = { 'bifrostapi.a': 'low', 'bifrostapi.b': 'high' };
		const requests = Object.keys(preferences).map((id) => ({
			model: id,
			...buildPipeReasoningPayload({ id }, preferences[id])
		}));
		expect(requests.map((body) => body.reasoning?.effort)).toEqual(['low', 'high']);
	});
	it('sends chat upload scope, exact selected IDs and STT language', () => {
		expect(chatUploadMetadata(['', 'model-a', 'model-a', 'model-b'], 'zh')).toEqual({
			upload_context: 'chat',
			model_ids: ['model-a', 'model-b'],
			language: 'zh'
		});
		expect(chatUploadMetadata([])).toEqual({ upload_context: 'chat', model_ids: [] });
	});
	it('compiles the new control and retains official ModelControls alongside it', () => {
		const component = readFileSync(
			new URL('../components/chat/MessageInput/PipeReasoningControls.svelte', import.meta.url),
			'utf8'
		);
		expect(() =>
			compile(component, { filename: 'PipeReasoningControls.svelte', generate: 'client' })
		).not.toThrow();
		const input = readFileSync(
			new URL('../components/chat/MessageInput.svelte', import.meta.url),
			'utf8'
		);
		expect(input).toContain('<ModelControls selectedModels={selectedModelIds} />');
		expect(input).toContain('<PipeReasoningControls selectedModels={selectedModelIds} />');
		const chat = readFileSync(new URL('../components/chat/Chat.svelte', import.meta.url), 'utf8');
		expect(chat).toContain(
			'...buildPipeReasoningPayload(model, $settings?.pipeReasoningEfforts?.[model.id])'
		);
	});
});
