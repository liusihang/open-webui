import { describe, expect, it } from 'vitest';
import {
	normalizeRichSeries,
	resolveRichSeries,
	pieSlices,
	chartScale,
	validateRichChart
} from './rich-chart';

describe('bounded rich chart geometry', () => {
	it('preserves old single-series inputs and caps aggregate work', () => {
		expect(normalizeRichSeries([{ label: 'A', value: -2 }], undefined, 'Total')[0].name).toBe(
			'Total'
		);
		const series = Array.from({ length: 20 }, (_, index) => ({
			name: String(index),
			data: Array.from({ length: 100 }, (_, value) => ({ label: String(value), value }))
		}));
		expect(normalizeRichSeries([], series).flatMap((item) => item.data)).toHaveLength(128);
	});
	it('uses filtered source rows without inherited fields', () => {
		const rows = [{ category: 'A', value: 3 }, Object.create({ category: 'B', value: 9 })];
		expect(resolveRichSeries(rows, 'category', [{ name: 'Sample', yField: 'value' }])).toEqual([
			{ name: 'Sample', data: [{ label: 'A', value: 3 }] }
		]);
	});
	it('handles full-circle, zero, negative and overflow-scale pie values', () => {
		expect(pieSlices([{ label: 'A', value: 0 }])).toEqual([]);
		expect(
			pieSlices([
				{ label: 'A', value: -1 },
				{ label: 'B', value: 3 }
			])
		).toEqual([]);
		expect(pieSlices([{ label: 'A', value: 2 }])[0].fraction).toBe(1);
		const slices = pieSlices([
			{ label: 'A', value: Number.MAX_VALUE },
			{ label: 'B', value: Number.MAX_VALUE }
		]);
		expect(slices.map((slice) => slice.fraction)).toEqual([0.5, 0.5]);
		expect(slices[0].path).not.toMatch(/NaN|Infinity/);
	});
	it('keeps negative and huge line/bar coordinates finite', () => {
		const scale = chartScale([
			{
				name: 'Sample',
				data: [
					{ label: 'A', value: -Number.MAX_VALUE },
					{ label: 'B', value: Number.MAX_VALUE }
				]
			}
		]);
		expect(scale.y(-Number.MAX_VALUE)).toBe(205);
		expect(scale.y(Number.MAX_VALUE)).toBe(25);
		expect(scale.y(0)).toBe(115);
	});
	it('rejects invalid series combinations, duplicate labels, negative pie and excess points', () => {
		expect(() => validateRichChart({ kind: 'pie', data: [{ label: 'A', value: -1 }] })).toThrow();
		expect(() => validateRichChart({ kind: 'line', donut: true })).toThrow();
		expect(() =>
			validateRichChart({
				kind: 'line',
				series: [
					{
						name: 'A',
						data: [
							{ label: 'X', value: 1 },
							{ label: 'X', value: 2 }
						]
					}
				]
			})
		).toThrow();
		expect(() =>
			validateRichChart({
				kind: 'line',
				series: [
					{
						name: 'A',
						data: Array.from({ length: 129 }, (_, value) => ({ label: String(value), value }))
					}
				]
			})
		).toThrow();
		expect(() =>
			validateRichChart({
				kind: 'line',
				dataSource: 'rows',
				series: [{ name: 'A', yField: 'value' }]
			})
		).not.toThrow();
	});
});
