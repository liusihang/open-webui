import { describe, expect, it } from 'vitest';
import {
	applyResponseStreamEvent,
	buildOutputDisplayItems,
	getOutputText,
	type OutputItem
} from './structuredOutput';

const message = (id: string, text: string, phase?: string): OutputItem => ({
	id,
	type: 'message',
	role: 'assistant',
	...(phase === undefined ? {} : { phase }),
	content: [{ type: 'output_text', text }]
});

describe('native commentary display conversion', () => {
	it('distinguishes commentary from final answers without changing raw output or copy text', () => {
		const output = [
			message('progress', 'Checking inputs.', 'commentary'),
			message('answer', 'Result.', 'final_answer')
		];
		const before = JSON.stringify(output);
		expect(buildOutputDisplayItems(output)).toEqual([
			{ type: 'commentary', id: 'progress', text: 'Checking inputs.' },
			{ type: 'message', id: 'answer', text: 'Result.' }
		]);
		expect(JSON.stringify(output)).toBe(before);
		expect(getOutputText(output)).toBe('Checking inputs.\nResult.');
	});

	it.each([undefined, 'final_answer', 'unknown', 'Commentary', ''])(
		'keeps phase %s as ordinary message text',
		(phase) => {
			expect(buildOutputDisplayItems([message('text', 'Body.', phase)])[0].type).toBe('message');
		}
	);

	it.each(['user', undefined])('does not infer assistant role from phase for role %s', (role) => {
		expect(
			buildOutputDisplayItems([{ ...message('text', 'Body.', 'commentary'), role }])[0].type
		).toBe('message');
	});

	it('does not reclassify reasoning even if it carries a commentary phase', () => {
		const [item] = buildOutputDisplayItems([
			{ type: 'reasoning', phase: 'commentary', summary: [{ text: 'Reasoning summary.' }] }
		]);
		expect(item.type).toBe('detail_single');
		if (item.type === 'detail_single') expect(item.token.attributes.type).toBe('reasoning');
	});

	it('preserves commentary, tool details, and answer positions without merging progress items', () => {
		const output = [
			message('first', 'First check.', 'commentary'),
			{ type: 'function_call', id: 'call', name: 'lookup', arguments: '{}' },
			{ type: 'function_call_output', call_id: 'call', output: [{ text: 'Found.' }] },
			message('second', 'Second check.', 'commentary'),
			message('final', 'Answer.', 'final_answer')
		];
		expect(buildOutputDisplayItems(output).map(({ type, id }) => [type, id])).toEqual([
			['commentary', 'first'],
			['detail_single', 'detail-1'],
			['commentary', 'second'],
			['message', 'final']
		]);
	});

	it('keeps a commentary-only completion visible to the renderer and omits empty text', () => {
		expect(
			buildOutputDisplayItems([
				message('progress', 'Still checking.', 'commentary'),
				message('empty', ' ', 'final_answer')
			])
		).toEqual([{ type: 'commentary', id: 'progress', text: 'Still checking.' }]);
	});

	it('preserves native phase through text deltas, item.done, and response completion', () => {
		let output = applyResponseStreamEvent([], {
			type: 'response.output_item.added',
			output_index: 0,
			item: message('progress', '', 'commentary')
		});
		output = applyResponseStreamEvent(output, {
			type: 'response.output_text.delta',
			item_id: 'progress',
			delta: 'Checking.'
		});
		expect(buildOutputDisplayItems(output)[0]).toMatchObject({
			type: 'commentary',
			text: 'Checking.'
		});
		output = applyResponseStreamEvent(output, {
			type: 'response.output_item.done',
			item: { ...message('progress', 'Checked.', 'commentary'), status: 'completed' }
		});
		output = applyResponseStreamEvent(output, {
			type: 'response.completed',
			response: {
				output: [
					message('progress', 'Checked.', 'commentary'),
					message('final', 'Answer.', 'final_answer')
				]
			}
		});
		expect(buildOutputDisplayItems(output).map((item) => item.type)).toEqual([
			'commentary',
			'message'
		]);
		expect(output.map((item) => item.phase)).toEqual(['commentary', 'final_answer']);
	});

	it('uses a late native phase from item.done without inventing a phase for early text', () => {
		let output = applyResponseStreamEvent([], {
			type: 'response.output_text.delta',
			item_id: 'late',
			output_index: 0,
			delta: 'Checking.'
		});
		expect(output[0].phase).toBeUndefined();
		expect(buildOutputDisplayItems(output)[0].type).toBe('message');
		output = applyResponseStreamEvent(output, {
			type: 'response.output_item.done',
			item: message('late', 'Checking.', 'commentary')
		});
		expect(buildOutputDisplayItems(output)[0].type).toBe('commentary');
	});

	it('honors an authoritative item.done phase replacement', () => {
		const output = applyResponseStreamEvent([message('revised', 'Checking.', 'commentary')], {
			type: 'response.output_item.done',
			item: message('revised', 'Answer.', 'final_answer')
		});
		expect(buildOutputDisplayItems(output)).toEqual([
			{ type: 'message', id: 'revised', text: 'Answer.' }
		]);
	});
});
