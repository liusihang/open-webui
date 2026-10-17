import {
	EXTENDED_TYPES,
	EXTENDED_CONTROL_TYPES,
	extendedStateReferences,
	validatePressableChildren,
	validateExtendedElement,
	type ExtendedElement
} from './extended';
import { validateIsolatedBlock, type IsolatedBlockElement } from './blocks';
import {
	validateRichChart,
	resolveRichSeries,
	type RichSeries,
	type RichSeriesField
} from './rich-chart';
import {
	chartData,
	filteredRows,
	metricValue,
	tableRows,
	type BarLinePoint,
	type ScatterPoint,
	type HeatmapPoint
} from './data';

/** The deliberately small, data-only subset accepted from model-generated UI fences. */
export type DatasetCell = string | number | boolean | null;
export type DatasetRow = Record<string, DatasetCell>;
export type Filter = {
	field: string;
	op: 'eq' | 'gte' | 'lte' | 'contains';
	stateKey: string;
	ignoreValue?: string;
};
export type StackProps = { title?: string };
export type GridProps = { title?: string; columns: number };
export type TabsProps = { title?: string; labels: string[] };
export type ChartKind = 'bar' | 'line' | 'scatter' | 'heatmap' | 'pie';
type ChartCommonProps = { title: string; description?: string; unit?: string; donut?: boolean };
export type BarLineChartProps = ChartCommonProps & {
	kind: 'bar' | 'line' | 'pie';
	series?: RichSeries[];
	data: { label: string; value: number }[];
	dataSource?: never;
};
export type ScatterChartProps = ChartCommonProps & {
	kind: 'scatter';
	data: { x: number; y: number; label?: string }[];
	dataSource?: never;
};
export type HeatmapChartProps = ChartCommonProps & {
	kind: 'heatmap';
	data: { x: string; y: string; value: number }[];
	dataSource?: never;
};
export type DatasetChartProps = ChartCommonProps & {
	kind: ChartKind;
	dataSource: string;
	xField: string;
	yField: string;
	valueField?: string;
	series?: RichSeriesField[];
	filters?: Filter[];
	data?: never;
};
export type ChartProps =
	| BarLineChartProps
	| ScatterChartProps
	| HeatmapChartProps
	| DatasetChartProps;
export type ComparisonCardsProps = {
	title: string;
	items: {
		title: string;
		description: string;
		attributes?: { label: string; value: string }[];
	}[];
};
export type ExplanationProps = { title: string; content: string };
export type ParameterSliderProps = {
	title: string;
	min: number;
	max: number;
	step: number;
	value: number;
	unit?: string;
	followUp?: string;
	stateKey?: string;
};
export type NumberInputProps = {
	title: string;
	stateKey: string;
	min: number;
	max: number;
	step: number;
	value: number;
	unit?: string;
};
export type SelectProps = {
	title: string;
	stateKey: string;
	value: string;
	options: { label: string; value: string }[];
};
export type DataTableProps = {
	title: string;
	dataSource: string;
	columns: { key: string; label: string }[];
	filters?: Filter[];
	searchable?: boolean;
	sortable?: boolean;
	selectable?: boolean;
	rowKey?: string;
	selectionKey?: string;
};
type MetricCommonProps = { title: string; description?: string; unit?: string };
export type StaticMetricCardProps = MetricCommonProps & { value: number; dataSource?: never };
export type DatasetMetricCardProps = MetricCommonProps & {
	dataSource: string;
	aggregation: 'count' | 'sum' | 'mean' | 'min' | 'max';
	field?: string;
	filters?: Filter[];
	value?: never;
};
export type MetricCardProps = StaticMetricCardProps | DatasetMetricCardProps;

type ElementOf<T extends string, P> = { type: T; props: P; children: string[] };
export type IntelligentUIElement =
	| ExtendedElement
	| IsolatedBlockElement
	| ElementOf<'CodeBlock', { title?: string; language: string; code: string }>
	| ElementOf<'Stack', StackProps>
	| ElementOf<'Grid', GridProps>
	| ElementOf<'Tabs', TabsProps>
	| ElementOf<'Chart', ChartProps>
	| ElementOf<'ComparisonCards', ComparisonCardsProps>
	| ElementOf<'Explanation', ExplanationProps>
	| ElementOf<'ParameterSlider', ParameterSliderProps>
	| ElementOf<'NumberInput', NumberInputProps>
	| ElementOf<'Select', SelectProps>
	| ElementOf<'DataTable', DataTableProps>
	| ElementOf<'MetricCard', MetricCardProps>;
export type Spec = {
	root: string;
	elements: Record<string, IntelligentUIElement>;
	datasets?: Record<string, DatasetRow[]>;
};
export type IntelligentUISpec = Spec;
export type ParseResult = {
	spec: Spec | null;
	error: string | null;
	pending: boolean;
	/** Plain text only. Render as text, never HTML or executable Markdown. */
	fallback: string;
};

