import { describe, expect, it } from 'vitest';
import {
	INTELLIGENT_UI_LIMITS,
	intelligentUIHash,
	parseIntelligentUI,
	type IntelligentUIElement,
	type Spec
} from './index';

const explanation: IntelligentUIElement = {
	type: 'Explanation',
	props: { title: 'What changes?', content: 'A useful explanation.' },
	children: []
};
const stack = (children: string[]): IntelligentUIElement => ({
	type: 'Stack',
	props: { title: 'Overview' },
	children
});
const patch = (path: string, value: unknown, op = 'add') => JSON.stringify({ op, path, value });
const root = patch('/root', 'main');
const element = (id: string, value: unknown) => patch(`/elements/${id}`, value);
const valid = `${root}\n${element('main', explanation)}`;
const spec = (node: unknown) => JSON.stringify({ root: 'main', elements: { main: node } });
const rejected = (source: string, done = true) => {
	const result = parseIntelligentUI(source, done);
	expect(result.spec).toBeNull();
	expect(result.error).toEqual(expect.any(String));
	expect(result.pending).toBe(false);
	expect(result.fallback.length).toBeGreaterThan(0);
};

describe('intelligent-ui streaming protocol', () => {
	it('accepts complete JSONL without a final newline', () => {
		const result = parseIntelligentUI(valid, true);
		expect(result).toEqual({
			spec: { root: 'main', elements: { main: explanation } },
			error: null,
			pending: false,
			fallback: 'What changes?\nA useful explanation.'
		});
	});

	it('supports CRLF, blank lines, and whole-element replacement', () => {
		const updated = { ...explanation, props: { title: 'Changed', content: 'New text.' } };
		const source = `\r\n${root}\r\n\r\n${element('main', explanation)}\r\n${patch('/elements/main', updated, 'replace')}\r\n`;
		expect(parseIntelligentUI(source, true).spec?.elements.main).toEqual(updated);
	});

	it('buffers a partial final record while preserving the previous valid view', () => {
		const result = parseIntelligentUI(
			`${valid}\n{"op":"replace","path":"/elements/main","value":`,
			false
		);
		expect(result.spec?.elements.main).toEqual(explanation);
		expect(result.error).toBeNull();
		expect(result.pending).toBe(true);
	});

	it('accepts a complete last record during streaming, including every preceding prefix', () => {
		for (let index = 0; index <= valid.length; index++) {
			const result = parseIntelligentUI(valid.slice(0, index), false);
			expect(result.error, `prefix ${index}`).toBeNull();
			expect(result.pending).toBe(true);
			if (index === valid.length) expect(result.spec?.elements.main).toEqual(explanation);
		}
	});

	it('waits for an initially missing root and excludes unresolved children and orphan nodes', () => {
		expect(parseIntelligentUI(root, false)).toEqual({
			spec: null,
			error: null,
			pending: true,
			fallback: ''
		});
		const partial = [
			root,
			element('main', stack(['ready', 'later'])),
			element('ready', explanation),
			element('orphan', explanation)
		].join('\n');
		const result = parseIntelligentUI(partial, false);
		expect(Object.keys(result.spec!.elements)).toEqual(['main', 'ready']);
		expect(result.spec!.elements.main.children).toEqual(['ready']);
		const finished = parseIntelligentUI(`${partial}\n${element('later', explanation)}`, true);
		expect(finished.spec?.elements.main.children).toEqual(['ready', 'later']);
		expect(finished.spec?.elements.orphan).toBeUndefined();
	});

	it('can receive elements before their root and change the root safely', () => {
		const source = [
			element('main', explanation),
			element('other', stack(['main'])),
			root,
			patch('/root', 'other', 'replace')
		].join('\n');
		expect(parseIntelligentUI(source, true).spec?.root).toBe('other');
	});

	it('rejects an incomplete record once newline or stream completion makes it final', () => {
		rejected(`${valid}\n{"op":`, true);
		rejected(`${valid}\n{"op":\n`, false);
	});

	it.each([
		'{"op":!}',
		'{"op":"add",invalid',
		'{"op":"add",}',
		'{"op":"add","path":"/root","value":"main\\q"}',
		'{"op":"add","path":"/root","value":01}',
		'{"op":"add","path":"/root","value":truX}',
		'{"op":"add"} trailing'
	])('fails closed on malformed final JSON during streaming: %s', (tail) => {
		rejected(`${valid}\n${tail}`, false);
	});

	it.each(['"unterminated', '"\\u0', '1e', '1e-', '-'])(
		'buffers incomplete JSON values: %s',
		(tail) => {
			const result = parseIntelligentUI(
				`${valid}\n{"op":"add","path":"/root","value":${tail}`,
				false
			);
			expect(result.error).toBeNull();
			expect(result.spec?.elements.main).toEqual(explanation);
		}
	);

	it('rejects a finished missing root or child and an empty finished payload', () => {
		for (const source of [
			'',
			root,
			element('main', explanation),
			`${root}\n${element('main', stack(['missing']))}`
		])
			rejected(source);
		expect(parseIntelligentUI('', false).pending).toBe(true);
	});

	it('accepts a single surrounding fence and rejects wrong fences or trailing content', () => {
		expect(parseIntelligentUI(`\`\`\`intelligent-ui\n${valid}\n\`\`\``, true).spec).not.toBeNull();
		expect(parseIntelligentUI(`\`\`\`intelligent-ui\n${valid}`, false).spec).not.toBeNull();
		rejected(`\`\`\`intelligent-ui\n${valid}`);
		rejected(`\`\`\`json\n${valid}\n\`\`\``);
		rejected(`\`\`\`intelligent-ui\n${valid}\n\`\`\`\nUnexpected`);
	});

	it('accepts compact and pretty full specs without mixing patch dialects', () => {
		const source: Spec = { root: 'main', elements: { main: explanation } };
		for (const space of [undefined, 2])
			expect(parseIntelligentUI(JSON.stringify(source, null, space), true).spec).toEqual(source);
		expect(parseIntelligentUI('{\n  "root": "main",\n  "elements": {', false).error).toBeNull();
		rejected(`${JSON.stringify(source)}\n${root}`);
	});

	it('accepts every-character prefixes of both pretty specs and surrounding fences', () => {
		const pretty = JSON.stringify({ root: 'main', elements: { main: explanation } }, null, 2);
		for (const source of [pretty, `\`\`\`intelligent-ui\n${valid}\n\`\`\``]) {
			for (let index = 0; index <= source.length; index++) {
				expect(
					parseIntelligentUI(source.slice(0, index), false).error,
					`prefix ${index}: ${source.slice(0, index)}`
				).toBeNull();
			}
		}
	});

	it('does not mutate earlier snapshots when the accumulated source changes', () => {
		const first = parseIntelligentUI(valid, false);
		const second = parseIntelligentUI(
			`${valid}\n${patch('/elements/main', { ...explanation, props: { title: 'New', content: 'New' } }, 'replace')}`,
			true
		);
		expect(first.spec?.elements.main.props.title).toBe('What changes?');
		expect(second.spec?.elements.main.props.title).toBe('New');
	});
});

