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

describe('terminal file display conversion', () => {
	const file = {
		type: 'file',
		source: 'open_terminal',
		exists: true,
		path: '/home/user/report.docx',
		terminal_selector: 'terminals',
		session_id: 'source-session'
	};
	const output = (result: unknown, args: Record<string, unknown> = {}): OutputItem[] => [
		{
			type: 'function_call',
			name: 'display_file',
			call_id: 'file-call',
			arguments: JSON.stringify({ path: file.path, ...args })
		},
		{
			type: 'function_call_output',
			call_id: 'file-call',
			status: 'completed',
			output: [{ type: 'input_text', text: JSON.stringify(result) }]
		}
	];
	it.each([{}, { inline: false }, { inline: true }])(
		'keeps old and new file results visible without a mode requirement: %o',
		(args) => {
			const files = buildOutputDisplayItems(output(file, args)).filter(
				(item) => item.type === 'file'
			);
			expect(files).toHaveLength(1);
			expect(files[0]).toMatchObject({ item: file });
		}
	);
	it('supports authorized uploaded-file results without a terminal selector', () => {
		const uploaded = {
			type: 'file',
			source: 'open_webui',
			exists: true,
			displayed: true,
			path: '/api/v1/files/file-id/content',
			name: 'report.pdf'
		};
		expect(
			buildOutputDisplayItems(output(uploaded)).filter((item) => item.type === 'file')
		).toEqual([expect.objectContaining({ item: uploaded })]);
		for (const result of [
			{ ...uploaded, exists: false },
			{ ...uploaded, exists: undefined }
		]) {
			expect(
				buildOutputDisplayItems(output(result)).filter((item) => item.type === 'file')
			).toEqual([]);
		}
	});

	it('retains the requested document page and handles nested JSON results', () => {
		const files = buildOutputDisplayItems(output(JSON.stringify(file), { page: 3 })).filter(
			(item) => item.type === 'file'
		);
		expect(files[0]).toMatchObject({ item: { ...file, page: 3 } });
	});
	it.each([
		{ ...file, exists: false },
		{ ...file, exists: undefined },
		{ error: 'File not found' }
	])('does not promote invalid results: %o', (result) => {
		expect(
			buildOutputDisplayItems(output(result)).filter((item) => item.type === 'file')
		).toHaveLength(0);
	});
	it.each(['failed', 'incomplete'])('does not promote a %s result', (status) => {
		const items = output(file);
		items[1].status = status;
		expect(buildOutputDisplayItems(items).filter((item) => item.type === 'file')).toHaveLength(0);
	});
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

describe('upstream display items alongside commentary', () => {
	it('keeps generated images and context compaction in output order', () => {
		const output = [
			message('progress', 'Making an image.', 'commentary'),
			{ id: 'image', type: 'image_generation_call', result: 'aW1hZ2U=', output_format: 'webp' },
			{ id: 'compact', type: 'open_webui:context_compaction', summary: 'Earlier context' },
			message('answer', 'Done.', 'final_answer')
		];
		expect(buildOutputDisplayItems(output)).toEqual([
			{ type: 'commentary', id: 'progress', text: 'Making an image.' },
			{ type: 'image', id: 'image', src: 'data:image/webp;base64,aW1hZ2U=' },
			{ type: 'context_compaction', id: 'compact', summary: 'Earlier context' },
			{ type: 'message', id: 'answer', text: 'Done.' }
		]);
	});
});
