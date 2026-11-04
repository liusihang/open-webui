import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { buildPipeReasoningPayload } from '../../src/lib/utils/pipeReasoning';

// Exercise the request expression at SideChat's actual API boundary, without mounting its chrome.
function sideRequest(model: any, effort: string) {
	const source = readFileSync('src/lib/components/chat/ChatTabs/SideChat.svelte', 'utf8');
	const tree = ts.createSourceFile(
		'side.ts',
		source.split('<script lang="ts">')[1].split('</script>')[0],
		ts.ScriptTarget.Latest,
		true
	);
	let request: ts.CallExpression | undefined;
	function visit(node: ts.Node) {
		if (
			ts.isCallExpression(node) &&
			node.expression.getText(tree) === 'generateOpenAIChatCompletion'
		)
			request = node;
		ts.forEachChild(node, visit);
	}
	visit(tree);
	if (!request) throw new Error('SideChat completion request missing');
	const code = ts.transpileModule(`return ${request.getText(tree)}`, {
		compilerOptions: { target: ts.ScriptTarget.ES2022 }
	}).outputText;
	const generateOpenAIChatCompletion = vi.fn((token, body) => body);
	return new Function(
		'generateOpenAIChatCompletion',
		'buildPipeReasoningPayload',
		'model',
		'effort',
		`
		const localStorage = {token: 'fixture'}, sideModelId = model.id, $models = [model];
		const $settings = {pipeReasoningEfforts: {[model.id]: effort}}, $socket = {id: 'socket'};
		const id = 'temporary:side', answer = {id: 'answer'}, userMessage = {id: 'question'};
		const params = {temperature: 0.5}, toolApprovalMode = 'ask', requestFiles = [{id: 'file'}];
		const selectedToolIds = ['lookup'], selectedSkillIds = ['skill'], selectedFilterIds = ['filter'];
		const $config = {features: {enable_tool_servers: true}}, $selectedTerminalId = 'terminal';
		const $toolServers = [], $terminalServers = [], directServers = [];
		const webSearchEnabled = true, imageGenerationEnabled = false, codeInterpreterEnabled = false;
		const system = '', conversation = [{role: 'user', content: 'side question'}];
		${code}
	`
	)(generateOpenAIChatCompletion, buildPipeReasoningPayload, model, effort);
}

describe('SideChat request compatibility', () => {
	it('sends Bifrost reasoning with the temporary side chat envelope', () => {
		expect(sideRequest({ id: 'bifrostapi.model' }, 'high')).toMatchObject({
			model: 'bifrostapi.model',
			reasoning: { enabled: true, effort: 'high' },
			chat_id: 'temporary:side',
			params: { temperature: 0.5, tool_approval_mode: 'ask' },
			files: [{ id: 'file' }],
			terminal_id: 'terminal',
			tool_ids: ['lookup']
		});
	});
	it('sends opted-in Sub2API effort and leaves native models to their own params', () => {
		expect(
			sideRequest(
				{ id: 'sub2api.model', info: { meta: { capabilities: { reasoning_effort: ['high'] } } } },
				'high'
			).reasoning_effort
		).toBe('high');
		const native = sideRequest({ id: 'native-model' }, 'high');
		expect(native).not.toHaveProperty('reasoning');
		expect(native).not.toHaveProperty('reasoning_effort');
	});
});