describe('intelligent-ui allowlist and bounds', () => {
	it('accepts all five data-only components and creates a plain-text fallback', () => {
		const nodes: Spec['elements'] = {
			main: stack(['chart', 'comparison', 'explanation', 'slider']),
			chart: {
				type: 'Chart',
				props: {
					title: 'Costs',
					description: 'Monthly costs',
					kind: 'bar',
					data: [
						{ label: 'A', value: 20 },
						{ label: 'B', value: -5 }
					],
					unit: 'USD'
				},
				children: []
			},
			comparison: {
				type: 'ComparisonCards',
				props: {
					title: 'Options',
					items: [
						{
							title: 'A',
							description: 'Small option',
							attributes: [{ label: 'Size', value: 'Small' }]
						}
					]
				},
				children: []
			},
			explanation,
			slider: {
				type: 'ParameterSlider',
				props: {
					title: 'Budget',
					min: 0,
					max: 100,
					step: 0.5,
					value: 20,
					unit: 'USD',
					followUp: 'Recalculate using this budget.'
				},
				children: []
			}
		};
		const result = parseIntelligentUI(JSON.stringify({ root: 'main', elements: nodes }), true);
		expect(result.spec?.elements).toEqual(nodes);
		expect(result.fallback).toContain('A: 20 USD');
		expect(result.fallback).toContain('Size: Small');
		expect(result.fallback).toContain('20 USD (0–100)');
	});

	it.each(['remove', 'move', 'copy', 'test'])('rejects patch operation %s', (operation) => {
		rejected(`${valid}\n${patch('/root', 'main', operation)}`, false);
	});

	it.each([
		'/elements',
		'/elements/main/props',
		'/state/a',
		'/',
		'/root/child',
		'/elements/../main',
		'/elements/a~1b',
		'/elements/a.b',
		'/elements/__proto__',
		'/elements/constructor',
		'/elements/prototype'
	])('rejects patch path %s', (path) => {
		rejected(patch(path, explanation));
	});

	it('rejects extra patch, spec, component, prop, and nested item keys', () => {
		rejected(JSON.stringify({ op: 'add', path: '/root', value: 'main', extra: true }));
		rejected(JSON.stringify({ root: 'main', elements: { main: explanation }, state: {} }));
		rejected(spec({ ...explanation, visible: true }));
		rejected(spec({ ...explanation, props: { ...explanation.props, onClick: 'alert(1)' } }));
		rejected(
			spec({
				type: 'Chart',
				props: { title: 'A', kind: 'bar', data: [{ label: 'A', value: 1, action: {} }] },
				children: []
			})
		);
	});

	it('rejects duplicate object keys, including escaped duplicates and hidden unsafe values', () => {
		rejected('{"root":"main","root":"main","elements":{}}');
		rejected('{"root":"main","r\\u006fot":"main","elements":{}}');
		rejected('{"op":"remove","op":"add","path":"/root","value":"main"}', false);
		rejected('{"root":"main","elements":{"__proto__":{}},"elements":{}}');
	});

	it('requires letter-first bounded identifiers', () => {
		for (const id of ['0main', '_main', '-main', 'a'.repeat(65)]) {
			rejected(patch('/root', id));
			rejected(element(id, explanation));
		}
		const id = `a${'_b-9'.repeat(15)}`;
		expect(
			parseIntelligentUI(`${patch('/root', id)}\n${element(id, explanation)}`, true).error
		).toBeNull();
	});

	it.each(['Button', 'Image', 'Link', 'HTML', 'Script', 'iframe', 'Unknown'])(
		'rejects unregistered component %s',
		(type) => {
			rejected(spec({ type, props: {}, children: [] }));
		}
	);

	it.each(['__proto__', 'constructor', 'prototype', '$state', '$bind', '$cond', '$action'])(
		'rejects unsafe or dynamic field %s recursively',
		(key) => {
			rejected(
				`{"root":"main","elements":{"main":{"type":"Explanation","props":{"title":"T","content":"C","${key}":{}},"children":[]}}}`
			);
		}
	);

	it('rejects escaped prototype keys, reserved identifiers, and nested dynamic values', () => {
		rejected(
			'{"root":"main","elements":{"main":{"type":"Stack","props":{},"children":[]},"__pr\\u006fto__":{}}}'
		);
		rejected(patch('/root', '__proto__'));
		rejected(spec({ ...explanation, props: { title: 'T', content: { $state: '/data' } } }));
		expect(({} as Record<string, unknown>).polluted).toBeUndefined();
	});

	it('requires props and children and allows child arrays only on Stack', () => {
		rejected(spec({ type: 'Stack', children: [] }));
		rejected(spec({ type: 'Stack', props: {} }));
		rejected(spec({ ...explanation, children: ['main'] }));
		rejected(spec({ ...explanation, children: 'main' }));
		rejected(spec(stack(['child', 'child'])));
	});

	it('rejects cycles, including an unreachable cycle', () => {
		rejected(spec(stack(['main'])));
		rejected(
			JSON.stringify({
				root: 'main',
				elements: { main: explanation, a: stack(['b']), b: stack(['a']) }
			})
		);
	});

	it('limits tree depth to eight elements', () => {
		const elements: Spec['elements'] = {};
		for (let index = 0; index < 8; index++)
			elements[`n${index}`] = index === 7 ? explanation : stack([`n${index + 1}`]);
		expect(parseIntelligentUI(JSON.stringify({ root: 'n0', elements }), true).error).toBeNull();
		elements.n7 = stack(['n8']);
		elements.n8 = explanation;
		rejected(JSON.stringify({ root: 'n0', elements }));
	});

	it('limits both stored nodes and expanded DAG render visits', () => {
		const elements: Spec['elements'] = { main: explanation };
		for (let index = 0; index < 64; index++) elements[`n${index}`] = explanation;
		rejected(JSON.stringify({ root: 'main', elements }));
		const dag: Spec['elements'] = { main: stack(['a0', 'b0']) };
		for (let level = 0; level < 7; level++) {
			for (const prefix of ['a', 'b'])
				dag[`${prefix}${level}`] =
					level === 6 ? explanation : stack([`a${level + 1}`, `b${level + 1}`]);
		}
		rejected(JSON.stringify({ root: 'main', elements: dag }));
	});

	it('rejects oversized UTF-8 input, text, lists, and excessive patch operations', () => {
		rejected(' '.repeat(INTELLIGENT_UI_LIMITS.bytes + 1));
		rejected('你'.repeat(Math.ceil(INTELLIGENT_UI_LIMITS.bytes / 3)));
		rejected(spec({ ...explanation, props: { title: 'a'.repeat(161), content: 'x' } }));
		rejected(spec({ ...explanation, props: { title: 'x', content: 'a'.repeat(16 * 1024 + 1) } }));
		rejected(
			spec({
				type: 'Chart',
				props: {
					title: 'A',
					kind: 'bar',
					data: Array.from({ length: 129 }, () => ({ label: 'A', value: 1 }))
				},
				children: []
			})
		);
		rejected(
			spec({
				type: 'ComparisonCards',
				props: {
					title: 'A',
					items: Array.from({ length: 13 }, () => ({ title: 'A', description: 'B' }))
				},
				children: []
			})
		);
		rejected(`${valid}\n${Array(512).fill(root).join('\n')}`);
	});

	it.each([
		{ min: 0, max: 0, step: 1, value: 0 },
		{ min: 1, max: 0, step: 1, value: 0 },
		{ min: 0, max: 10, step: 0, value: 5 },
		{ min: 0, max: 10, step: -1, value: 5 },
		{ min: 0, max: 10, step: 11, value: 5 },
		{ min: 0, max: 10, step: 1, value: 11 },
		{ min: 0, max: 10, step: 1, value: -1 },
		{ min: 0, max: 1e100, step: 1, value: 5 },
		{ min: 0, max: 10, step: 1e-320, value: 5 }
	])('rejects invalid numeric slider configuration %j', (range) => {
		rejected(spec({ type: 'ParameterSlider', props: { title: 'A', ...range }, children: [] }));
	});

	it('rejects non-finite JSON numbers and excessive JSON nesting', () => {
		rejected(
			'{"root":"main","elements":{"main":{"type":"Chart","props":{"title":"A","kind":"line","data":[{"label":"A","value":1e400}]},"children":[]}}}'
		);
		rejected(`${'['.repeat(20)}${']'.repeat(20)}`);
	});

	it('keeps literal HTML and instruction-like text inert as strings', () => {
		const node = {
			...explanation,
			props: {
				title: '<script>alert(1)</script>',
				content: 'constructor and $state are ordinary words here.'
			}
		};
		const result = parseIntelligentUI(spec(node), true);
		expect(result.spec?.elements.main).toEqual(node);
		expect(result.fallback).toContain('<script>alert(1)</script>');
	});

	it('uses a deterministic small hash without implying cryptographic security', () => {
		expect(intelligentUIHash(valid)).toBe(intelligentUIHash(valid));
		expect(intelligentUIHash(valid)).not.toBe(intelligentUIHash(`${valid} `));
		expect(intelligentUIHash(valid)).toMatch(/^[a-z0-9]+$/);
	});
});
