import { describe, expect, it } from 'vitest';
import { chartData, type SharedValue } from './data';
import {
	chartColor as rawChartColor,
	resolvedAppearance,
	contrastRatio,
	colorPair
} from './colors';
const chartColor = (
	value: unknown,
	identity: string,
	palette: 'default' | 'ocean' | 'forest' | 'sunset' = 'default'
) =>
	rawChartColor(
		value,
		identity,
		palette,
		resolvedAppearance(undefined, { surface: true }).background
	);
import type { ChartProps, Spec, DatasetRow } from './index';
import {
	chartMetadata,
	chartRowMetadata,
	normalizeRichSeries,
	resolveRichSeries,
	pointIdentity,
	seriesIdentity,
	pointColor,
	seriesColor,
	pieSlices,
	validateRichChart,
	type RichSeries
} from './rich-chart';

const defaults = (_key: string, fallback: SharedValue) => fallback;
const spec = (rows: DatasetRow[] = []): Spec => ({
	root: 'root',
	elements: {},
	datasets: { rows }
});
const themeColor = { light: '#047857', dark: '#6ee7b7' };

describe('semantic chart color identity and metadata', () => {
	it('preserves valid IDs and theme colors through normalization and pie slicing', () => {
		const input: RichSeries[] = [
			{
				id: 'growth',
				name: 'Growth',
				color: '#123',
				data: [{ id: 'stable', label: 'Changed label', value: 5, color: themeColor }]
			}
		];
		expect(normalizeRichSeries([], input)).toEqual(input);
		expect(pieSlices(input[0].data)[0]).toMatchObject(input[0].data[0]);
		expect(pointIdentity(input[0].data[0])).toBe('stable');
		expect(seriesIdentity(input[0])).toBe('growth');
	});

	it('ignores inherited metadata and unsafe or incomplete color values', () => {
		expect(chartMetadata(Object.create({ id: 'inherited', color: '#f00' }))).toEqual({});
		for (const color of [
			'red',
			'url(https://bad.test)',
			'#abcd',
			{ light: '#123' },
			{ light: '#123', dark: '#456', extra: 'color:red' }
		]) {
			expect(chartMetadata({ id: 'row-1', color })).toEqual({ id: 'row-1' });
		}
		expect(
			chartRowMetadata(Object.create({ key: 'inherited', tint: '#f00' }), {
				idField: 'key',
				colorField: 'tint'
			})
		).toEqual({});
		expect(
			chartRowMetadata({ key: 0, tint: '#123' }, { idField: 'key', colorField: 'tint' })
		).toEqual({ id: '0', color: '#123' });
	});

	it('uses explicit point, category, series, then deterministic palette colors', () => {
		const series: RichSeries = { id: 'stable-series', name: 'Series', color: '#005599', data: [] };
		const options = { palette: 'forest' as const, categoryColors: [{ id: 'A', color: '#456' }] };
		expect(pointColor({ id: 'A', color: '#123' }, options, series)).toEqual(
			chartColor('#123', 'A')
		);
		expect(pointColor({ id: 'A' }, options, series)).toEqual(chartColor('#456', 'A'));
		expect(pointColor({ id: 'B' }, options, series)).toEqual(seriesColor(series, options));
		expect(pointColor({ id: 'B' }, options)).toEqual(chartColor(undefined, 'B', 'forest'));
		expect(pointColor({ label: 'A' }, options)).toEqual(chartColor('#456', 'A'));
		expect(pointIdentity({ x: 2, y: 3 })).toBe('[2,3]');
		expect(
			pointColor({ x: 2, y: 3 }, { categoryColors: [{ id: '[2,3]', color: themeColor }] })
		).toEqual(chartColor(themeColor, '[2,3]'));
	});

	it('keeps category and series colors across filtering, reorder, and label changes with stable IDs', () => {
		const points = [
			{ id: 'one', label: 'One', value: 1 },
			{ id: 'two', label: 'Two', value: 2 }
		];
		const colors = new Map(points.map((point) => [point.id, pointColor(point)]));
		for (const point of [...points].reverse().slice(0, 1))
			expect(pointColor(point)).toEqual(colors.get(point.id));
		expect(pointColor({ ...points[0], label: 'Renamed' })).toEqual(colors.get('one'));
		const before: RichSeries = { id: 'series-key', name: 'Old name', data: points };
		expect(seriesColor({ ...before, name: 'New name' })).toEqual(seriesColor(before));
		expect(pointColor(points[0], {}, before)).toEqual(seriesColor(before));
	});

	it('uses appearance palettes unless the chart explicitly selects another palette', () => {
		const point = { id: 'stable-category' };
		const appearance = { palette: 'forest' as const };
		const background = resolvedAppearance(appearance, { surface: true }).background;
		expect(pointColor(point, { appearance })).toEqual(
			rawChartColor(undefined, point.id, 'forest', background)
		);
		expect(pointColor(point, { appearance, palette: 'sunset' })).toEqual(
			rawChartColor(undefined, point.id, 'sunset', background)
		);
	});

	it('keeps custom marks readable against the actual chart surface', () => {
		const appearance = { background: { light: '#ff0000', dark: '#00ff00' } };
		const point = { id: 'A', color: appearance.background };
		const pair = colorPair(pointColor(point, { appearance }));
		expect(contrastRatio(pair.light, appearance.background.light)).toBeGreaterThanOrEqual(3);
		expect(contrastRatio(pair.dark, appearance.background.dark)).toBeGreaterThanOrEqual(3);
		const series: RichSeries = { id: 'A', name: 'Series', color: point.color, data: [] };
		expect(seriesColor(series, { appearance })).toEqual(pointColor(point, { appearance }));
	});

	it('retains point metadata for all inline chart kinds', () => {
		for (const [kind, point] of [
			['bar', { label: 'A', value: 2 }],
			['line', { label: 'A', value: 2 }],
			['pie', { label: 'A', value: 2 }],
			['scatter', { x: 1, y: 2, label: 'A' }],
			['heatmap', { x: 'A', y: 'B', value: 2 }]
		] as const) {
			const data = [{ ...point, id: 'key', color: themeColor }];
			expect(chartData(spec(), { title: 'Chart', kind, data } as ChartProps, defaults)).toEqual(
				data
			);
		}
	});

	it('maps dataset IDs and colors for bar, scatter, heatmap, and per-series fields', () => {
		const source = spec([
			{ key: 7, label: 'A', x: 1, y: 2, tint: '#123', otherTint: '#456', score: 3 },
			{ key: 'bad-color', label: 'B', x: 3, y: 4, tint: 'red', score: 5 }
		]);
		const common = { title: 'Chart', dataSource: 'rows', idField: 'key', colorField: 'tint' };
		expect(
			chartData(source, { ...common, kind: 'bar', xField: 'label', yField: 'score' }, defaults)
		).toEqual([
			{ id: '7', label: 'A', value: 3, color: '#123' },
			{ id: 'bad-color', label: 'B', value: 5 }
		]);
		expect(
			chartData(source, { ...common, kind: 'scatter', xField: 'x', yField: 'y' }, defaults)[0]
		).toEqual({ id: '7', x: 1, y: 2, color: '#123' });
		expect(
			chartData(
				source,
				{ ...common, kind: 'heatmap', xField: 'label', yField: 'y', valueField: 'score' },
				defaults
			)[0]
		).toEqual({ id: '7', x: 'A', y: '2', value: 3, color: '#123' });
		const resolved = resolveRichSeries(
			source.datasets!.rows,
			'label',
			[
				{ id: 'series', name: 'Score', yField: 'score', color: themeColor, colorField: 'otherTint' }
			],
			common
		);
		expect(resolved[0]).toMatchObject({ id: 'series', color: themeColor });
		expect(resolved[0].data).toEqual([
			{ label: 'A', value: 3, id: '7', color: '#456' },
			{ label: 'B', value: 5, id: 'bad-color' }
		]);
	});

	it('validates series IDs/colors and rejects duplicate point and series identities', () => {
		const valid = {
			kind: 'line',
			series: [
				{
					id: 'first',
					name: 'First',
					color: themeColor,
					data: [{ id: 'one', label: 'One', value: 1, color: '#123' }]
				}
			]
		};
		expect(() => validateRichChart(valid)).not.toThrow();
		expect(() =>
			validateRichChart({ ...valid, series: [{ ...valid.series[0], color: 'red' }] })
		).toThrow();
		expect(() =>
			validateRichChart({
				...valid,
				series: [valid.series[0], { ...valid.series[0], name: 'Second' }]
			})
		).toThrow();
		expect(() =>
			validateRichChart({
				...valid,
				series: [
					{
						...valid.series[0],
						data: [
							{ id: 'one', label: 'One', value: 1 },
							{ id: 'one', label: 'Two', value: 2 }
						]
					}
				]
			})
		).toThrow();
	});
});
