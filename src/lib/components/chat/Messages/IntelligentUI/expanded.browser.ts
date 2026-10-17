// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TestHarness from './TestHarness.svelte';
import { parseIntelligentUI } from '$lib/utils/intelligent-ui';

const node = (type: string, props: Record<string, unknown>, children: string[] = []) => ({
	type,
	props,
	children
});
const filters = [
	{ field: 'region', op: 'eq', stateKey: 'region', ignoreValue: 'all' },
	{ field: 'price', op: 'gte', stateKey: 'minimum' }
];
const selectedFilter = { field: 'id', op: 'eq', stateKey: 'selected', ignoreValue: '' };
const linkedSpec = {
	root: 'root',
	datasets: {
		products: [
			{ id: 'a', region: 'East', price: 10, score: 100 },
			{ id: 'b', region: 'West', price: 20, score: 80 },
			{ id: 'c', region: 'East', price: 30, score: 90 }
		]
	},
	elements: {
		root: node('Stack', { title: 'Linked analysis' }, ['controls', 'table', 'chart', 'total']),
		controls: node('Grid', { columns: 3 }, ['region', 'minimum', 'slider']),
		region: node('Select', {
			title: 'Region',
			stateKey: 'region',
			value: 'all',
			options: [
				{ label: 'All regions', value: 'all' },
				{ label: 'East', value: 'East' },
				{ label: 'West', value: 'West' }
			]
		}),
		minimum: node('NumberInput', {
			title: 'Minimum price',
			stateKey: 'minimum',
			min: 0,
			max: 50,
			step: 5,
			value: 0
		}),
		slider: node('ParameterSlider', {
			title: 'Price filter',
			stateKey: 'minimum',
			min: 0,
			max: 50,
			step: 5,
			value: 0
		}),
		table: node('DataTable', {
			title: 'Products',
			dataSource: 'products',
			columns: [
				{ key: 'id', label: 'Product' },
				{ key: 'region', label: 'Region' },
				{ key: 'price', label: 'Price' }
			],
			filters,
			searchable: true,
			sortable: true,
			selectable: true,
			rowKey: 'id',
			selectionKey: 'selected'
		}),
		chart: node('Chart', {
			title: 'Product prices',
			kind: 'bar',
			dataSource: 'products',
			xField: 'id',
			yField: 'price',
			filters: [...filters, selectedFilter]
		}),
		total: node('MetricCard', {
			title: 'Total price',
			dataSource: 'products',
			aggregation: 'sum',
			field: 'price',
			filters: [...filters, selectedFilter]
		})
	}
};
function stream(spec: any) {
	return [
		...Object.entries(spec.datasets ?? {}).map(([key, value]) => ({
			op: 'add',
			path: `/datasets/${key}`,
			value
		})),
		{ op: 'add', path: '/root', value: spec.root },
		...Object.entries(spec.elements).map(([key, value]) => ({
			op: 'add',
			path: `/elements/${key}`,
			value
		}))
	]
		.map((operation) => JSON.stringify(operation))
		.join('\n');
}
let instance: ReturnType<typeof mount> | undefined;
afterEach(async () => {
	if (instance) await unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});
function setup(initialSource: string, other: Record<string, unknown> = {}) {
	expect(parseIntelligentUI(initialSource, true).error).toBeNull();
	const target = document.createElement('div');
	document.body.append(target);
	instance = mount(TestHarness, { target, props: { initialSource, ...other } });
	flushSync();
	return target.querySelector('[data-intelligent-ui="first"]') as HTMLElement;
}
const total = (root: HTMLElement) =>
	root.querySelector('[data-metric="total"] p')?.textContent?.trim();
const tableCount = (root: HTMLElement) =>
	root.querySelectorAll('[data-table="table"] tbody tr').length;
const barCount = (root: HTMLElement) => root.querySelectorAll('svg rect').length;

