import { describe, expect, it } from 'vitest';
import { categoricalChartLayout, chartTextWidth, wrapChartLabel } from './chart-layout';

describe('chart label layout', () => {
	it.each([
		'Microorganism response',
		'单细胞分析与动力学参数',
		'VeryLongUnbrokenIdentifier1234567890',
		'A mixed 中英文 label'
	])('wraps %s without removing content', (label) => {
		const lines = wrapChartLabel(label, 56);
		expect(lines.join('').replace(/\s/g, '')).toBe(label.replace(/\s/g, ''));
		lines.forEach((line) => expect(chartTextWidth(line)).toBeLessThanOrEqual(56));
	});
	it.each([320, 600, 1200])(
		'fits complete category labels inside allocated bounds at %i px',
		(viewport) => {
			const labels = Array.from({ length: 24 }, (_, i) => `完整的分类名称 ${i + 1}`);
			const layout = categoricalChartLayout(labels, viewport);
			expect(layout.width).toBeGreaterThanOrEqual(viewport);
			labels.forEach((label, i) => {
				const extent = chartTextWidth(label) * Math.SQRT1_2;
				expect(layout.left + (i + 0.5) * layout.columnWidth - extent).toBeGreaterThanOrEqual(12);
				expect(layout.bottom + 20 + extent + 4).toBeLessThan(layout.height);
			});
		}
	);
	it('grows heatmap rows and left margin for full Y labels', () => {
		const labels = Array.from({ length: 12 }, (_, i) => `很长的行名称，包含完整说明和编号 ${i}`);
		const layout = categoricalChartLayout(['Column'], 480, labels);
		expect(layout.yLines).toHaveLength(12);
		expect(layout.plotHeight / labels.length).toBeGreaterThanOrEqual(
			Math.max(...layout.yLines.map((lines) => lines.length)) * 16 + 8
		);
	});
});