export const INTELLIGENT_UI_LIMITS = Object.freeze({
	bytes: 128 * 1024,
	nodes: 64,
	depth: 8,
	operations: 512,
	chartPoints: 128,
	cards: 12,
	attributes: 16,
	datasets: 4,
	datasetRows: 256,
	datasetFields: 16,
	tableCells: 8192,
	cellText: 256,
	filters: 16,
	selectOptions: 32,
	tabs: 8,
	title: 160,
	description: 2048,
	content: 16 * 1024,
	number: 1e12
});

const unsafeKeys = new Set(['__proto__', 'prototype', 'constructor']);
const own = (object: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(object, key);
const unavailable = 'This interactive view could not be displayed. Please use the text response.';

class InvalidUI extends Error {}

function requireValid(condition: unknown, message: string): asserts condition {
	if (!condition) throw new InvalidUI(message);
}

function isObject(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function fields(
	value: unknown,
	required: string[],
	optional: string[] = []
): Record<string, unknown> {
	requireValid(isObject(value), 'Expected a data object.');
	const allowed = new Set([...required, ...optional]);
	requireValid(
		Object.keys(value).every((key) => allowed.has(key)) && required.every((key) => own(value, key)),
		'Unsupported or missing UI fields.'
	);
	return value;
}

function safeId(value: unknown): asserts value is string {
	requireValid(
		typeof value === 'string' &&
			/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value) &&
			!unsafeKeys.has(value.toLowerCase()),
		'Invalid element identifier.'
	);
}

function string(
	value: unknown,
	max: number = INTELLIGENT_UI_LIMITS.title
): asserts value is string {
	requireValid(
		typeof value === 'string' &&
			value.length <= max &&
			!/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value),
		'Invalid or oversized text.'
	);
}

/** Values persisted as shared state use the backend's stricter control-character rule. */
function stateString(value: unknown, max: number): asserts value is string {
	string(value, max);
	requireValid(!/[\u0000-\u001F\u007F]/.test(value), 'Invalid persisted state text.');
}

function optionalString(object: Record<string, unknown>, key: string, max?: number) {
	if (own(object, key)) string(object[key], max);
}

function number(value: unknown): asserts value is number {
	requireValid(
		typeof value === 'number' &&
			Number.isFinite(value) &&
			Math.abs(value) <= INTELLIGENT_UI_LIMITS.number,
		'Invalid or out-of-range number.'
	);
}

function array(value: unknown, max: number): asserts value is unknown[] {
	requireValid(Array.isArray(value) && value.length <= max, 'Invalid or oversized list.');
}

/** No prototype keys, executable directives, or dynamic bindings are part of this format. */
function inspectKeys(value: unknown, depth = 0) {
	requireValid(depth <= 16, 'UI data is nested too deeply.');
	if (Array.isArray(value)) {
		for (const item of value) inspectKeys(item, depth + 1);
	} else if (isObject(value)) {
		for (const [key, item] of Object.entries(value)) {
			requireValid(!unsafeKeys.has(key.toLowerCase()) && !key.startsWith('$'), 'Unsafe UI field.');
			inspectKeys(item, depth + 1);
		}
	}
}

function optionalId(object: Record<string, unknown>, key: string) {
	if (own(object, key)) safeId(object[key]);
}

function filters(value: unknown) {
	array(value, INTELLIGENT_UI_LIMITS.filters);
	for (const entry of value) {
		const filter = fields(entry, ['field', 'op', 'stateKey'], ['ignoreValue']);
		safeId(filter.field);
		safeId(filter.stateKey);
		requireValid(
			['eq', 'gte', 'lte', 'contains'].includes(filter.op as string),
			'Unsupported filter operation.'
		);
		optionalString(filter, 'ignoreValue', INTELLIGENT_UI_LIMITS.cellText);
		requireValid(
			!own(filter, 'ignoreValue') || filter.op === 'eq',
			'Only equality filters support an ignored value.'
		);
	}
}

function optionalFilters(props: Record<string, unknown>) {
	if (own(props, 'filters')) filters(props.filters);
}

function dataset(value: unknown): DatasetRow[] {
	array(value, INTELLIGENT_UI_LIMITS.datasetRows);
	for (const row of value) {
		requireValid(isObject(row), 'Expected a dataset row.');
		requireValid(
			Object.keys(row).length <= INTELLIGENT_UI_LIMITS.datasetFields,
			'Too many dataset fields.'
		);
		for (const [key, cell] of Object.entries(row)) {
			safeId(key);
			if (typeof cell === 'string') string(cell, INTELLIGENT_UI_LIMITS.cellText);
			else if (typeof cell === 'number') number(cell);
			else requireValid(cell === null || typeof cell === 'boolean', 'Unsupported dataset cell.');
		}
	}
	return value as DatasetRow[];
}

function numericControl(props: Record<string, unknown>) {
	string(props.title);
	for (const key of ['min', 'max', 'step', 'value']) number(props[key]);
	const { min, max, step, value } = props as NumberInputProps;
	requireValid(
		min < max &&
			step > 0 &&
			step <= max - min &&
			Number.isFinite((max - min) / step) &&
			value >= min &&
			value <= max,
		'Invalid numeric control range, step, or value.'
	);
	optionalString(props, 'unit', 64);
}