describe('shared local data views', () => {
	it('excludes null categories from linked table, chart and metric equality filters', () => {
		const spec: any = structuredClone(linkedSpec);
		spec.datasets.products = [
			{ id: 'missing', region: null, price: 100, score: 1 },
			{ id: 'literal', region: 'null', price: 20, score: 2 }
		];
		spec.elements.region.props.value = 'null';
		spec.elements.region.props.options = [
			{ label: 'All regions', value: 'all' },
			{ label: 'Literal null', value: 'null' }
		];
		const root = setup(stream(spec));
		expect(tableCount(root)).toBe(1);
		expect(barCount(root)).toBe(1);
		expect(total(root)).toBe('20');
		expect(root.querySelector('[data-table] tbody')?.textContent).not.toContain('100');
	});
	it('uses Chinese system copy and accessible labels without translating model data', async () => {
		const spec: any = structuredClone(linkedSpec);
		spec.elements.slider.props.followUp = 'Keep this model-generated question.';
		const root = setup(stream(spec), {
			locale: 'zh-CN',
			onFollowUp: async () => {
				throw new Error('Fixture failure');
			}
		});
		expect(root.textContent).toContain('3 行');
		expect(root.querySelector('input[type="search"]')?.getAttribute('placeholder')).toBe(
			'搜索表格…'
		);
		expect(root.querySelector('input[type="radio"]')?.getAttribute('aria-label')).toBe(
			'选择 Products 中的第 1 行'
		);
		expect(root.textContent).toContain('Products');
		expect(root.textContent).toContain('追问：');
		const action = [...root.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('用此值向模型提问')
		)!;
		action.click();
		await vi.waitFor(() => expect(root.textContent).toContain('追问发送失败，请重试。'));
		expect(root.textContent).toContain('Keep this model-generated question.');
	});
	it('links selection and numeric controls to table, chart and metric without model calls', async () => {
		const onFollowUp = vi.fn();
		const onWrite = vi.fn();
		const root = setup(stream(linkedSpec), { onFollowUp, onWrite });
		expect(tableCount(root)).toBe(3);
		expect(barCount(root)).toBe(3);
		expect(total(root)).toBe('60');
		const select = root.querySelector('select')!;
		select.value = 'East';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(tableCount(root)).toBe(2);
		expect(barCount(root)).toBe(2);
		expect(total(root)).toBe('40');
		(root.querySelector('input[type="radio"]') as HTMLInputElement).click();
		await tick();
		expect(tableCount(root)).toBe(2);
		expect(barCount(root)).toBe(1);
		expect(total(root)).toBe('10');
		[...root.querySelectorAll('button')]
			.find((button) => button.textContent === 'Clear selection')!
			.click();
		await tick();
		expect(barCount(root)).toBe(2);
		expect(total(root)).toBe('40');
		const input = root.querySelector('input[type="number"]') as HTMLInputElement;
		input.value = '2';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(input.value).toBe('2');
		expect(total(root)).toBe('40');
		input.value = '25';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect((root.querySelector('input[type="range"]') as HTMLInputElement).value).toBe('25');
		expect(tableCount(root)).toBe(1);
		expect(total(root)).toBe('30');
		const slider = root.querySelector('input[type="range"]') as HTMLInputElement;
		slider.value = '0';
		slider.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(input.value).toBe('0');
		expect(tableCount(root)).toBe(2);
		expect(onFollowUp).not.toHaveBeenCalled();
		expect(onWrite).toHaveBeenCalled();
	});
	it('keeps a numeric draft stable across sequential keystrokes until commit', async () => {
		const root = setup(stream(linkedSpec), { saved: { first: { 'shared:minimum': 5 } } });
		const input = root.querySelector('input[type="number"]') as HTMLInputElement;
		const slider = root.querySelector('input[type="range"]') as HTMLInputElement;
		expect(input.value).toBe('5');
		input.value = '2';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(input.value).toBe('2');
		expect(slider.value).toBe('5');
		input.value = '25';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(input.value).toBe('25');
		expect(slider.value).toBe('5');
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(slider.value).toBe('25');
		expect(total(root)).toBe('30');
	});
	it('discloses capped charts while metrics retain all matching rows', () => {
		const spec = structuredClone(linkedSpec);
		spec.datasets.products = Array.from({ length: 256 }, (_, index) => ({
			id: `item${index}`,
			region: 'East',
			price: 10,
			score: index
		}));
		const root = setup(stream(spec));
		expect(tableCount(root)).toBe(256);
		expect(barCount(root)).toBe(128);
		expect(total(root)).toBe('2,560');
		expect(root.textContent).toContain('Showing first 128 of 256 matching points.');
	});
	it('searches, sorts and selects table rows with keyboard-native controls', async () => {
		const root = setup(stream(linkedSpec));
		const price = [...root.querySelectorAll('th button')].find((button) =>
			button.textContent?.includes('Price')
		) as HTMLButtonElement;
		price.click();
		await tick();
		price.click();
		await tick();
		expect(root.querySelector('[data-table] tbody tr')?.textContent).toContain('30');
		const search = root.querySelector('input[type="search"]') as HTMLInputElement;
		search.value = 'West';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(tableCount(root)).toBe(1);
		expect(root.querySelector('[data-table] tbody')?.textContent).toContain('West');
		(root.querySelector('input[type="radio"]') as HTMLInputElement).click();
		await tick();
		expect(total(root)).toBe('20');
	});
	it('restores valid shared state, rejects invalid saved options and handles empty filters', async () => {
		const root = setup(stream(linkedSpec), {
			saved: { first: { 'shared:region': 'injected-option', 'shared:minimum': 'not-a-number' } }
		});
		expect(root.querySelector('select')!.value).toBe('all');
		expect(total(root)).toBe('60');
		const input = root.querySelector('input[type="number"]') as HTMLInputElement;
		input.value = '50';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(tableCount(root)).toBe(0);
		expect(barCount(root)).toBe(0);
		expect(total(root)).toBe('—');
		expect(root.textContent).toContain('No matching rows');
	});
	it('keeps linked exploration local in read-only shared views', async () => {
		const onWrite = vi.fn();
		const onFollowUp = vi.fn();
		const root = setup(stream(linkedSpec), { writable: false, onWrite, onFollowUp });
		const select = root.querySelector('select')!;
		select.value = 'West';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(total(root)).toBe('20');
		expect(onWrite).not.toHaveBeenCalled();
		expect(onFollowUp).not.toHaveBeenCalled();
	});
	it('renders scatter and heatmap data with accessible values', () => {
		const spec = {
			root: 'grid',
			elements: {
				grid: node('Grid', { columns: 2 }, ['scatter', 'heatmap']),
				scatter: node('Chart', {
					title: 'Scatter',
					kind: 'scatter',
					data: [
						{ x: 1, y: 2, label: 'A' },
						{ x: 2, y: 4 }
					]
				}),
				heatmap: node('Chart', {
					title: 'Heatmap',
					kind: 'heatmap',
					data: [
						{ x: 'A', y: 'Monday', value: 5 },
						{ x: 'B', y: 'Monday', value: 10 }
					]
				})
			}
		};
		const root = setup(stream(spec));
		expect(root.querySelectorAll('svg circle')).toHaveLength(2);
		expect(root.querySelectorAll('svg rect')).toHaveLength(2);
		expect(root.textContent).toContain('Monday');
	});
	it('switches Tabs with arrow keys and restores the selected tab', async () => {
		const source = stream({
			root: 'tabs',
			elements: {
				tabs: node('Tabs', { title: 'Details', labels: ['Summary', 'Numbers'] }, [
					'summary',
					'metric'
				]),
				summary: node('Explanation', { title: 'First panel', content: 'Summary text' }),
				metric: node('MetricCard', { title: 'Second panel', value: 42 })
			}
		});
		const saved: Record<string, any> = {};
		let root = setup(source, { onWrite: (key: string, state: any) => (saved[key] = state) });
		expect(root.textContent).toContain('First panel');
		expect(root.textContent).not.toContain('Second panel');
		root
			.querySelector('[role="tab"]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		await tick();
		expect(root.textContent).toContain('Second panel');
		expect(root.textContent).not.toContain('First panel');
		await unmount(instance!);
		instance = undefined;
		document.body.innerHTML = '';
		root = setup(source, { saved });
		expect(root.textContent).toContain('Second panel');
		expect(root.querySelectorAll('[aria-selected="true"]')[0]?.textContent).toBe('Numbers');
	});
	it('normalizes numeric row IDs and restores linked selection after reload', async () => {
		const spec = structuredClone(linkedSpec);
		spec.datasets.products = spec.datasets.products.map((row, index) => ({
			...row,
			id: index + 1
		})) as any;
		const source = stream(spec);
		const saved: Record<string, any> = {};
		let root = setup(source, { onWrite: (key: string, state: any) => (saved[key] = state) });
		(root.querySelectorAll('input[type="radio"]')[1] as HTMLInputElement).click();
		await tick();
		expect(total(root)).toBe('20');
		expect(barCount(root)).toBe(1);
		expect(saved.first['shared:selected']).toBe('2');
		await unmount(instance!);
		instance = undefined;
		document.body.innerHTML = '';
		root = setup(source, { saved });
		expect(total(root)).toBe('20');
		expect((root.querySelectorAll('input[type="radio"]')[1] as HTMLInputElement).checked).toBe(
			true
		);
	});
	it('keeps shared keys isolated between separate UI fences', async () => {
		const source = stream(linkedSpec);
		const root = setup(source, { secondSource: source });
		const select = root.querySelector('select')!;
		select.value = 'East';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(total(root)).toBe('40');
		const second = document.querySelector('[data-intelligent-ui="second"]') as HTMLElement;
		expect(total(second)).toBe('60');
		expect(second.querySelector('select')!.value).toBe('all');
	});
	it('filters dataset-backed scatter and heatmap views locally', async () => {
		const spec = structuredClone(linkedSpec) as any;
		spec.elements.chart = node('Chart', {
			title: 'Scatter data',
			kind: 'scatter',
			dataSource: 'products',
			xField: 'price',
			yField: 'score',
			filters
		});
		spec.elements.heat = node('Chart', {
			title: 'Heat data',
			kind: 'heatmap',
			dataSource: 'products',
			xField: 'region',
			yField: 'id',
			valueField: 'price',
			filters
		});
		spec.elements.root.children.push('heat');
		const root = setup(stream(spec));
		expect(root.querySelectorAll('svg circle')).toHaveLength(3);
		expect(root.querySelectorAll('svg rect')).toHaveLength(3);
		const select = root.querySelector('select')!;
		select.value = 'East';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await tick();
		expect(root.querySelectorAll('svg circle')).toHaveLength(2);
		expect(root.querySelectorAll('svg rect')).toHaveLength(2);
	});
});
