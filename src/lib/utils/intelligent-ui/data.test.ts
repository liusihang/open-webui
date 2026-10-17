import { describe, expect, it, vi } from 'vitest';
import {
	parseIntelligentUI,
	type ChartProps,
	type DataTableProps,
	type Filter,
	type MetricCardProps,
	type Spec
} from './index';
import {
	chartData,
	chartPointCount,
	filteredRows,
	metricValue,
	resolveSharedValue,
	tableRows,
	type SharedStateReader,
	type SharedValue
} from './data';

const defaults: SharedStateReader = (_key, fallback) => fallback;
const reader =
	(values: Record<string, SharedValue>): SharedStateReader =>
	(key, fallback) =>
		Object.prototype.hasOwnProperty.call(values, key) ? values[key] : fallback;
const filters: Filter[] = [
	{ field: 'group', op: 'eq', stateKey: 'group', ignoreValue: 'all' },
	{ field: 'score', op: 'gte', stateKey: 'minimum' }
];
const chart: ChartProps = {
	title: 'Scores',
	kind: 'bar',
	dataSource: 'samples',
	xField: 'name',
	yField: 'score',
	filters
};
const metric: MetricCardProps = {
	title: 'Mean score',
	dataSource: 'samples',
	aggregation: 'mean',
	field: 'score',
	filters
};
const table: DataTableProps = {
	title: 'Samples',
	dataSource: 'samples',
	columns: [
		{ key: 'name', label: 'Name' },
		{ key: 'score', label: 'Score' }
	],
	filters,
	searchable: true,
	sortable: true,
	selectable: true,
	rowKey: 'id',
	selectionKey: 'selected'
};

function spec(): Spec {
	return {
		root: 'main',
		datasets: {
			samples: [
				{ id: 1, name: 'Alpha', group: 'A', score: 20, size: 2, private: 'secret' },
				{ id: 2, name: 'Beta', group: 'B', score: 60, size: 4, private: 'hidden' },
				{ id: 3, name: 'Gamma', group: 'A', score: 80, size: 6, private: 'hidden' },
				{ id: 4, name: 'Delta', group: 'A', score: null, size: 8, private: 'hidden' }
			]
		},
		elements: {
			main: {
				type: 'Stack',
				props: { title: 'Linked results' },
				children: ['group', 'minimum', 'chart', 'metric', 'table']
			},
			group: {
				type: 'Select',
				props: {
					title: 'Group',
					stateKey: 'group',
					value: 'all',
					options: [
						{ label: 'All groups', value: 'all' },
						{ label: 'Group A', value: 'A' },
						{ label: 'Group B', value: 'B' }
					]
				},
				children: []
			},
			minimum: {
				type: 'NumberInput',
				props: { title: 'Minimum score', stateKey: 'minimum', min: 0, max: 100, step: 5, value: 0 },
				children: []
			},
			chart: { type: 'Chart', props: chart, children: [] },
			metric: { type: 'MetricCard', props: metric, children: [] },
			table: { type: 'DataTable', props: table, children: [] }
		}
	};
}

