// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync, type Component } from 'svelte';
import RichChart from './RichChart.svelte';
import MatrixChart from './MatrixChart.svelte';
import {
	colorPair,
	chartColor,
	resolvedAppearance,
	contrastRatio,
	type Appearance
} from '$lib/utils/intelligent-ui/colors';
import { createTestI18n } from './test-i18n';

let component: ReturnType<typeof mount> | undefined;
afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
	document.documentElement.classList.remove('dark');
});
function setup<Props extends Record<string, unknown>>(Component: Component<Props>, props: Props) {
	const target = document.createElement('div');
	document.body.append(target);
	component = mount(Component, {
		target,
		props,
		context: new Map([['i18n', createTestI18n()]])
	});
	flushSync();
	return target;
}
const color = (element: Element | null) => ({
	light: (element as HTMLElement)?.style.getPropertyValue('--chart-light'),
	dark: (element as HTMLElement)?.style.getPropertyValue('--chart-dark')
});
const pair = (input: unknown, identity = '', appearance?: Appearance) =>
	colorPair(
		chartColor(
			input,
			identity,
			'default',
			resolvedAppearance(appearance, { surface: true }).background
		)
	);
const explicit = { light: '#005599', dark: '#77bbff' };

describe('theme-aware native chart colors', () => {
	it('uses the same point/category color in pie fills and legend swatches', () => {
		document.documentElement.classList.add('dark');
		const target = setup(RichChart, {
			view: {
				title: 'Shares',
				kind: 'pie',
				donut: true,
				appearance: { background: { light: '#fafafa', dark: '#202020' } },
				categoryColors: [{ id: 'b', color: '#950' }]
			},
			data: [
				{ id: 'a', label: 'A', value: 3, color: explicit },
				{ id: 'b', label: 'B', value: 1 }
			]
		});
		const slices = [...target.querySelectorAll('[data-chart-point]')];
		const swatches = [...target.querySelectorAll('li .chart-ink')];
		expect(slices.map(color)).toEqual([
			pair(explicit, '', { background: { light: '#fafafa', dark: '#202020' } }),
			pair('#950', '', { background: { light: '#fafafa', dark: '#202020' } })
		]);
		expect(swatches.map(color)).toEqual(slices.map(color));
		expect(target.querySelector('circle[r="48"]')?.classList.contains('chart-donut')).toBe(true);
		expect(
			(target.querySelector('section') as HTMLElement).style.getPropertyValue('--iui-bg-dark')
		).toBe('#202020');
	});

	it.each(['bar', 'line'])(
		'keeps %s series colors consistent when a legend hides and restores another series',
		(kind) => {
			const target = setup(RichChart, {
				view: { title: 'Samples', kind, palette: 'ocean' },
				series: [
					{ id: 'a', name: 'First', color: '#047857', data: [{ label: 'A', value: 1 }] },
					{ id: 'b', name: 'Second', color: explicit, data: [{ label: 'A', value: 2 }] }
				]
			});
			const swatches = [...target.querySelectorAll('button .chart-ink')];
			expect(swatches.map(color)).toEqual([pair('#047857'), pair(explicit)]);
			const marks = () => [...target.querySelectorAll('[data-chart-point] .chart-fill')];
			expect(marks().map(color)).toEqual(swatches.map(color));
			if (kind === 'line')
				expect([...target.querySelectorAll('polyline')].map(color)).toEqual(swatches.map(color));
			const button = target.querySelector('button')!;
			button.click();
			flushSync();
			expect(marks().map(color)).toEqual([pair(explicit)]);
			if (kind === 'line') expect(color(target.querySelector('polyline'))).toEqual(pair(explicit));
			button.click();
			flushSync();
			expect(marks().map(color)).toEqual(swatches.map(color));
		}
	);

	it('honors point and category overrides without changing the line series stroke or legend', () => {
		const target = setup(RichChart, {
			view: { title: 'Samples', kind: 'line', categoryColors: [{ id: 'b', color: '#950' }] },
			series: [
				{
					name: 'First',
					color: '#047857',
					data: [
						{ id: 'a', label: 'A', value: 1, color: explicit },
						{ id: 'b', label: 'B', value: 2 }
					]
				},
				{ name: 'Second', color: '#555', data: [{ label: 'A', value: 2 }] }
			]
		});
		expect(
			[...target.querySelectorAll('[data-chart-point] circle')].slice(0, 2).map(color)
		).toEqual([pair(explicit), pair('#950')]);
		expect(color(target.querySelector('polyline'))).toEqual(pair('#047857'));
		expect(color(target.querySelector('button .chart-ink'))).toEqual(pair('#047857'));
	});

	it('uses stable scatter identity, shows values on focus, and supports arrow keys', () => {
		const target = setup(MatrixChart, {
			view: {
				title: 'Measurements',
				kind: 'scatter',
				categoryColors: [{ id: '[3,4]', color: explicit }]
			},
			data: [
				{ id: 'first', x: 1, y: 2, label: 'First', color: '#950' },
				{ x: 3, y: 4 }
			]
		});
		const points = [...target.querySelectorAll<SVGElement>('[data-chart-point]')];
		expect(points.map(color)).toEqual([pair('#950'), pair(explicit)]);
		points[0].focus();
		flushSync();
		expect(target.querySelector('[data-chart-tooltip]')?.textContent).toBe('First: 1, 2');
		points[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		expect(document.activeElement).toBe(points[1]);
		expect(target.querySelector('[data-chart-tooltip]')?.textContent).toBe('3, 4');
	});

	it('keeps the default heatmap gradient while supporting valid custom cells', () => {
		const target = setup(MatrixChart, {
			view: { title: 'Matrix', kind: 'heatmap' },
			data: [
				{ x: 'A', y: 'Row', value: 1 },
				{ x: 'B', y: 'Row', value: 2, color: explicit },
				{ x: 'C', y: 'Row', value: 3, color: 'url(https://bad.test)' }
			]
		});
		const cells = [...target.querySelectorAll('[data-chart-point]')];
		expect(color(cells[0]).light).toMatch(/^hsl\(245 70% /);
		expect(color(cells[0]).dark).toMatch(/^hsl\(245 70% /);
		expect(color(cells[1])).toEqual(pair(explicit));
		expect(color(cells[2]).light).toMatch(/^hsl\(245 70% /);
		expect(target.innerHTML).not.toContain('url(https://bad.test)');
		cells[1].dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(target.querySelector('[data-chart-tooltip]')?.textContent).toBe('B, Row: 2');
	});

	it.each(['scatter', 'heatmap'] as const)(
		'keeps %s custom colors visible against matching custom backgrounds',
		(kind) => {
			const background = { light: '#ff0000', dark: '#00ff00' };
			const data =
				kind === 'scatter'
					? [{ x: 1, y: 2, color: background }]
					: [
							{ x: 'A', y: 'Row', value: 1, color: background },
							{ x: 'B', y: 'Row', value: 2 }
						];
			const target = setup(MatrixChart, {
				view: { title: 'Contrast', kind, appearance: { background } },
				data
			});
			for (const point of target.querySelectorAll('[data-chart-point]')) {
				const colors = color(point);
				expect(contrastRatio(colors.light, background.light)).toBeGreaterThanOrEqual(3);
				expect(contrastRatio(colors.dark, background.dark)).toBeGreaterThanOrEqual(3);
			}
		}
	);

	it('keeps readable chart axes inside a keyboard-accessible local scroll viewport', () => {
		const target = setup(RichChart, {
			view: { title: 'Categories', kind: 'bar' },
			data: Array.from({ length: 12 }, (_, index) => ({
				label: `Long category ${index}`,
				value: index + 1
			}))
		});
		const viewport = target.querySelector('.chart-viewport');
		expect(viewport?.getAttribute('tabindex')).toBe('0');
		expect(viewport?.querySelector('svg.chart-plot')).not.toBeNull();
		for (const label of target.querySelectorAll('svg text'))
			expect(label.getAttribute('font-size')).toBe('12');
		for (const [index, label] of [...target.querySelectorAll('[data-axis="x"]')].entries())
			expect(label.getAttribute('aria-label')).toBe(`Long category ${index}`);
	});

	it('keeps extreme finite scatter coordinates finite', () => {
		const target = setup(MatrixChart, {
			view: { title: 'Extreme', kind: 'scatter' },
			data: [
				{ x: -Number.MAX_VALUE, y: -Number.MAX_VALUE },
				{ x: Number.MAX_VALUE, y: Number.MAX_VALUE }
			]
		});
		const points = [...target.querySelectorAll('circle')];
		expect(points).toHaveLength(2);
		for (const point of points) {
			expect(Number.isFinite(Number(point.getAttribute('cx')))).toBe(true);
			expect(Number.isFinite(Number(point.getAttribute('cy')))).toBe(true);
		}
	});
});
