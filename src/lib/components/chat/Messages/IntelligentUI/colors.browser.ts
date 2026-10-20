// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TestHarness from './TestHarness.svelte';
import RichChart from './RichChart.svelte';
import { createTestI18n } from './test-i18n';
import { parseIntelligentUI } from '$lib/utils/intelligent-ui';
import {
	fixtureSource,
	contrastSource
} from '../../../../../../tests/intelligent-ui/colors-fixture/spec';
let component: ReturnType<typeof mount> | undefined;
afterEach(async () => {
	if (component) await unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});
function setup(source = fixtureSource) {
	const target = document.createElement('div');
	document.body.append(target);
	component = mount(TestHarness, { target, props: { initialSource: source } });
	flushSync();
	return target.querySelector<HTMLElement>('[data-intelligent-ui="first"]')!;
}
describe('native flexible colors through the real parser and catalog', () => {
	it('accepts the complete fixture and the adversarial local-surface fixture', () => {
		expect(parseIntelligentUI(fixtureSource, true).error).toBeNull();
		expect(parseIntelligentUI(contrastSource, true).error).toBeNull();
		const root = setup();
		expect(root.querySelectorAll('[data-chart-point]')).toHaveLength(6);
		expect(root.querySelectorAll('[data-metric]')).toHaveLength(3);
	});
	it('renders colored bold spans and updates their bindings via native actions', async () => {
		const root = setup();
		const span = [...root.querySelectorAll('span')].find(
			(node) => node.textContent === 'Clean energy '
		)!;
		expect(span).toBeDefined();
		expect(span.classList.contains('font-semibold') || span.classList.contains('font-bold')).toBe(
			true
		);
		expect(span.style.getPropertyValue('--iui-fg-light')).not.toBe('');
		expect(root.textContent).toContain('75%');
		[...root.querySelectorAll('button')]
			.find((node) => node.textContent === 'Set target to 85%')!
			.click();
		await tick();
		expect(root.textContent).toContain('85%');
		expect(root.textContent).not.toContain('{{target}}');
	});
	it('retains table cell > row > column > table styling after sorting and search', async () => {
		const root = setup();
		const table = root.querySelector<HTMLElement>('[data-table="operations"]')!;
		const cell = () =>
			table.querySelector<HTMLElement>('[data-row-id="porto"][data-column="status"]')!;
		expect(cell().style.getPropertyValue('--iui-bg-light')).toBe('#ffdfcc');
		expect(cell().style.getPropertyValue('--iui-bg-dark')).toBe('#6b3321');
		expect(
			table
				.querySelector<HTMLElement>('[data-row-id="porto"][data-column="clean"]')!
				.style.getPropertyValue('--iui-bg-light')
		).toBe('#fff5ec');
		expect(
			table
				.querySelector<HTMLElement>('[data-row-id="oslo"][data-column="clean"]')!
				.style.getPropertyValue('--iui-bg-light')
		).toBe('#edf8f1');
		[...table.querySelectorAll('button')]
			.find((node) => node.textContent?.trim().startsWith('Site'))!
			.click();
		await tick();
		expect(cell().style.getPropertyValue('--iui-bg-light')).toBe('#ffdfcc');
		const input = table.querySelector<HTMLInputElement>('input[type="search"]')!;
		input.value = 'Porto';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(table.querySelectorAll('tbody tr')).toHaveLength(1);
		expect(cell().style.getPropertyValue('--iui-bg-dark')).toBe('#6b3321');
		expect(table.querySelector('input[type="checkbox"]')).toBeNull();
	});
	it('keeps explicit series colors when a peer is hidden and restored', () => {
		const target = document.createElement('div');
		document.body.append(target);
		component = mount(RichChart, {
			target,
			context: new Map([['i18n', createTestI18n()]]),
			props: {
				view: { title: 'Stable lines', kind: 'line' },
				series: [
					{
						id: 'north',
						name: 'North',
						color: { light: '#124578', dark: '#8BC3F2' },
						data: [{ label: 'Week 1', value: 4 }]
					},
					{
						id: 'south',
						name: 'South',
						color: { light: '#954218', dark: '#FAC095' },
						data: [{ label: 'Week 1', value: 3 }]
					}
				]
			}
		});
		flushSync();
		const lines = () => target.querySelectorAll<SVGPolylineElement>('polyline');
		const south = lines()[1].getAttribute('style');
		target.querySelector<HTMLButtonElement>('button')!.click();
		flushSync();
		expect(lines()).toHaveLength(1);
		expect(lines()[0].getAttribute('style')).toBe(south);
		target.querySelector<HTMLButtonElement>('button')!.click();
		flushSync();
		expect(lines()).toHaveLength(2);
		expect(lines()[1].getAttribute('style')).toBe(south);
	});
});