function element(value: unknown): IntelligentUIElement {
	const node = fields(value, ['type', 'props', 'children']);
	array(node.children, INTELLIGENT_UI_LIMITS.nodes);
	node.children.forEach(safeId);
	requireValid(
		new Set(node.children).size === node.children.length,
		'Duplicate child identifiers.'
	);
	if (EXTENDED_TYPES.includes(node.type as any)) return validateExtendedElement(node);
	requireValid(
		['Stack', 'Grid', 'Tabs'].includes(node.type as string) || node.children.length === 0,
		'Only layout components may have children.'
	);
	let props: Record<string, unknown>;
	switch (node.type) {
		case 'AppBlock':
		case 'WritingBlock':
			validateIsolatedBlock(node.type, node.props);
			break;
		case 'CodeBlock':
			props = fields(node.props, ['language', 'code'], ['title']);
			optionalString(props, 'title');
			string(props.code, INTELLIGENT_UI_LIMITS.content);
			requireValid(
				typeof props.language === 'string' && /^[a-zA-Z0-9_+-]{1,32}$/.test(props.language),
				'Invalid code language.'
			);
			break;
		case 'Stack':
			props = fields(node.props, [], ['title']);
			optionalString(props, 'title');
			break;
		case 'Grid':
			props = fields(node.props, ['columns'], ['title']);
			optionalString(props, 'title');
			requireValid(
				Number.isInteger(props.columns) && Number(props.columns) >= 1 && Number(props.columns) <= 4,
				'Invalid grid columns.'
			);
			break;
		case 'Tabs':
			props = fields(node.props, ['labels'], ['title']);
			optionalString(props, 'title');
			array(props.labels, INTELLIGENT_UI_LIMITS.tabs);
			props.labels.forEach((label) => string(label));
			requireValid(
				props.labels.length === node.children.length,
				'Each tab needs one label and child.'
			);
			break;
		case 'Chart': {
			requireValid(isObject(node.props), 'Expected chart properties.');
			const fromDataset = own(node.props, 'dataSource');
			props = fields(
				node.props,
				fromDataset
					? ['title', 'kind', 'dataSource', 'xField', 'yField']
					: ['title', 'kind', 'data'],
				fromDataset
					? ['description', 'unit', 'valueField', 'filters', 'series', 'donut']
					: ['description', 'unit', 'series', 'donut']
			);
			string(props.title);
			optionalString(props, 'description', INTELLIGENT_UI_LIMITS.description);
			optionalString(props, 'unit', 64);
			requireValid(
				['bar', 'line', 'scatter', 'heatmap', 'pie'].includes(props.kind as string),
				'Unsupported chart kind.'
			);
			validateRichChart(props);
			if (fromDataset) {
				for (const series of (props.series as RichSeriesField[] | undefined) ?? [])
					safeId(series.yField);
				for (const key of ['dataSource', 'xField', 'yField']) safeId(props[key]);
				optionalId(props, 'valueField');
				optionalFilters(props);
				requireValid(
					props.kind !== 'heatmap' || own(props, 'valueField'),
					'A heatmap needs a value field.'
				);
				requireValid(
					props.kind === 'heatmap' || !own(props, 'valueField'),
					'Only heatmaps support a value field.'
				);
			} else {
				array(props.data, INTELLIGENT_UI_LIMITS.chartPoints);
				const coordinates = new Set<string>();
				for (const entry of props.data) {
					if (props.kind === 'scatter') {
						const point = fields(entry, ['x', 'y'], ['label']);
						number(point.x);
						number(point.y);
						optionalString(point, 'label');
					} else if (props.kind === 'heatmap') {
						const point = fields(entry, ['x', 'y', 'value']);
						string(point.x);
						string(point.y);
						number(point.value);
						const coordinate = JSON.stringify([point.x, point.y]);
						requireValid(!coordinates.has(coordinate), 'Duplicate heatmap coordinates.');
						coordinates.add(coordinate);
					} else {
						const point = fields(entry, ['label', 'value']);
						string(point.label);
						number(point.value);
					}
				}
			}
			break;
		}
		case 'ComparisonCards':
			props = fields(node.props, ['title', 'items']);
			string(props.title);
			array(props.items, INTELLIGENT_UI_LIMITS.cards);
			for (const entry of props.items) {
				const card = fields(entry, ['title', 'description'], ['attributes']);
				string(card.title);
				string(card.description, INTELLIGENT_UI_LIMITS.description);
				if (own(card, 'attributes')) {
					array(card.attributes, INTELLIGENT_UI_LIMITS.attributes);
					for (const entry of card.attributes) {
						const attribute = fields(entry, ['label', 'value']);
						string(attribute.label);
						string(attribute.value, 1024);
					}
				}
			}
			break;
		case 'Explanation':
			props = fields(node.props, ['title', 'content']);
			string(props.title);
			string(props.content, INTELLIGENT_UI_LIMITS.content);
			break;
		case 'ParameterSlider':
			props = fields(
				node.props,
				['title', 'min', 'max', 'step', 'value'],
				['unit', 'followUp', 'stateKey']
			);
			numericControl(props);
			optionalId(props, 'stateKey');
			optionalString(props, 'followUp', INTELLIGENT_UI_LIMITS.description);
			break;
		case 'NumberInput':
			props = fields(node.props, ['title', 'stateKey', 'min', 'max', 'step', 'value'], ['unit']);
			numericControl(props);
			safeId(props.stateKey);
			break;
		case 'Select': {
			props = fields(node.props, ['title', 'stateKey', 'value', 'options']);
			string(props.title);
			safeId(props.stateKey);
			stateString(props.value, 128);
			array(props.options, INTELLIGENT_UI_LIMITS.selectOptions);
			const values = new Set<string>();
			for (const entry of props.options) {
				const option = fields(entry, ['label', 'value']);
				string(option.label);
				stateString(option.value, 128);
				requireValid(!values.has(option.value), 'Duplicate select option values.');
				values.add(option.value);
			}
			requireValid(values.has(props.value), 'The select value must match an option.');
			break;
		}
		case 'DataTable':
			props = fields(
				node.props,
				['title', 'dataSource', 'columns'],
				['filters', 'searchable', 'sortable', 'selectable', 'rowKey', 'selectionKey']
			);
			string(props.title);
			safeId(props.dataSource);
			array(props.columns, INTELLIGENT_UI_LIMITS.datasetFields);
			requireValid(props.columns.length > 0, 'A table needs at least one column.');
			const columnKeys = new Set<string>();
			for (const entry of props.columns) {
				const column = fields(entry, ['key', 'label']);
				safeId(column.key);
				string(column.label);
				requireValid(!columnKeys.has(column.key), 'Duplicate table columns.');
				columnKeys.add(column.key);
			}
			optionalFilters(props);
			for (const key of ['searchable', 'sortable', 'selectable']) {
				if (own(props, key))
					requireValid(typeof props[key] === 'boolean', 'Expected a boolean option.');
			}
			optionalId(props, 'rowKey');
			optionalId(props, 'selectionKey');
			requireValid(
				props.selectable !== true || (own(props, 'rowKey') && own(props, 'selectionKey')),
				'Selectable tables need row and selection keys.'
			);
			requireValid(
				props.selectable === true || (!own(props, 'rowKey') && !own(props, 'selectionKey')),
				'Only selectable tables support row and selection keys.'
			);
			break;
		case 'MetricCard': {
			requireValid(isObject(node.props), 'Expected metric properties.');
			const fromDataset = own(node.props, 'dataSource');
			props = fields(
				node.props,
				fromDataset ? ['title', 'dataSource', 'aggregation'] : ['title', 'value'],
				fromDataset ? ['description', 'unit', 'field', 'filters'] : ['description', 'unit']
			);
			string(props.title);
			optionalString(props, 'description', INTELLIGENT_UI_LIMITS.description);
			optionalString(props, 'unit', 64);
			if (fromDataset) {
				safeId(props.dataSource);
				requireValid(
					['count', 'sum', 'mean', 'min', 'max'].includes(props.aggregation as string),
					'Unsupported metric aggregation.'
				);
				optionalId(props, 'field');
				requireValid(
					props.aggregation === 'count' || own(props, 'field'),
					'This metric needs a numeric field.'
				);
				optionalFilters(props);
			} else number(props.value);
			break;
		}
		default:
			throw new InvalidUI('Unsupported UI component.');
	}
	// All nested fields have been checked, including their exact key sets.
	return node as IntelligentUIElement;
}

