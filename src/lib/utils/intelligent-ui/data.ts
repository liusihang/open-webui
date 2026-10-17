import { extendedSharedValue } from './extended';
import type {
	ChartProps,
	DataTableProps,
	DatasetCell,
	DatasetRow,
	Filter,
	MetricCardProps,
	Spec
} from './index';

export type SharedValue = string | number | boolean;
export type SharedStateReader = (key: string, fallback: SharedValue) => SharedValue;
export type BarLinePoint = { label: string; value: number };
export type ScatterPoint = { x: number; y: number; label?: string };
export type HeatmapPoint = { x: string; y: string; value: number };
export type ChartData = BarLinePoint[] | ScatterPoint[] | HeatmapPoint[];
export type TableSort = { field: string; direction: 'asc' | 'desc' };

// Keep work bounded even if a caller bypasses the protocol parser. These helpers
// only derive local views; they never mutate a spec or perform external actions.
const MAX_ROWS = 256;
const MAX_POINTS = 128;
const MAX_ELEMENTS = 64;
const MAX_FILTERS = 16;
const MAX_COLUMNS = 32;
const MAX_OPTIONS = 32;
const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object, key);
const finite = (value: unknown): value is number =>
	typeof value === 'number' && Number.isFinite(value);
const cell = (row: DatasetRow, field: string): DatasetCell | undefined =>
	own(row, field) ? row[field] : undefined;

function datasetRows(spec: Spec, dataSource: string): DatasetRow[] {
	if (!spec.datasets || !own(spec.datasets, dataSource)) return [];
	const rows = spec.datasets[dataSource];
	return Array.isArray(rows) ? rows.slice(0, MAX_ROWS) : [];
}

function numericValue(value: number, min: number, max: number, step: number): number {
	const bounded = Math.min(max, Math.max(min, value));
	const range = (max - min) / step;
	const maxSteps = Math.floor(range + Number.EPSILON * Math.max(1, Math.abs(range)) * 4);
	const steps = Math.min(maxSteps, Math.round((bounded - min) / step));
	// Remove floating-point residue such as 0.30000000000000004 without widening
	// the declared range or discarding precision from a small declared step.
	const decimals = (number: number) => {
		const [coefficient, exponent = '0'] = String(number).split('e');
		return Math.max(0, (coefficient.split('.')[1]?.length ?? 0) - Number(exponent));
	};
	const precision = Math.max(decimals(min), decimals(step));
	const raw = min + steps * step;
	const quantized = precision <= 100 ? Number(raw.toFixed(precision)) : raw;
	return Math.min(max, Math.max(min, quantized));
}

/** Resolve only state declared by a current control or selectable table. */
export function resolveSharedValue(
	spec: Spec,
	stateKey: string,
	read: SharedStateReader
): SharedValue | undefined {
	for (const element of Object.values(spec.elements).slice(0, MAX_ELEMENTS)) {
		const extended = extendedSharedValue(element, stateKey, read);
		if (extended !== undefined) return extended;
		if (element.type === 'Select' && element.props.stateKey === stateKey) {
			const { value, options } = element.props;
			const saved = read(`shared:${stateKey}`, value);
			return typeof saved === 'string' &&
				options.slice(0, MAX_OPTIONS).some((option) => option.value === saved)
				? saved
				: value;
		}
		if (
			(element.type === 'NumberInput' || element.type === 'ParameterSlider') &&
			element.props.stateKey === stateKey
		) {
			const { value, min, max, step } = element.props;
			if (
				![value, min, max, step].every(finite) ||
				min >= max ||
				step <= 0 ||
				step > max - min ||
				!Number.isFinite((max - min) / step)
			)
				return undefined;
			const saved = read(`shared:${stateKey}`, value);
			return numericValue(finite(saved) ? saved : value, min, max, step);
		}
		if (
			element.type === 'DataTable' &&
			element.props.selectable === true &&
			element.props.selectionKey === stateKey
		) {
			const { dataSource, rowKey } = element.props;
			const saved = read(`shared:${stateKey}`, '');
			if (saved === '' || typeof saved !== 'string' || !rowKey) return '';
			return datasetRows(spec, dataSource).some((row) => {
				const key = cell(row, rowKey);
				return key !== null && key !== undefined && String(key) === saved;
			})
				? saved
				: '';
		}
	}
	return undefined;
}

/** Unresolved controls are skipped so a partial stream remains useful. */
export function filteredRows(
	spec: Spec,
	dataSource: string,
	filters: Filter[] | undefined,
	read: SharedStateReader
): DatasetRow[] {
	const active = (filters ?? []).slice(0, MAX_FILTERS).flatMap((filter) => {
		const value = resolveSharedValue(spec, filter.stateKey, read);
		if (
			value === undefined ||
			(filter.op === 'eq' && filter.ignoreValue !== undefined && value === filter.ignoreValue)
		)
			return [];
		return [{ filter, value }];
	});
	return datasetRows(spec, dataSource).filter((row) =>
		active.every(({ filter, value }) => {
			const current = cell(row, filter.field);
			if (current === undefined) return false;
			switch (filter.op) {
				case 'eq':
					return current !== null && String(current) === String(value);
				case 'gte':
					return finite(current) && finite(value) && current >= value;
				case 'lte':
					return finite(current) && finite(value) && current <= value;
				case 'contains':
					return (
						typeof current === 'string' &&
						typeof value === 'string' &&
						current.toLowerCase().includes(value.toLowerCase())
					);
			}
		})
	);
}

function uniqueHeatmap(points: HeatmapPoint[]): HeatmapPoint[] {
	const coordinates = new Set<string>();
	for (const point of points) {
		const key = JSON.stringify([point.x, point.y]);
		if (coordinates.has(key)) return [];
		coordinates.add(key);
	}
	return points;
}

