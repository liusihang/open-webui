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
	it('accepts SVG line coordinates in complete specs and JSONL fences', () => {
		const line: IntelligentUIElement = {
			type: 'SVG',
			props: {
				label: 'A line between two points',
				svg: '<svg viewBox="0 0 20 20"><line x1="0" y1="0" x2="20" y2="20" stroke="#000"/></svg>'
			},
			children: []
		};
		const jsonl = `${root}\n${element('main', line)}`;
		for (const source of [spec(line), jsonl, `\`\`\`intelligent-ui\n${jsonl}\n\`\`\``]) {
			const result = parseIntelligentUI(source, true);
			expect(result.error).toBeNull();
			expect(result.spec?.elements.main).toEqual(line);
		}
	});

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
		expect((first.spec?.elements.main.props as { title?: string })?.title).toBe('What changes?');
		expect((second.spec?.elements.main.props as { title?: string })?.title).toBe('New');
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

const datasetRows = [
	{ id: 'a', group: 'North', month: 'Jan', amount: 10, quantity: 2, active: true },
	{ id: 'b', group: 'South', month: 'Feb', amount: null, quantity: 3, active: false }
];
const selectControl: IntelligentUIElement = {
	type: 'Select',
	props: {
		title: 'Region',
		stateKey: 'region',
		value: 'all',
		options: [
			{ label: 'All', value: 'all' },
			{ label: 'North', value: 'North' }
		]
	},
	children: []
};
const dataChart: IntelligentUIElement = {
	type: 'Chart',
	props: {
		title: 'Amount',
		kind: 'bar',
		dataSource: 'records',
		xField: 'group',
		yField: 'amount',
		filters: [{ field: 'group', op: 'eq', stateKey: 'region', ignoreValue: 'all' }]
	},
	children: []
};
const table: IntelligentUIElement = {
	type: 'DataTable',
	props: {
		title: 'Rows',
		dataSource: 'records',
		columns: [
			{ key: 'group', label: 'Region' },
			{ key: 'amount', label: 'Amount' }
		],
		searchable: true,
		sortable: true,
		selectable: true,
		rowKey: 'id',
		selectionKey: 'selectedRow'
	},
	children: []
};
const expandedSpec = (): Spec => ({
	root: 'main',
	datasets: { records: structuredClone(datasetRows) },
	elements: {
		main: {
			type: 'Grid',
			props: { title: 'Dashboard', columns: 2 },
			children: [
				'select',
				'number',
				'tabs',
				'table',
				'metric',
				'staticMetric',
				'scatter',
				'heatmap',
				'slider'
			]
		},
		select: structuredClone(selectControl),
		number: {
			type: 'NumberInput',
			props: {
				title: 'Minimum',
				stateKey: 'minimum',
				min: 0,
				max: 100,
				step: 1,
				value: 0,
				unit: 'USD'
			},
			children: []
		},
		tabs: {
			type: 'Tabs',
			props: { title: 'Views', labels: ['Bars', 'Heatmap'] },
			children: ['chart', 'datasetHeatmap']
		},
		chart: structuredClone(dataChart),
		datasetHeatmap: {
			type: 'Chart',
			props: {
				title: 'Map',
				kind: 'heatmap',
				dataSource: 'records',
				xField: 'group',
				yField: 'month',
				valueField: 'amount'
			},
			children: []
		},
		table: structuredClone(table),
		metric: {
			type: 'MetricCard',
			props: {
				title: 'Total',
				dataSource: 'records',
				aggregation: 'sum',
				field: 'amount',
				filters: [{ field: 'amount', op: 'gte', stateKey: 'minimum' }]
			},
			children: []
		},
		staticMetric: {
			type: 'MetricCard',
			props: { title: 'Target', value: 42, unit: 'USD', description: 'Current target' },
			children: []
		},
		scatter: {
			type: 'Chart',
			props: { title: 'Scatter', kind: 'scatter', data: [{ x: 1, y: 2, label: 'A' }] },
			children: []
		},
		heatmap: {
			type: 'Chart',
			props: { title: 'Heatmap', kind: 'heatmap', data: [{ x: 'A', y: 'B', value: 3 }] },
			children: []
		},
		slider: {
			type: 'ParameterSlider',
			props: { title: 'Quantity', stateKey: 'quantity', min: 0, max: 10, step: 1, value: 2 },
			children: []
		}
	}
});
const expandedSource = (mutate: (value: Spec) => void) => {
	const value = expandedSpec();
	mutate(value);
	return JSON.stringify(value);
};