describe('intelligent-ui linked local data', () => {
	it.each(['null', 7, true, false])(
		'excludes missing cells from equality filtering while normalizing non-null %s values',
		(value) => {
			const source = spec();
			source.datasets!.samples = [
				{ id: 'missing', name: 'Missing', group: null, score: 100 },
				{ id: 'absent', name: 'Absent', score: 200 },
				{ id: 'literal', name: 'Literal', group: String(value), score: 10 },
				{ id: 'typed', name: 'Typed', group: value, score: 20 }
			];
			const select = source.elements.group;
			if (select.type !== 'Select') throw new Error('Expected Select fixture');
			select.props.value = String(value);
			select.props.options = [
				{ label: 'All groups', value: 'all' },
				{ label: 'Selected group', value: String(value) }
			];
			const equality: Filter[] = [{ field: 'group', op: 'eq', stateKey: 'group' }];
			const view = parseIntelligentUI(JSON.stringify(source), true);
			expect(view.error).toBeNull();
			expect(
				tableRows(view.spec!, { ...table, filters: equality }, defaults).map((row) => row.id)
			).toEqual(['literal', 'typed']);
			expect(chartData(view.spec!, { ...chart, filters: equality }, defaults)).toEqual([
				{ label: 'Literal', value: 10 },
				{ label: 'Typed', value: 20 }
			]);
			expect(
				metricValue(view.spec!, { ...metric, aggregation: 'sum', filters: equality }, defaults)
			).toBe(30);
			expect(
				metricValue(view.spec!, { ...metric, aggregation: 'count', filters: equality }, defaults)
			).toBe(2);
		}
	);

	it('updates a chart, metric, and table together from real Select and NumberInput state', () => {
		const parsed = parseIntelligentUI(JSON.stringify(spec()), true);
		expect(parsed.error).toBeNull();
		const view = parsed.spec!;
		const state = { 'shared:group': 'A', 'shared:minimum': 25 };
		const read = reader(state);
		expect(chartData(view, chart, read)).toEqual([{ label: 'Gamma', value: 80 }]);
		expect(metricValue(view, metric, read)).toBe(80);
		expect(tableRows(view, table, read).map((row) => row.name)).toEqual(['Gamma']);
		state['shared:group'] = 'all';
		expect(chartData(view, chart, read)).toEqual([
			{ label: 'Beta', value: 60 },
			{ label: 'Gamma', value: 80 }
		]);
		expect(metricValue(view, metric, read)).toBe(70);
		expect(tableRows(view, table, read).map((row) => row.name)).toEqual(['Beta', 'Gamma']);
	});

	it('reads only namespaced declared state and skips missing controls during streaming', () => {
		const view = spec();
		const read = vi.fn(defaults);
		expect(resolveSharedValue(view, 'group', read)).toBe('all');
		expect(read).toHaveBeenLastCalledWith('shared:group', 'all');
		expect(resolveSharedValue(view, 'absent', read)).toBeUndefined();
		expect(read).toHaveBeenCalledTimes(1);
		delete view.elements.minimum;
		expect(filteredRows(view, 'samples', filters, defaults)).toHaveLength(4);
		expect(filteredRows(view, 'arriving-later', filters, defaults)).toEqual([]);
	});

	it.each(['not-an-option', true, 123])('rejects an invalid persisted Select value %s', (value) => {
		expect(resolveSharedValue(spec(), 'group', reader({ 'shared:group': value }))).toBe('all');
	});

	it.each([
		['35', 0],
		[true, 0],
		[NaN, 0],
		[Infinity, 0],
		[-Infinity, 0],
		[32, 30],
		[33, 35],
		[-200, 0],
		[200, 100]
	])('validates and quantizes numeric state %s to %s', (value, expected) => {
		expect(resolveSharedValue(spec(), 'minimum', reader({ 'shared:minimum': value }))).toBe(
			expected
		);
	});

	it('quantizes slider state relative to min and handles decimal endpoints', () => {
		const view = spec();
		view.elements.minimum = {
			type: 'ParameterSlider',
			props: { title: 'Ratio', stateKey: 'minimum', min: 0, max: 0.3, step: 0.1, value: 0.1 },
			children: []
		};
		expect(resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': 9 }))).toBe(0.3);
		expect(resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': 0.24 }))).toBe(0.2);
		view.elements.minimum.props.min = -0.3;
		expect(resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': -0.19 }))).toBe(-0.2);
		view.elements.minimum.props.max = 0.29;
		expect(resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': 9 }))).toBe(0.2);
	});

	it('resolves controls in inactive tabs and matching numeric controls sharing one state key', () => {
		const view = spec();
		view.elements.main = {
			type: 'Tabs',
			props: {
				title: 'Results and controls',
				labels: ['Chart', 'Minimum', 'Group', 'Metric', 'Table', 'Slider']
			},
			children: ['chart', 'minimum', 'group', 'metric', 'table', 'slider']
		};
		view.elements.slider = {
			type: 'ParameterSlider',
			props: { title: 'Score slider', stateKey: 'minimum', min: 0, max: 100, step: 5, value: 0 },
			children: []
		};
		const parsed = parseIntelligentUI(JSON.stringify(view), true);
		expect(parsed.error).toBeNull();
		expect(resolveSharedValue(parsed.spec!, 'minimum', reader({ 'shared:minimum': 33 }))).toBe(35);
		expect(chartData(parsed.spec!, chart, reader({ 'shared:minimum': 33 }))).toEqual([
			{ label: 'Beta', value: 60 },
			{ label: 'Gamma', value: 80 }
		]);
	});

	it('preserves small steps and high-precision numeric state while quantizing', () => {
		const view = spec();
		view.elements.minimum = {
			type: 'NumberInput',
			props: { title: 'Precise value', stateKey: 'minimum', min: 0, max: 1, step: 1e-16, value: 0 },
			children: []
		};
		expect(
			resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': 0.1234567890123456 }))
		).toBe(0.1234567890123456);
		view.elements.minimum.props.step = 1e-200;
		expect(resolveSharedValue(view, 'minimum', reader({ 'shared:minimum': 1e-200 }))).toBe(1e-200);
	});

	it('restores a table selection only while the row exists and filters linked outputs by row key', () => {
		const view = spec();
		expect(resolveSharedValue(view, 'selected', defaults)).toBe('');
		expect(resolveSharedValue(view, 'selected', reader({ 'shared:selected': '3' }))).toBe('3');
		expect(resolveSharedValue(view, 'selected', reader({ 'shared:selected': 'gone' }))).toBe('');
		expect(resolveSharedValue(view, 'selected', reader({ 'shared:selected': 3 }))).toBe('');
		const selected: Filter[] = [{ field: 'id', op: 'eq', stateKey: 'selected', ignoreValue: '' }];
		expect(filteredRows(view, 'samples', selected, defaults)).toHaveLength(4);
		expect(filteredRows(view, 'samples', selected, reader({ 'shared:selected': '3' }))).toEqual([
			view.datasets!.samples[2]
		]);
		view.datasets!.samples = view.datasets!.samples.filter((row) => row.id !== 3);
		expect(resolveSharedValue(view, 'selected', reader({ 'shared:selected': '3' }))).toBe('');
	});

	it('does not treat a nonselectable table as a shared-state provider', () => {
		const view = spec();
		view.elements.table = {
			type: 'DataTable',
			props: { ...table, selectable: false },
			children: []
		};
		const read = vi.fn(defaults);
		expect(resolveSharedValue(view, 'selected', read)).toBeUndefined();
		expect(read).not.toHaveBeenCalled();
	});

	it('does not let an earlier nonselectable table shadow a valid shared-state provider', () => {
		const view = spec();
		view.elements = {
			earlierTable: {
				type: 'DataTable',
				props: { ...table, selectable: false, selectionKey: 'group' },
				children: []
			},
			...view.elements
		};
		expect(resolveSharedValue(view, 'group', reader({ 'shared:group': 'A' }))).toBe('A');
	});

	it('does not coerce null, numeric strings, booleans, or non-finite numbers into numeric filters', () => {
		const view = spec();
		view.datasets!.samples.push(
			{ id: 5, name: 'Numeric string', group: 'A', score: '90' },
			{ id: 6, name: 'Boolean', group: 'A', score: true },
			{ id: 7, name: 'Infinite', group: 'A', score: Infinity }
		);
		expect(filteredRows(view, 'samples', filters, defaults).map((row) => row.id)).toEqual([
			1, 2, 3
		]);
		const upper: Filter[] = [{ field: 'score', op: 'lte', stateKey: 'minimum' }];
		expect(
			filteredRows(view, 'samples', upper, reader({ 'shared:minimum': 60 })).map((row) => row.id)
		).toEqual([1, 2]);
		expect(
			filteredRows(view, 'samples', [{ field: 'score', op: 'gte', stateKey: 'group' }], defaults)
		).toEqual([]);
	});

	it('uses string equality and case-insensitive string containment without executing expressions', () => {
		const view = spec();
		const contains: Filter[] = [{ field: 'name', op: 'contains', stateKey: 'group' }];
		expect(filteredRows(view, 'samples', contains, reader({ 'shared:group': 'A' }))).toHaveLength(
			4
		);
		view.datasets!.samples[0].name = 'GROUP A';
		expect(
			filteredRows(view, 'samples', contains, reader({ 'shared:group': 'B' })).map((row) => row.id)
		).toEqual([2]);
		const fetchSpy = vi.spyOn(globalThis, 'fetch');
		try {
			chartData(view, chart, defaults);
			metricValue(view, metric, defaults);
			tableRows(view, table, defaults, 'fetch("/api/chat/completions")');
			expect(fetchSpy).not.toHaveBeenCalled();
		} finally {
			fetchSpy.mockRestore();
		}
	});
});

describe('intelligent-ui chart data', () => {
	it('keeps legacy inline charts compatible and copies point data', () => {
		const props: ChartProps = { title: 'Legacy', kind: 'line', data: [{ label: 'A', value: 4 }] };
		const points = chartData(spec(), props, defaults);
		expect(points).toEqual(props.data);
		expect(points).not.toBe(props.data);
		expect(points[0]).not.toBe(props.data[0]);
	});

	it('builds dataset scatter and heatmap formats and discards null and invalid numeric cells', () => {
		const view = spec();
		view.datasets!.samples.push({ id: 5, name: 'Invalid', group: 'B', size: '10', score: '90' });
		expect(
			chartData(view, { ...chart, kind: 'scatter', xField: 'size', filters: [] }, defaults)
		).toEqual([
			{ x: 2, y: 20 },
			{ x: 4, y: 60 },
			{ x: 6, y: 80 }
		]);
		expect(
			chartData(
				view,
				{
					...chart,
					kind: 'heatmap',
					xField: 'group',
					yField: 'id',
					valueField: 'score',
					filters: []
				},
				defaults
			)
		).toEqual([
			{ x: 'A', y: '1', value: 20 },
			{ x: 'B', y: '2', value: 60 },
			{ x: 'A', y: '3', value: 80 }
		]);
	});

	it('supports inline scatter/heatmap and rejects non-finite or null numeric values', () => {
		expect(
			chartData(
				spec(),
				{
					title: 'XY',
					kind: 'scatter',
					data: [
						{ x: 0, y: 0 },
						{ x: NaN, y: 4 },
						{ x: 2, y: Infinity }
					]
				},
				defaults
			)
		).toEqual([{ x: 0, y: 0 }]);
		expect(
			chartData(
				spec(),
				{
					title: 'Grid',
					kind: 'heatmap',
					data: [
						{ x: 'A', y: 'B', value: 0 },
						{ x: 'A', y: 'C', value: NaN }
					]
				},
				defaults
			)
		).toEqual([{ x: 'A', y: 'B', value: 0 }]);
		expect(
			chartData(
				spec(),
				{
					title: 'Invalid',
					kind: 'bar',
					data: [{ label: 'Null', value: null as unknown as number }]
				},
				defaults
			)
		).toEqual([]);
	});

	it('reports 256 valid matching points before displaying the first 128 and handles absent sources', () => {
		const view = spec();
		view.datasets!.samples = Array.from({ length: 300 }, (_, index) => ({
			name: String(index),
			score: index
		}));
		expect(chartData(view, { ...chart, filters: [] }, defaults)).toHaveLength(128);
		expect(chartPointCount(view, { ...chart, filters: [] }, defaults)).toBe(256);
		expect(chartData({ ...view, datasets: {} }, chart, defaults)).toEqual([]);
		expect(chartPointCount({ ...view, datasets: {} }, chart, defaults)).toBe(0);
	});

	it('counts only filtered valid points using the same validation as chart rendering', () => {
		const view = spec();
		const read = reader({ 'shared:group': 'A' });
		for (const props of [
			chart,
			{ ...chart, kind: 'scatter', xField: 'size' },
			{ ...chart, kind: 'heatmap', xField: 'group', yField: 'id', valueField: 'score' }
		] as ChartProps[]) {
			expect(chartPointCount(view, props, read)).toBe(2);
			expect(chartPointCount(view, props, read)).toBe(chartData(view, props, read).length);
		}
	});

	it('fails closed on duplicate normalized heatmap coordinates rather than hiding overlapping cells', () => {
		const view = spec();
		view.datasets!.samples = [
			{ x: 1, y: 'A', value: 10 },
			{ x: '1', y: 'A', value: 20 }
		];
		expect(
			chartData(
				view,
				{
					title: 'Duplicate coordinates',
					kind: 'heatmap',
					dataSource: 'samples',
					xField: 'x',
					yField: 'y',
					valueField: 'value'
				},
				defaults
			)
		).toEqual([]);
		expect(
			chartData(
				view,
				{
					title: 'Duplicate inline coordinates',
					kind: 'heatmap',
					data: [
						{ x: 'A', y: 'B', value: 10 },
						{ x: 'A', y: 'B', value: 20 }
					]
				},
				defaults
			)
		).toEqual([]);
	});
});

describe('intelligent-ui metric aggregates', () => {
	it.each([
		['count', 4],
		['sum', 160],
		['mean', 160 / 3],
		['min', 20],
		['max', 80]
	] as const)('computes %s locally without turning null into zero', (aggregation, expected) => {
		expect(metricValue(spec(), { ...metric, aggregation, filters: [] }, defaults)).toBeCloseTo(
			expected
		);
	});

	it('returns zero for an empty count and no-data for every other empty aggregate', () => {
		const view = { ...spec(), datasets: {} };
		expect(metricValue(view, { ...metric, aggregation: 'count' }, defaults)).toBe(0);
		for (const aggregation of ['sum', 'mean', 'min', 'max'] as const)
			expect(metricValue(view, { ...metric, aggregation }, defaults)).toBeNull();
	});

	it('preserves valid static values and never returns a non-finite aggregate', () => {
		expect(metricValue(spec(), { title: 'Static', value: 0 }, defaults)).toBe(0);
		expect(metricValue(spec(), { title: 'Invalid', value: Infinity }, defaults)).toBeNull();
		const view = spec();
		view.datasets!.samples = [{ score: Number.MAX_VALUE }, { score: Number.MAX_VALUE }];
		expect(metricValue(view, { ...metric, aggregation: 'sum', filters: [] }, defaults)).toBeNull();
		expect(metricValue(view, { ...metric, aggregation: 'mean', filters: [] }, defaults)).toBe(
			Number.MAX_VALUE
		);
	});
});

describe('intelligent-ui table views', () => {
	it('searches declared columns case-insensitively without leaking undeclared fields', () => {
		expect(tableRows(spec(), table, defaults, 'aLPHa').map((row) => row.id)).toEqual([1]);
		expect(tableRows(spec(), table, defaults, '60').map((row) => row.id)).toEqual([2]);
		expect(tableRows(spec(), table, defaults, 'secret')).toEqual([]);
	});

	it('sorts numeric columns numerically, keeps ties stable, and puts nulls last in either direction', () => {
		const view = spec();
		view.datasets!.samples = [
			{ id: 'null', score: null },
			{ id: 'ten', score: 10 },
			{ id: 'two', score: 2 },
			{ id: 'ten-again', score: 10 },
			{ id: 'missing' }
		];
		const props = { ...table, filters: [] };
		expect(
			tableRows(view, props, defaults, '', { field: 'score', direction: 'asc' }).map(
				(row) => row.id
			)
		).toEqual(['two', 'ten', 'ten-again', 'null', 'missing']);
		expect(
			tableRows(view, props, defaults, '', { field: 'score', direction: 'desc' }).map(
				(row) => row.id
			)
		).toEqual(['ten', 'ten-again', 'two', 'null', 'missing']);
	});

	it('sorts strings and mixed columns deterministically and ignores undeclared sort fields', () => {
		const view = spec();
		view.datasets!.samples = [
			{ name: 'b', score: '11' },
			{ name: 'A', score: 10 },
			{ name: 'a', score: 9 },
			{ name: 'c', score: false }
		];
		const props = { ...table, filters: [] };
		expect(
			tableRows(view, props, defaults, '', { field: 'name', direction: 'asc' }).map(
				(row) => row.name
			)
		).toEqual(['A', 'a', 'b', 'c']);
		expect(
			tableRows(view, props, defaults, '', { field: 'score', direction: 'asc' }).map(
				(row) => row.score
			)
		).toEqual([9, 10, '11', false]);
		expect(tableRows(view, props, defaults, '', { field: 'private', direction: 'desc' })).toEqual(
			view.datasets!.samples
		);
	});

	it('bounds tables and never mutates spec/datasets while deriving views', () => {
		const view = spec();
		const original = structuredClone(view);
		Object.freeze(view.datasets!.samples);
		view.datasets!.samples.forEach(Object.freeze);
		chartData(view, chart, defaults);
		metricValue(view, metric, defaults);
		tableRows(view, table, defaults, '', { field: 'score', direction: 'desc' });
		expect(view).toEqual(original);
		const many = {
			...view,
			datasets: { samples: Array.from({ length: 1000 }, (_, id) => ({ id })) }
		};
		expect(tableRows(many, { ...table, filters: [] }, defaults)).toHaveLength(256);
		expect(metricValue(many, { ...metric, aggregation: 'count', filters: [] }, defaults)).toBe(256);
	});
});