/** Validate and transform bounded source rows before applying a display-only cap. */
function matchingChartData(spec: Spec, props: ChartProps, read: SharedStateReader): ChartData {
	if (props.dataSource === undefined) {
		const points = props.data.slice(0, MAX_ROWS);
		switch (props.kind) {
			case 'bar':
			case 'line':
			case 'pie':
				return (points as BarLinePoint[])
					.filter((point) => typeof point.label === 'string' && finite(point.value))
					.map((point) => ({ label: point.label, value: point.value }));
			case 'scatter':
				return (points as ScatterPoint[])
					.filter((point) => finite(point.x) && finite(point.y))
					.map(({ x, y, label }) => ({ x, y, ...(label === undefined ? {} : { label }) }));
			case 'heatmap':
				return uniqueHeatmap(
					(points as HeatmapPoint[])
						.filter(
							(point) =>
								typeof point.x === 'string' && typeof point.y === 'string' && finite(point.value)
						)
						.map(({ x, y, value }) => ({ x, y, value }))
				);
		}
	}
	const rows = filteredRows(spec, props.dataSource, props.filters, read);
	if (props.kind === 'bar' || props.kind === 'line' || props.kind === 'pie') {
		return rows.flatMap((row): BarLinePoint[] => {
			const label = cell(row, props.xField);
			const value = cell(row, props.yField);
			return label !== null && label !== undefined && finite(value)
				? [{ label: String(label), value }]
				: [];
		});
	}
	if (props.kind === 'scatter') {
		return rows.flatMap((row): ScatterPoint[] => {
			const x = cell(row, props.xField);
			const y = cell(row, props.yField);
			return finite(x) && finite(y) ? [{ x, y }] : [];
		});
	}
	return uniqueHeatmap(
		rows.flatMap((row): HeatmapPoint[] => {
			const x = cell(row, props.xField);
			const y = cell(row, props.yField);
			const value = props.valueField ? cell(row, props.valueField) : undefined;
			return x !== null && x !== undefined && y !== null && y !== undefined && finite(value)
				? [{ x: String(x), y: String(y), value }]
				: [];
		})
	);
}

/** Convert static or filtered dataset values into the renderer's small point formats. */
export function chartData(spec: Spec, props: ChartProps, read: SharedStateReader): ChartData {
	return matchingChartData(spec, props, read).slice(0, MAX_POINTS) as ChartData;
}

/** Count the same valid points before the display cap so truncation stays visible. */
export function chartPointCount(spec: Spec, props: ChartProps, read: SharedStateReader): number {
	return matchingChartData(spec, props, read).length;
}

/** Only a fixed aggregation vocabulary is supported; expressions are never evaluated. */
export function metricValue(
	spec: Spec,
	props: MetricCardProps,
	read: SharedStateReader
): number | null {
	if (props.dataSource === undefined) return finite(props.value) ? props.value : null;
	const rows = filteredRows(spec, props.dataSource, props.filters, read);
	if (props.aggregation === 'count') return rows.length;
	const values = rows
		.map((row) => (props.field ? cell(row, props.field) : undefined))
		.filter(finite);
	if (!values.length) return null;
	let result: number;
	switch (props.aggregation) {
		case 'sum':
			result = values.reduce((sum, value) => sum + value, 0);
			break;
		case 'mean':
			result = values.reduce((mean, value) => mean + value / values.length, 0);
			break;
		case 'min':
			result = Math.min(...values);
			break;
		case 'max':
			result = Math.max(...values);
			break;
		default:
			return null;
	}
	return finite(result) ? result : null;
}

function compareCells(left: DatasetCell | undefined, right: DatasetCell | undefined): number {
	if (typeof left === 'number' && typeof right === 'number') return left - right;
	// Keep a total ordering for mixed columns, rather than sometimes comparing
	// numbers arithmetically and sometimes lexically, which is non-transitive.
	const rank = (value: DatasetCell | undefined) =>
		typeof value === 'number' ? 0 : typeof value === 'string' ? 1 : 2;
	if (rank(left) !== rank(right)) return rank(left) - rank(right);
	const a = String(left).toLowerCase();
	const b = String(right).toLowerCase();
	return a < b ? -1 : a > b ? 1 : 0;
}

/** Search declared columns and stable-sort a fresh array, keeping absent cells last. */
export function tableRows(
	spec: Spec,
	props: DataTableProps,
	read: SharedStateReader,
	search = '',
	sort?: TableSort
): DatasetRow[] {
	const columns = props.columns.slice(0, MAX_COLUMNS);
	const term = search.slice(0, 2048).trim().toLowerCase();
	const rows = filteredRows(spec, props.dataSource, props.filters, read).filter(
		(row) =>
			!term ||
			columns.some(({ key }) => {
				const value = cell(row, key);
				return value !== undefined && value !== null && String(value).toLowerCase().includes(term);
			})
	);
	if (!sort || !columns.some(({ key }) => key === sort.field)) return rows;
	const absent = (value: DatasetCell | undefined) =>
		value === null || value === undefined || (typeof value === 'number' && !finite(value));
	return rows
		.map((row, index) => ({ row, index }))
		.sort((a, b) => {
			const left = cell(a.row, sort.field);
			const right = cell(b.row, sort.field);
			if (absent(left) || absent(right))
				return absent(left) === absent(right) ? a.index - b.index : absent(left) ? 1 : -1;
			const comparison = compareCells(left, right);
			return (sort.direction === 'desc' ? -comparison : comparison) || a.index - b.index;
		})
		.map(({ row }) => row);
}