describe('intelligent-ui expanded data-only protocol', () => {
	it('preserves all expanded nodes and datasets, with plain-text fallback for each family', () => {
		const value = expandedSpec();
		const result = parseIntelligentUI(JSON.stringify(value), true);
		expect(result.error).toBeNull();
		expect(result.spec).toEqual(value);
		for (const text of [
			'Dashboard',
			'Region',
			'Minimum',
			'Bars',
			'Heatmap',
			'Rows',
			'Total',
			'42 USD',
			'A: (1, 2)',
			'A, B: 3'
		])
			expect(result.fallback).toContain(text);
	});

	it('accepts whole dataset add/replace patches, and buffers every partial update character', () => {
		const lines = [
			root,
			element('main', stack(['chart', 'select'])),
			element('chart', dataChart),
			element('select', selectControl),
			patch('/datasets/records', datasetRows)
		];
		const before = lines.join('\n');
		const replacement = [{ ...datasetRows[0], amount: 55 }];
		const update = patch('/datasets/records', replacement, 'replace');
		for (let index = 0; index <= update.length; index++) {
			const result = parseIntelligentUI(`${before}\n${update.slice(0, index)}`, false);
			expect(result.error, `dataset patch prefix ${index}`).toBeNull();
			expect(result.spec?.datasets?.records).toEqual(
				index < update.length ? datasetRows : replacement
			);
		}
		expect(parseIntelligentUI(`${before}\n${update}`, true).spec?.datasets?.records).toEqual(
			replacement
		);
	});

	it('buffers every prefix of an expanded stream with late references and dataset-first full specs', () => {
		const lines = [
			root,
			element('main', stack(['chart', 'select'])),
			element('chart', dataChart),
			patch('/datasets/records', datasetRows),
			element('select', selectControl)
		];
		const source = lines.join('\n');
		for (let index = 0; index <= source.length; index++)
			expect(
				parseIntelligentUI(source.slice(0, index), false).error,
				`stream prefix ${index}`
			).toBeNull();
		const full = JSON.stringify({
			datasets: { records: datasetRows },
			root: 'main',
			elements: { main: explanation }
		});
		for (let index = 0; index <= full.length; index++)
			expect(
				parseIntelligentUI(full.slice(0, index), false).error,
				`full-spec prefix ${index}`
			).toBeNull();
		expect(parseIntelligentUI(source, true).error).toBeNull();
	});

	it('keeps Tabs labels attached to their matching children during out-of-order arrival', () => {
		const source = [
			root,
			element('main', {
				type: 'Tabs',
				props: { labels: ['Later', 'Ready'] },
				children: ['later', 'ready']
			}),
			element('ready', explanation)
		].join('\n');
		const result = parseIntelligentUI(source, false);
		expect(result.spec?.elements.main).toEqual({
			type: 'Tabs',
			props: { labels: ['Ready'] },
			children: ['ready']
		});
		expect(
			parseIntelligentUI(`${source}\n${element('later', explanation)}`, true).spec?.elements.main
				.props
		).toEqual({ labels: ['Later', 'Ready'] });
	});

	it('defers dataset, field, state, and selected-row reference validation until done', () => {
		for (const mutate of [
			(value: Spec) => {
				delete value.datasets;
			},
			(value: Spec) => {
				value.datasets!.records = [{ unrelated: 1 }];
			},
			(value: Spec) => {
				value.elements.select = explanation;
			},
			(value: Spec) => {
				value.datasets!.records[1].id = 'a';
			}
		]) {
			const source = expandedSource(mutate);
			expect(parseIntelligentUI(source, false).error).toBeNull();
			rejected(source);
		}
	});

	it('allows empty datasets and heterogeneous rows but requires every referenced field somewhere', () => {
		expect(
			parseIntelligentUI(
				expandedSource((value) => {
					value.datasets!.records = [];
				}),
				true
			).error
		).toBeNull();
		const source = expandedSource((value) => {
			const { rowKey, selectionKey, ...props } = table.props;
			value.elements.table = { ...table, props: { ...props, selectable: false } };
			value.datasets!.records = [{ group: 'A', month: 'Jan', id: 'a' }, { amount: null }];
		});
		expect(parseIntelligentUI(source, true).error).toBeNull();
		rejected(
			expandedSource((value) => {
				value.datasets!.records.forEach((row) => delete row.month);
			})
		);
	});

	it('allows only compatible shared control contracts, regardless of object-key order', () => {
		const good = expandedSource((value) => {
			value.elements.main.children.push('duplicate');
			value.elements.duplicate = {
				...selectControl,
				props: {
					options: selectControl.props.options,
					value: 'all',
					stateKey: 'region',
					title: 'Region'
				}
			};
		});
		expect(parseIntelligentUI(good, true).error).toBeNull();
		for (const changed of [
			{ ...selectControl.props, value: 'North' },
			{ ...selectControl.props, options: [{ label: 'All regions', value: 'all' }] }
		]) {
			const source = expandedSource((value) => {
				value.elements.duplicate = { type: 'Select', props: changed, children: [] };
			});
			expect(parseIntelligentUI(source, false).error).toBeNull();
			rejected(source);
		}
		rejected(
			expandedSource((value) => {
				value.elements.select = {
					...selectControl,
					props: { ...selectControl.props, stateKey: 'selectedRow' }
				};
			})
		);
	});

	it('allows shared numeric controls and display-only Select differences', () => {
		const source = expandedSource((value) => {
			value.elements.main.children.push('duplicateNumber', 'duplicateSelect');
			value.elements.duplicateNumber = {
				type: 'ParameterSlider',
				props: {
					title: 'Another label',
					stateKey: 'minimum',
					min: 0,
					max: 100,
					step: 1,
					value: 0,
					unit: 'dollars'
				},
				children: []
			};
			value.elements.duplicateSelect = {
				type: 'Select',
				props: {
					title: 'Another region label',
					stateKey: 'region',
					value: 'all',
					options: [
						{ label: 'Northern region', value: 'North' },
						{ label: 'All regions', value: 'all' }
					]
				},
				children: []
			};
		});
		expect(parseIntelligentUI(source, true).error).toBeNull();
		for (const change of [{ min: -1 }, { max: 101 }, { step: 2 }, { value: 1 }]) {
			rejected(
				expandedSource((value) => {
					value.elements.duplicate = {
						type: 'ParameterSlider',
						props: {
							title: 'Duplicate',
							stateKey: 'minimum',
							min: 0,
							max: 100,
							step: 1,
							value: 0,
							...change
						},
						children: []
					};
				})
			);
		}
	});

	it('requires filter state providers to be reachable and validates ignored options', () => {
		rejected(
			expandedSource((value) => {
				value.elements.main.children = value.elements.main.children.filter((id) => id !== 'select');
			})
		);
		rejected(
			expandedSource((value) => {
				const chart = value.elements.chart;
				if (chart.type === 'Chart' && chart.props.dataSource)
					chart.props.filters![0].ignoreValue = 'unknown';
			})
		);
		const selection = expandedSource((value) => {
			value.elements.metric = {
				type: 'MetricCard',
				props: {
					title: 'Selected',
					dataSource: 'records',
					aggregation: 'count',
					filters: [{ field: 'id', op: 'eq', stateKey: 'selectedRow', ignoreValue: '' }]
				},
				children: []
			};
		});
		expect(parseIntelligentUI(selection, true).error).toBeNull();
	});

	it('rejects overlapping heatmap coordinates, including stringified dataset collisions', () => {
		rejected(
			spec({
				type: 'Chart',
				props: {
					title: 'Heat',
					kind: 'heatmap',
					data: [
						{ x: 'a', y: 'b', value: 1 },
						{ x: 'a', y: 'b', value: 2 }
					]
				},
				children: []
			})
		);
		const source = expandedSource((value) => {
			value.datasets!.records[0].group = 1;
			value.datasets!.records[1].group = '1';
			value.datasets!.records[1].month = 'Jan';
			value.datasets!.records[1].amount = 20;
		});
		expect(parseIntelligentUI(source, false).error).toBeNull();
		rejected(source);
		// Null/invalid numeric cells are omitted, so they cannot overlap a visible tile.
		expect(
			parseIntelligentUI(
				expandedSource((value) => {
					value.datasets!.records[1].group = 'North';
					value.datasets!.records[1].month = 'Jan';
				}),
				true
			).error
		).toBeNull();
	});

	it('accepts table-selection state as a filter source and validates stringified unique row keys', () => {
		const source = expandedSource((value) => {
			value.elements.metric = {
				type: 'MetricCard',
				props: {
					title: 'Selected count',
					dataSource: 'records',
					aggregation: 'count',
					filters: [{ field: 'id', op: 'eq', stateKey: 'selectedRow' }]
				},
				children: []
			};
		});
		expect(parseIntelligentUI(source, true).error).toBeNull();
		for (const key of [null, undefined])
			rejected(
				expandedSource((value) => {
					if (key === undefined) delete value.datasets!.records[0].id;
					else value.datasets!.records[0].id = key;
				})
			);
		rejected(
			expandedSource((value) => {
				value.datasets!.records[0].id = 1;
				value.datasets!.records[1].id = '1';
			})
		);
	});

	it.each(['eq', 'gte', 'lte', 'contains'])('allows the bounded filter operation %s', (op) => {
		const source = expandedSource((value) => {
			const node = value.elements.chart;
			if (node.type === 'Chart' && node.props.dataSource) {
				node.props.filters![0].op = op as 'eq';
				if (op !== 'eq') delete node.props.filters![0].ignoreValue;
			}
		});
		expect(parseIntelligentUI(source, true).error).toBeNull();
	});

	it('allows all dataset chart kinds and metric aggregation kinds', () => {
		for (const kind of ['bar', 'line', 'scatter', 'heatmap'] as const) {
			const source = expandedSource((value) => {
				value.elements.chart = {
					type: 'Chart',
					props: {
						title: 'Plot',
						kind,
						dataSource: 'records',
						xField: 'quantity',
						yField: 'amount',
						...(kind === 'heatmap' ? { valueField: 'amount' } : {})
					},
					children: []
				};
			});
			expect(parseIntelligentUI(source, true).error).toBeNull();
		}
		for (const aggregation of ['count', 'sum', 'mean', 'min', 'max'] as const) {
			const source = expandedSource((value) => {
				value.elements.metric = {
					type: 'MetricCard',
					props: {
						title: 'Metric',
						dataSource: 'records',
						aggregation,
						...(aggregation === 'count' ? {} : { field: 'amount' })
					},
					children: []
				};
			});
			expect(parseIntelligentUI(source, true).error).toBeNull();
		}
	});

	it('rejects mixed inline/dataset sources, unsupported fields, and unsafe filter expressions', () => {
		const wrongNodes = [
			{
				type: 'Chart',
				props: {
					title: 'X',
					kind: 'bar',
					data: [],
					dataSource: 'records',
					xField: 'group',
					yField: 'amount'
				},
				children: []
			},
			{
				type: 'Chart',
				props: {
					title: 'X',
					kind: 'heatmap',
					dataSource: 'records',
					xField: 'group',
					yField: 'month'
				},
				children: []
			},
			{
				type: 'MetricCard',
				props: { title: 'X', value: 1, dataSource: 'records', aggregation: 'count' },
				children: []
			},
			{
				type: 'MetricCard',
				props: { title: 'X', dataSource: 'records', aggregation: 'sum' },
				children: []
			},
			{
				type: 'MetricCard',
				props: { title: 'X', dataSource: 'records', aggregation: 'median', field: 'amount' },
				children: []
			},
			{
				...dataChart,
				props: {
					...dataChart.props,
					filters: [{ field: 'amount', op: 'eval', stateKey: 'minimum' }]
				}
			},
			{
				...dataChart,
				props: {
					...dataChart.props,
					filters: [{ field: 'amount', op: 'eq', stateKey: 'minimum', expression: 'alert(1)' }]
				}
			},
			{ ...selectControl, props: { ...selectControl.props, onChange: 'alert(1)' } },
			{ ...table, props: { ...table.props, selectable: 'yes' } },
			{
				...table,
				props: {
					title: 'X',
					dataSource: 'records',
					columns: [{ key: 'group', label: 'Group' }],
					selectable: true
				}
			}
		];
		for (const node of wrongNodes) rejected(spec(node));
	});

	it.each(['bar', 'line', 'scatter'])('rejects unused valueField on dataset %s charts', (kind) => {
		rejected(
			spec({
				type: 'Chart',
				props: {
					title: 'Chart',
					kind,
					dataSource: 'records',
					xField: 'group',
					yField: 'amount',
					valueField: 'quantity'
				},
				children: []
			})
		);
	});

	it('enforces Grid, Tabs, Select, table-column and filter bounds', () => {
		for (const columns of [0, 5, 1.5, '2'])
			rejected(spec({ type: 'Grid', props: { columns }, children: [] }));
		for (const columns of [1, 4])
			expect(
				parseIntelligentUI(spec({ type: 'Grid', props: { columns }, children: [] }), true).error
			).toBeNull();
		rejected(spec({ type: 'Tabs', props: { labels: ['Only one'] }, children: [] }));
		rejected(
			spec({
				type: 'Tabs',
				props: { labels: Array(9).fill('Tab') },
				children: Array.from({ length: 9 }, (_, i) => `child${i}`)
			})
		);
		for (const options of [
			[],
			[
				{ label: 'A', value: 'all' },
				{ label: 'B', value: 'all' }
			],
			[{ label: 'A', value: 'x'.repeat(129) }],
			Array.from({ length: 33 }, (_, i) => ({ label: `${i}`, value: `${i}` }))
		])
			rejected(spec({ ...selectControl, props: { ...selectControl.props, options } }));
		rejected(spec({ ...selectControl, props: { ...selectControl.props, value: 'missing' } }));
		rejected(
			spec({
				...table,
				props: {
					...table.props,
					columns: [
						{ key: 'id', label: 'ID' },
						{ key: 'id', label: 'Again' }
					]
				}
			})
		);
		rejected(spec({ ...table, props: { ...table.props, columns: [] } }));
		rejected(
			spec({
				...dataChart,
				props: {
					...dataChart.props,
					filters: Array(17).fill({ field: 'group', op: 'eq', stateKey: 'region' })
				}
			})
		);
	});

	it('enforces bounded dataset maps, rows, cells, identifiers and primitive data only', () => {
		const withRows = (rows: unknown) =>
			JSON.stringify({
				root: 'main',
				elements: { main: explanation },
				datasets: { records: rows }
			});
		expect(
			parseIntelligentUI(
				withRows(
					Array.from({ length: 256 }, () => ({
						text: 'x'.repeat(256),
						numeric: 1,
						boolean: true,
						empty: null
					}))
				),
				true
			).error
		).toBeNull();
		for (const rows of [
			Array(257).fill({ value: 1 }),
			[{ value: 'x'.repeat(257) }],
			[Object.fromEntries(Array.from({ length: 17 }, (_, i) => [`field${i}`, i]))],
			[{ value: {} }],
			[{ value: [] }],
			[{ value: 1e13 }],
			[{ 'bad.field': 1 }],
			[{ '0field': 1 }],
			[null],
			{ value: 1 }
		])
			rejected(withRows(rows));
		rejected(
			JSON.stringify({
				root: 'main',
				elements: { main: explanation },
				datasets: Object.fromEntries(Array.from({ length: 5 }, (_, i) => [`data${i}`, []]))
			})
		);
		rejected(`${valid}\n${patch('/datasets/records/0', {})}`);
		rejected(`${valid}\n${patch('/datasets/constructor', [])}`);
		rejected(
			'{"root":"main","elements":{"main":{"type":"Stack","props":{},"children":[]}},"datasets":{"records":[{"__proto__":{}}]}}'
		);
		rejected(
			'{"root":"main","elements":{"main":{"type":"Stack","props":{},"children":[]}},"datasets":{"records":[{"value":1e400}]}}'
		);
	});

	it('bounds total rendered table cells across shared and repeated datasets', () => {
		const columns = Array.from({ length: 16 }, (_, index) => ({
			key: `field${index}`,
			label: `Field ${index}`
		}));
		const rows = Array.from({ length: 256 }, () =>
			Object.fromEntries(columns.map(({ key }) => [key, 1]))
		);
		const node: IntelligentUIElement = {
			type: 'DataTable',
			props: { title: 'Rows', dataSource: 'records', columns },
			children: []
		};
		const value: Spec = {
			root: 'main',
			datasets: { records: rows },
			elements: { main: stack(['first', 'second']), first: node, second: node }
		};
		expect(parseIntelligentUI(JSON.stringify(value), true).error).toBeNull();
		value.elements.main.children.push('third');
		value.elements.third = node;
		rejected(JSON.stringify(value));
		rejected(JSON.stringify(value), false);
		// The same table rendered under separate stacks also counts more than once.
		value.elements = {
			main: stack(['left', 'right']),
			left: stack(['first', 'second']),
			right: stack(['first']),
			first: node,
			second: node
		};
		rejected(JSON.stringify(value));
	});

	it('matches persisted state restrictions without banning ordinary multi-line dataset text', () => {
		for (const value of ['line\nbreak', 'tab\tvalue', 'return\rvalue', 'delete\u007Fvalue']) {
			rejected(
				spec({
					type: 'Select',
					props: { title: 'State', stateKey: 'state', value, options: [{ label: 'Label', value }] },
					children: []
				})
			);
			rejected(
				expandedSource((spec) => {
					spec.datasets!.records[0].id = value;
				})
			);
		}
		expect(
			parseIntelligentUI(
				expandedSource((spec) => {
					spec.datasets!.records[0].group = 'First line\nSecond line\tColumn';
				}),
				true
			).error
		).toBeNull();
		expect(
			parseIntelligentUI(
				spec({
					type: 'Select',
					props: {
						title: 'State',
						stateKey: 'state',
						value: 'ok',
						options: [{ label: 'Multi\nline label', value: 'ok' }]
					},
					children: []
				}),
				true
			).error
		).toBeNull();
	});

	it('forbids selection keys on nonselectable tables, including omitted selectable', () => {
		const base = { title: 'Rows', dataSource: 'records', columns: [{ key: 'id', label: 'ID' }] };
		for (const selectable of [undefined, false]) {
			for (const keys of [
				{ rowKey: 'id' },
				{ selectionKey: 'selection' },
				{ rowKey: 'id', selectionKey: 'selection' }
			]) {
				rejected(
					spec({
						type: 'DataTable',
						props: { ...base, ...(selectable === undefined ? {} : { selectable }), ...keys },
						children: []
					})
				);
			}
		}
	});

	it('reserves empty selection state and restricts ignoreValue to equality filters', () => {
		rejected(
			expandedSource((value) => {
				value.datasets!.records[0].id = '';
			})
		);
		for (const op of ['contains', 'gte', 'lte'])
			rejected(
				spec({
					...dataChart,
					props: {
						...dataChart.props,
						filters: [{ field: 'group', op, stateKey: 'region', ignoreValue: 'all' }]
					}
				})
			);
	});

	it('rejects invalid NumberInput ranges and unsafe state/field identifiers', () => {
		rejected(
			spec({
				type: 'NumberInput',
				props: { title: 'X', stateKey: 'number', min: 0, max: 10, step: 0, value: 2 },
				children: []
			})
		);
		for (const stateKey of ['$state', 'constructor', '/number', '0number'])
			rejected(spec({ ...selectControl, props: { ...selectControl.props, stateKey } }));
		for (const xField of ['__proto__', 'constructor', 'a.b'])
			rejected(spec({ ...dataChart, props: { ...dataChart.props, xField } }));
	});
});