type DraftSpec = {
	root: string | null;
	elements: Record<string, IntelligentUIElement>;
	datasets?: Record<string, DatasetRow[]>;
};

/** Object key order is irrelevant when matching repeated shared-control definitions. */
function canonical(value: unknown): string {
	if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
	if (isObject(value))
		return `{${Object.keys(value)
			.sort()
			.map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
			.join(',')}}`;
	return JSON.stringify(value);
}

function validateReferences(draft: DraftSpec) {
	validatePressableChildren(draft.elements);
	const states = new Map<string, string>();
	const ignoreValues = new Map<string, Set<string>>();
	const reachable = new Set<string>();
	const reachableStates = new Set<string>();
	function visit(id: string) {
		if (reachable.has(id) || !own(draft.elements, id)) return;
		reachable.add(id);
		draft.elements[id].children.forEach(visit);
	}
	if (draft.root !== null) visit(draft.root);
	for (const [id, node] of Object.entries(draft.elements)) {
		let key: string | undefined;
		let definition: unknown;
		if (node.type === 'Select' || node.type === 'RadioGroup' || node.type === 'SegmentedControl') {
			key = node.props.stateKey;
			const values = node.props.options.map((option) => option.value).sort();
			definition = { type: 'select', value: node.props.value, values };
			ignoreValues.set(key, new Set(values));
		} else if (EXTENDED_CONTROL_TYPES.includes(node.type)) {
			const props = node.props as any;
			key = props.stateKey;
			definition = {
				type: node.type,
				value: props.value,
				maxLength: props.maxLength,
				min: props.min,
				max: props.max
			};
		} else if (node.type === 'ParameterSlider' || node.type === 'NumberInput') {
			key = node.props.stateKey;
			const { min, max, step, value } = node.props;
			definition = { type: 'number', min, max, step, value };
		} else if (node.type === 'DataTable' && node.props.selectable) {
			key = node.props.selectionKey;
			definition = {
				type: 'selection',
				dataSource: node.props.dataSource,
				rowKey: node.props.rowKey
			};
			if (key)
				ignoreValues.set(
					key,
					new Set([
						'',
						...(draft.datasets?.[node.props.dataSource] ?? []).flatMap((row) => {
							const value = row[node.props.rowKey!];
							return value === null || value === undefined ? [] : [String(value)];
						})
					])
				);
		}
		if (key !== undefined) {
			const signature = canonical(definition);
			requireValid(
				!states.has(key) || states.get(key) === signature,
				'Conflicting shared-state definitions.'
			);
			states.set(key, signature);
			if (reachable.has(id)) reachableStates.add(key);
		}
	}
	for (const [id, node] of Object.entries(draft.elements)) {
		const references =
			node.type === 'AppBlock' ? (node.props.stateKeys ?? []) : extendedStateReferences(node);
		for (const reference of references) {
			requireValid(states.has(reference), 'The finished UI references missing shared state.');
			if (reachable.has(id))
				requireValid(
					reachableStates.has(reference),
					'A visible view needs a reachable state provider.'
				);
		}
		if (node.type !== 'Chart' && node.type !== 'MetricCard' && node.type !== 'DataTable') continue;
		const props = node.props;
		if (props.dataSource === undefined) continue;
		requireValid(
			draft.datasets && own(draft.datasets, props.dataSource),
			'The finished UI references a missing dataset.'
		);
		const rows = draft.datasets[props.dataSource];
		const fieldExists = (field: string) => {
			// Empty datasets have no declared schema; a future replacement may provide the fields.
			requireValid(
				rows.length === 0 || rows.some((row) => own(row, field)),
				'The finished UI references a missing dataset field.'
			);
		};
		if (node.type === 'Chart') {
			const chart = props as DatasetChartProps;
			fieldExists(chart.xField);
			fieldExists(chart.yField);
			for (const series of chart.series ?? []) fieldExists(series.yField);
			if (chart.kind === 'pie')
				requireValid(
					rows.every(
						(row) => typeof row[chart.yField] !== 'number' || Number(row[chart.yField]) >= 0
					),
					'Pie values cannot be negative.'
				);
			if (chart.valueField !== undefined) fieldExists(chart.valueField);
			if (chart.kind === 'heatmap') {
				const coordinates = new Set<string>();
				for (const row of rows) {
					const x = row[chart.xField],
						y = row[chart.yField],
						value = row[chart.valueField!];
					if (
						x === null ||
						x === undefined ||
						y === null ||
						y === undefined ||
						typeof value !== 'number' ||
						!Number.isFinite(value)
					)
						continue;
					const coordinate = JSON.stringify([String(x), String(y)]);
					requireValid(!coordinates.has(coordinate), 'Duplicate heatmap coordinates.');
					coordinates.add(coordinate);
				}
			}
		} else if (node.type === 'MetricCard') {
			const metric = props as DatasetMetricCardProps;
			if (metric.field !== undefined) fieldExists(metric.field);
		} else {
			const table = props as DataTableProps;
			table.columns.forEach((column) => fieldExists(column.key));
			if (table.rowKey !== undefined) fieldExists(table.rowKey);
			if (table.selectable) {
				const keys = new Set<string>();
				for (const row of rows) {
					const key = row[table.rowKey!];
					if (typeof key === 'string') stateString(key, INTELLIGENT_UI_LIMITS.cellText);
					requireValid(
						key !== null && key !== undefined && String(key) !== '' && !keys.has(String(key)),
						'Selectable table row keys must be unique and non-empty.'
					);
					keys.add(String(key));
				}
			}
		}
		for (const filter of (props as DatasetChartProps | DatasetMetricCardProps | DataTableProps)
			.filters ?? []) {
			fieldExists(filter.field);
			requireValid(states.has(filter.stateKey), 'The finished UI references missing filter state.');
			if (reachable.has(id))
				requireValid(
					reachableStates.has(filter.stateKey),
					'A visible filter needs a visible state provider.'
				);
			if (filter.ignoreValue !== undefined)
				requireValid(
					ignoreValues.get(filter.stateKey)?.has(filter.ignoreValue),
					'The ignored filter value must match a state option.'
				);
		}
	}
}

