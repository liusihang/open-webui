// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TestHarness from './TestHarness.svelte';
let component: ReturnType<typeof mount> | undefined;
afterEach(async () => {
	if (component) await unmount(component);
	component = undefined;
	document.body.innerHTML = '';
});
describe('scoped layout colors and unchanged persisted IDs', () => {
	it('paints an explicitly colored tab surface and distinguishes selected accents across switches', async () => {
		const source = JSON.stringify({
			root: 'tabs',
			elements: {
				tabs: {
					type: 'Tabs',
					props: {
						title: 'Options',
						labels: ['First', 'Second'],
						appearance: { foreground: '#ffffff', background: '#112233', accent: '#fb923c' }
					},
					children: ['a', 'b']
				},
				a: { type: 'Text', props: { text: 'First view' }, children: [] },
				b: { type: 'Text', props: { text: 'Second view' }, children: [] }
			}
		});
		const target = document.createElement('div');
		document.body.append(target);
		const onWrite = vi.fn();
		component = mount(TestHarness, { target, props: { initialSource: source, onWrite } });
		flushSync();
		const root = target.querySelector('[data-intelligent-ui="first"]')!;
		const section = root.querySelector<HTMLElement>('section')!;
		expect(section.classList.contains('iui-surface')).toBe(true);
		expect(section.style.getPropertyValue('--iui-bg-light')).toBe('#112233');
		const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
		expect(buttons[0].style.getPropertyValue('--iui-bg-light')).toBe('#fb923c');
		expect(buttons[1].style.getPropertyValue('--iui-bg-light')).toBe('#112233');
		buttons[1].click();
		await tick();
		expect(buttons[1].getAttribute('aria-selected')).toBe('true');
		expect(buttons[1].style.getPropertyValue('--iui-bg-light')).toBe('#fb923c');
		expect(onWrite).toHaveBeenCalledWith('first', { 'tab:tabs': 'b' });
		expect(root.textContent).toContain('Second view');
	});
});
