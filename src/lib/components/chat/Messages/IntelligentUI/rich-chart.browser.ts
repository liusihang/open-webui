// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import RichChart from './RichChart.svelte';
import { createTestI18n } from './test-i18n';
let component: ReturnType<typeof mount> | undefined;
afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});
function setup(props: any) {
	const target = document.createElement('div');
	document.body.append(target);
	component = mount(RichChart, { target, props, context: new Map([['i18n', createTestI18n()]]) });
	flushSync();
	return target;
}
const series = [
	{
		name: 'First',
		data: [
			{ label: 'A', value: 12 },
			{ label: 'B', value: -3 }
		]
	},
	{
		name: 'Second',
		data: [
			{ label: 'A', value: 8 },
			{ label: 'B', value: 6 }
		]
	}
];
describe('interactive chart controls', () => {
	it.each(['bar', 'line'])(
		'%s has hover/focus values, arrow navigation, legend toggles and table fallback',
		(kind) => {
			const target = setup({ view: { title: 'Samples', kind }, series });
			const points = target.querySelectorAll<SVGElement>('[data-chart-point]');
			expect(points).toHaveLength(4);
			points[0].dispatchEvent(new Event('pointerenter'));
			flushSync();
			expect(target.querySelector('[data-chart-tooltip]')?.textContent).toContain('First, A: 12');
			points[0].focus();
			points[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
			flushSync();
			expect(document.activeElement).toBe(points[1]);
			expect(target.querySelector('[data-chart-tooltip]')?.textContent).toContain('First, B: -3');
			points[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
			flushSync();
			expect(target.querySelector('[data-chart-tooltip]')?.textContent).toBe('');
			const button = target.querySelector('button')!;
			button.click();
			flushSync();
			expect(button.getAttribute('aria-pressed')).toBe('false');
			expect(target.querySelectorAll('[data-chart-point]')).toHaveLength(2);
			expect(target.querySelectorAll('tbody tr')).toHaveLength(4);
			button.click();
			flushSync();
			expect(target.querySelectorAll('[data-chart-point]')).toHaveLength(4);
		}
	);
	it('keeps repeated single-series labels distinct and supports keyboard activation', () => {
		const target = setup({
			view: { title: 'Samples', kind: 'bar' },
			data: [
				{ label: 'A', value: 1 },
				{ label: 'A', value: 2 }
			]
		});
		const points = target.querySelectorAll<SVGElement>('[data-chart-point]');
		expect(points[0].querySelector('rect')?.getAttribute('x')).not.toBe(
			points[1].querySelector('rect')?.getAttribute('x')
		);
		points[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		expect(target.querySelector('[role="tooltip"]')?.textContent).toBe('A: 1');
		expect(points[0].getAttribute('aria-describedby')).toBe(
			target.querySelector('[role="tooltip"]')?.id
		);
	});
	it('renders donut slices with accessible percentages and zero values in the table', () => {
		const target = setup({
			view: { title: 'Shares', kind: 'pie', donut: true },
			data: [
				{ label: 'A', value: 3 },
				{ label: 'B', value: 1 },
				{ label: 'C', value: 0 }
			]
		});
		expect(target.querySelectorAll('[data-chart-point]')).toHaveLength(2);
		expect(target.querySelector('[data-chart-point]')?.getAttribute('aria-label')).toBe(
			'A: 3 (75%)'
		);
		expect(target.querySelector('circle[r="48"]')).not.toBeNull();
		expect(target.querySelectorAll('tbody tr')).toHaveLength(3);
	});
	it.each([0, -2])('shows empty state instead of invalid pie geometry for %s', (value) => {
		const target = setup({ view: { title: 'Shares', kind: 'pie' }, data: [{ label: 'A', value }] });
		expect(target.querySelector('svg')).toBeNull();
		expect(target.textContent).toContain('No matching chart data');
	});
});