function validateGraph(draft: DraftSpec, done: boolean) {
	requireValid(
		Object.keys(draft.datasets ?? {}).length <= INTELLIGENT_UI_LIMITS.datasets,
		'Too many datasets.'
	);
	const ids = Object.keys(draft.elements);
	requireValid(ids.length <= INTELLIGENT_UI_LIMITS.nodes, 'Too many UI elements.');
	const heights = new Map<string, number>();
	const visiting = new Set<string>();
	function height(id: string): number {
		if (!own(draft.elements, id)) {
			requireValid(!done, 'The finished UI references a missing element.');
			return 0;
		}
		requireValid(!visiting.has(id), 'UI elements cannot contain cycles.');
		if (heights.has(id)) return heights.get(id)!;
		visiting.add(id);
		const result = 1 + Math.max(0, ...draft.elements[id].children.map(height));
		visiting.delete(id);
		requireValid(result <= INTELLIGENT_UI_LIMITS.depth, 'The UI tree is too deep.');
		heights.set(id, result);
		return result;
	}
	for (const id of ids) height(id);
	if (done) {
		requireValid(draft.root !== null, 'The finished UI has no root.');
		requireValid(own(draft.elements, draft.root), 'The finished UI references a missing root.');
		validateReferences(draft);
	}
}

