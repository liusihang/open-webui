// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RichChart from './RichChart.svelte';
import MatrixChart from './MatrixChart.svelte';
import { createTestI18n } from './test-i18n';

const labels = [
	'ATP metabolism',
	'Microorganism response',
	'Membrane transport',
	'动力学参数',
	'碰撞频率',
	'TopN comparison',
	'隔离处理',
	'峰值强度',
	'单细胞分析',
	'同源序列'
];
let component: ReturnType<typeof mount> | undefined;
beforeAll(() => {
	if (!globalThis.ResizeObserver)
		vi.stubGlobal(
			'ResizeObserver',
			class {
				observe() {}
				unobserve() {}
				disconnect() {}
			}
		);
});
afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});
function setup(Component: any, props: any) {
	const target = document.createElement('div');
	document.body.append(target);
	component = mount(Component, { target, props, context: new Map([['i18n', createTestI18n()]]) });
	flushSync();
	return target;
}
const visibleLabel = (node: Element) => node.textContent!.replace(/\s/g, '');

describe('complete chart labels and bounded typography', () => {
	it.each(['bar', 'line'])('shows every complete %s category without ellipsis', (kind) => {
		const target = setup(RichChart, {
			view: { title: 'Categories', kind },
			data: labels.map((label, index) => ({ label, value: index + 1 }))
		});
		const ticks = [...target.querySelectorAll('[data-axis="x"]')];
		expect(ticks).toHaveLength(labels.length);
		ticks.forEach((tick, i) => {
			expect(visibleLabel(tick)).toBe(labels[i].replace(/\s/g, ''));
			expect(tick.textContent).not.toContain('…');
			expect(tick.getAttribute('font-size')).toBe('12');
			expect(tick.getAttribute('transform')).toMatch(/^rotate\(-45 /);
		});
	});
	it('does not hide axes when there are more than twelve categories', () => {
		const data = Array.from({ length: 24 }, (_, i) => ({ label: `完整分类 ${i + 1}`, value: i }));
		const target = setup(RichChart, { view: { title: 'Many categories', kind: 'bar' }, data });
		expect(target.querySelectorAll('[data-axis="x"]')).toHaveLength(24);
		expect(Number(target.querySelector('svg')?.getAttribute('width'))).toBeGreaterThan(600);
	});
	it('keeps complete heatmap labels on both axes with more than ten rows', () => {
		const rows = Array.from({ length: 12 }, (_, i) => `完整的长行名称 ${i}`);
		const target = setup(MatrixChart, {
			view: { title: 'Heatmap', kind: 'heatmap' },
			data: rows.flatMap((y) => labels.map((x, value) => ({ x, y, value }))).slice(0, 120)
		});
		const x = [...target.querySelectorAll('[data-axis="x"]')];
		const y = [...target.querySelectorAll('[data-axis="y"]')];
		expect(x.map(visibleLabel)).toEqual(labels.map((label) => label.replace(/\s/g, '')));
		expect(y.map(visibleLabel)).toEqual(rows.map((label) => label.replace(/\s/g, '')));
	});
});