/** A renderer never receives dangling child references or unrelated orphan elements. */
function reachableSpec(draft: DraftSpec): Spec | null {
	if (draft.root === null || !own(draft.elements, draft.root)) return null;
	const elements: Spec['elements'] = {};
	let renderedNodes = 0;
	let renderedTableCells = 0;
	function visit(id: string) {
		requireValid(
			++renderedNodes <= INTELLIGENT_UI_LIMITS.nodes,
			'The UI expands to too many elements.'
		);
		const node = draft.elements[id];
		if (node.type === 'DataTable') {
			renderedTableCells +=
				(draft.datasets?.[node.props.dataSource]?.length ?? 0) * node.props.columns.length;
			requireValid(
				renderedTableCells <= INTELLIGENT_UI_LIMITS.tableCells,
				'The UI expands to too many table cells.'
			);
		}
		const children = node.children.filter((child) => own(draft.elements, child));
		elements[id] =
			node.type === 'Tabs'
				? {
						...node,
						props: {
							...node.props,
							labels: node.props.labels.filter((_, index) =>
								own(draft.elements, node.children[index])
							)
						},
						children
					}
				: { ...node, children };
		// Count repeated subtrees too: a small acyclic graph can otherwise expand exponentially.
		children.forEach(visit);
	}
	visit(draft.root);
	validatePressableChildren(elements);
	return {
		root: draft.root,
		elements,
		...(draft.datasets === undefined ? {} : { datasets: draft.datasets })
	};
}

function apply(value: unknown, draft: DraftSpec) {
	inspectKeys(value);
	const patch = fields(value, ['op', 'path', 'value']);
	requireValid(patch.op === 'add' || patch.op === 'replace', 'Unsupported UI patch operation.');
	if (patch.path === '/root') {
		safeId(patch.value);
		draft.root = patch.value;
	} else {
		requireValid(typeof patch.path === 'string', 'Invalid UI patch path.');
		const match = /^\/(elements|datasets)\/([a-zA-Z][a-zA-Z0-9_-]{0,63})$/.exec(patch.path);
		requireValid(match !== null, 'Only whole root, element, or dataset updates are supported.');
		const id = match[2];
		safeId(id);
		if (match[1] === 'datasets') {
			draft.datasets ??= {};
			draft.datasets[id] = dataset(patch.value);
		} else draft.elements[id] = element(patch.value);
	}
	validateGraph(draft, false);
}

function fullSpec(value: unknown): DraftSpec {
	inspectKeys(value);
	const spec = fields(value, ['root', 'elements'], ['datasets']);
	safeId(spec.root);
	requireValid(isObject(spec.elements), 'Expected an element map.');
	requireValid(
		Object.keys(spec.elements).length <= INTELLIGENT_UI_LIMITS.nodes,
		'Too many UI elements.'
	);
	const elements: Spec['elements'] = {};
	for (const [id, value] of Object.entries(spec.elements)) {
		safeId(id);
		elements[id] = element(value);
	}
	const draft: DraftSpec = { root: spec.root, elements };
	if (own(spec, 'datasets')) {
		requireValid(isObject(spec.datasets), 'Expected a dataset map.');
		draft.datasets = {};
		for (const [id, rows] of Object.entries(spec.datasets)) {
			safeId(id);
			draft.datasets[id] = dataset(rows);
		}
	}
	return draft;
}

const incomplete = Symbol('incomplete');
const invalid = Symbol('invalid');
type JsonState = 'complete' | 'incomplete' | 'invalid';

/** Classify a JSON prefix without mistaking malformed final lines for unfinished streams. */
function jsonState(source: string): JsonState {
	let offset = 0;
	const space = () => {
		while (/[\t\n\r ]/.test(source[offset] ?? '') && offset < source.length) offset++;
	};
	const expected = (character: string) => {
		if (offset === source.length) throw incomplete;
		if (source[offset++] !== character) throw invalid;
	};
	function quoted() {
		const start = offset;
		expected('"');
		while (offset < source.length) {
			const char = source[offset++];
			if (char === '"') return JSON.parse(source.slice(start, offset)) as string;
			if (char.charCodeAt(0) < 32) throw invalid;
			if (char === '\\') {
				if (offset === source.length) throw incomplete;
				const escape = source[offset++];
				if (escape === 'u') {
					for (let digit = 0; digit < 4; digit++) {
						if (offset === source.length) throw incomplete;
						if (!/[0-9a-fA-F]/.test(source[offset++])) throw invalid;
					}
				} else if (!'"\\/bfnrt'.includes(escape)) throw invalid;
			}
		}
		throw incomplete;
	}
	function value(depth: number) {
		if (depth > 16) throw invalid;
		space();
		if (offset === source.length) throw incomplete;
		const char = source[offset];
		if (char === '"') return quoted();
		if (char === '{' || char === '[') {
			const object = char === '{';
			const close = object ? '}' : ']';
			const keys = new Set<string>();
			offset++;
			space();
			if (source[offset] === close) {
				offset++;
				return;
			}
			while (true) {
				if (object) {
					const key = quoted();
					if (keys.has(key) || unsafeKeys.has(key.toLowerCase()) || key.startsWith('$'))
						throw invalid;
					keys.add(key);
					space();
					expected(':');
				}
				value(depth + 1);
				space();
				if (source[offset] === close) {
					offset++;
					return;
				}
				expected(',');
				space();
			}
		}
		for (const literal of ['true', 'false', 'null']) {
			if (char === literal[0]) {
				for (const character of literal) expected(character);
				return;
			}
		}
		if (char === '-' || /[0-9]/.test(char)) {
			if (char === '-') {
				offset++;
				if (offset === source.length) throw incomplete;
			}
			if (source[offset] === '0') offset++;
			else {
				if (!/[1-9]/.test(source[offset] ?? '')) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			if (source[offset] === '.') {
				offset++;
				if (offset === source.length) throw incomplete;
				if (!/[0-9]/.test(source[offset])) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			if (source[offset] === 'e' || source[offset] === 'E') {
				offset++;
				if (source[offset] === '+' || source[offset] === '-') offset++;
				if (offset === source.length) throw incomplete;
				if (!/[0-9]/.test(source[offset])) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			return;
		}
		throw invalid;
	}
	try {
		value(0);
		space();
		return offset === source.length ? 'complete' : 'invalid';
	} catch (error) {
		return error === incomplete ? 'incomplete' : 'invalid';
	}
}

function fallbackText(spec: Spec | null): string {
	if (!spec) return '';
	const lines: string[] = [];
	function visit(id: string) {
		const node = spec!.elements[id];
		if ('title' in node.props && node.props.title) lines.push(node.props.title);
		switch (node.type) {
			case 'Chart': {
				if (node.props.description) lines.push(node.props.description);
				if ('series' in node.props && node.props.series) {
					const series =
						node.props.dataSource !== undefined
							? resolveRichSeries(
									filteredRows(
										spec!,
										node.props.dataSource,
										node.props.filters,
										(_, fallback) => fallback
									),
									node.props.xField,
									node.props.series
								)
							: node.props.series;
					for (const item of series)
						for (const point of item.data)
							lines.push(`${item.name} / ${point.label}: ${point.value}`);
					break;
				}
				const points = chartData(spec!, node.props, (_, fallback) => fallback);
				if (node.props.kind === 'scatter') {
					for (const point of points as ScatterPoint[])
						lines.push(`${point.label ? `${point.label}: ` : ''}(${point.x}, ${point.y})`);
				} else if (node.props.kind === 'heatmap') {
					for (const point of points as HeatmapPoint[])
						lines.push(
							`${point.x}, ${point.y}: ${point.value}${node.props.unit ? ` ${node.props.unit}` : ''}`
						);
				} else {
					for (const point of points as BarLinePoint[])
						lines.push(
							`${point.label}: ${point.value}${node.props.unit ? ` ${node.props.unit}` : ''}`
						);
				}
				break;
			}
			case 'MetricCard': {
				if (node.props.description) lines.push(node.props.description);
				const value = metricValue(spec!, node.props, (_, fallback) => fallback);
				lines.push(
					value === null
						? 'No numeric data'
						: `${value}${node.props.unit ? ` ${node.props.unit}` : ''}`
				);
				break;
			}
			case 'DataTable':
				lines.push(node.props.columns.map((column) => column.label).join(' | '));
				for (const row of tableRows(spec!, node.props, (_, fallback) => fallback))
					lines.push(
						node.props.columns
							.map((column) =>
								row[column.key] === null || row[column.key] === undefined
									? ''
									: String(row[column.key])
							)
							.join(' | ')
					);
				break;
			case 'WritingBlock':
				lines.push(node.props.content);
				break;
			case 'CodeBlock':
				lines.push(node.props.code);
				break;
			case 'Math':
				lines.push(node.props.expression);
				break;
			case 'Text':
				lines.push(node.props.text);
				break;
			case 'Image':
				lines.push(node.props.alt, node.props.caption ?? '');
				break;
			case 'ImageGallery':
				for (const image of node.props.images) lines.push(image.alt, image.caption ?? '');
				break;
			case 'Flow':
				for (const step of node.props.steps) lines.push(step.title, step.description ?? '');
				break;
			case 'Input':
			case 'Textarea':
			case 'DatePicker':
				lines.push(node.props.value);
				break;
			case 'Checkbox':
				lines.push(String(node.props.value));
				break;
			case 'RadioGroup':
			case 'SegmentedControl':
			case 'Select':
				lines.push(
					node.props.options.find((option) => option.value === node.props.value)?.label ??
						node.props.value
				);
				break;
			case 'Tabs':
				lines.push(...node.props.labels);
				break;
			case 'ComparisonCards':
				for (const card of node.props.items) {
					lines.push(card.title, card.description);
					for (const attribute of card.attributes ?? [])
						lines.push(`${attribute.label}: ${attribute.value}`);
				}
				break;
			case 'Explanation':
				lines.push(node.props.content);
				break;
			case 'ParameterSlider':
			case 'NumberInput':
				lines.push(
					`${node.props.value}${node.props.unit ? ` ${node.props.unit}` : ''} (${node.props.min}–${node.props.max})`
				);
				break;
		}
		node.children.forEach(visit);
	}
	visit(spec.root);
	return lines.join('\n');
}

/**
 * Parse a fence body, or a single surrounding ```intelligent-ui fence.
 * This is a bounded patch dialect, not a general JSON Patch interpreter. Both add
 * and replace assign a whole root/element/dataset; no expressions or actions are evaluated.
 * Reparse the accumulated body on every update. Only an unfinished final JSONL
 * record may be buffered; malformed/unsupported data always fails closed.
 */
export function parseIntelligentUI(source: string, done: boolean): ParseResult {
	try {
		requireValid(
			source.length <= INTELLIGENT_UI_LIMITS.bytes &&
				new TextEncoder().encode(source).byteLength <= INTELLIGENT_UI_LIMITS.bytes,
			'The UI payload exceeds 128 KB.'
		);
		let body = source;
		const start = body.trimStart();
		if (
			!done &&
			start.startsWith('`') &&
			('```intelligent-ui'.startsWith(start) || /^```intelligent-ui[\t ]*\r?$/.test(start))
		) {
			return { spec: null, error: null, pending: true, fallback: '' };
		}
		if (/^\s*```/.test(body)) {
			const opening = /^\s*```intelligent-ui[\t ]*\r?\n/.exec(body);
			requireValid(opening !== null, 'Expected an intelligent-ui fence.');
			body = body.slice(opening[0].length);
			const closing = /(?:^|\r?\n)```[\t ]*(?:\r?\n)?[\t\r\n ]*$/.exec(body);
			if (closing) body = body.slice(0, closing.index);
			else requireValid(!done, 'The UI fence is not closed.');
		}
		// Marked includes one/two arriving closing backticks in an open code token.
		// Buffer that framing only while streaming; a finished stray fence still fails.
		if (!done) body = body.replace(/(?:^|\r?\n)`{1,2}$/, '');
		if (!body.trim()) {
			requireValid(!done, 'The UI payload is empty.');
			return { spec: null, error: null, pending: true, fallback: '' };
		}

		let draft: DraftSpec = { root: null, elements: {} };
		const state = jsonState(body);
		if (state === 'complete') {
			const value: unknown = JSON.parse(body);
			if (isObject(value) && own(value, 'op')) apply(value, draft);
			else draft = fullSpec(value);
		} else if (/^\s*\{(?:[\t ]*\r?\n|\s*"(?:root|elements|datasets)"\s*:|\s*$)/.test(body)) {
			// A pretty-printed full-spec fallback must arrive as one complete object.
			requireValid(state === 'incomplete' && !done, 'Invalid or incomplete UI JSON.');
			return { spec: null, error: null, pending: true, fallback: '' };
		} else {
			const lines = body.split('\n');
			let operations = 0;
			for (let index = 0; index < lines.length; index++) {
				const line = lines[index].trim();
				if (!line) continue;
				const state = jsonState(line);
				if (state === 'incomplete' && index === lines.length - 1 && !done) break;
				requireValid(state === 'complete', 'Invalid or incomplete UI JSONL record.');
				requireValid(++operations <= INTELLIGENT_UI_LIMITS.operations, 'Too many UI updates.');
				apply(JSON.parse(line), draft);
			}
		}
		validateGraph(draft, done);
		const spec = reachableSpec(draft);
		return { spec, error: null, pending: !done, fallback: fallbackText(spec) };
	} catch (error) {
		return {
			spec: null,
			error: error instanceof InvalidUI ? error.message : 'Invalid UI data.',
			pending: false,
			fallback: unavailable
		};
	}
}

/** Stable non-cryptographic key for view-local state; never use as a security token. */
export function intelligentUIHash(source: string): string {
	let hash = 2166136261;
	for (let index = 0; index < source.length; index++) {
		hash ^= source.charCodeAt(index);
		hash = Math.imul(hash, 16777619);
	}
	return (hash >>> 0).toString(36);
}
