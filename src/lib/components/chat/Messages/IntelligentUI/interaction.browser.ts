// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import TestHarness from './TestHarness.svelte';

const slider = [
	JSON.stringify({ op: 'add', path: '/root', value: 'slider' }),
	JSON.stringify({
		op: 'add',
		path: '/elements/slider',
		value: {
			type: 'ParameterSlider',
			props: {
				title: 'Budget',
				min: 0,
				max: 100,
				step: 1,
				value: 20,
				followUp: 'Update the recommendation.'
			},
			children: []
		}
	})
].join('\n');
let instance: ReturnType<typeof mount> | undefined;
afterEach(async () => {
	if (instance) await unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});
function setup(props: any) {
	const target = document.createElement('div');
	document.body.append(target);
	instance = mount(TestHarness, { target, props });
	flushSync();
	return target;
}

describe('Intelligent UI interactions', () => {
	it('exposes only writing actions and preserves editing, copying and expansion without an outer card', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
		const onWrite = vi.fn();
		const onFollowUp = vi.fn();
		const source = [
			JSON.stringify({ op: 'add', path: '/root', value: 'draft' }),
			JSON.stringify({
				op: 'add',
				path: '/elements/draft',
				value: {
					type: 'WritingBlock',
					props: { title: 'Draft', content: 'Original draft.' },
					children: []
				}
			})
		].join('\n');
		const target = setup({ initialSource: source, onWrite, onFollowUp });
		const root = target.querySelector('[data-intelligent-ui="first"]')!;
		expect(root.textContent).not.toMatch(/Interactive view|View data|Hide data/);
		expect([...root.querySelectorAll('button')].map((button) => button.textContent)).toEqual([
			'Edit',
			'Copy',
			'Expand'
		]);
		expect(root.classList.contains('border')).toBe(false);
		expect(root.classList.contains('shadow-sm')).toBe(false);
		expect(root.querySelectorAll('[data-writing-block]')).toHaveLength(1);
		const button = (label: string) =>
			[...root.querySelectorAll('button')].find((item) => item.textContent === label)!;
		button('Edit').click();
		await tick();
		const input = root.querySelector('textarea')!;
		input.value = 'Revised draft.';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		button('Copy').click();
		button('Expand').click();
		await tick();
		expect(writeText).toHaveBeenCalledWith('Revised draft.');
		expect(onWrite).toHaveBeenCalledWith('first', { 'writing:draft': 'Revised draft.' });
		expect(button('Collapse').getAttribute('aria-expanded')).toBe('true');
		expect(onFollowUp).not.toHaveBeenCalled();
	});
	it('shows a readable failure without a source-data escape hatch', () => {
		const target = setup({ initialSource: '{"unsupported_ui_source":"private-marker"}' });
		const root = target.querySelector('[data-intelligent-ui="first"]')!;
		expect(root.textContent).toContain('Please ask for a text response.');
		expect(root.textContent).not.toContain('private-marker');
		expect(root.querySelector('button, pre, iframe, script')).toBeNull();
	});
	it('keeps slider changes local until one explicit model action', async () => {
		const onWrite = vi.fn();
		const onFollowUp = vi.fn(async (_prompt: string) => {});
		const target = setup({ initialSource: slider, onWrite, onFollowUp });
		const input = target.querySelector('input')!;
		input.value = '45';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(target.textContent).toContain('Update the recommendation.');
		expect(onWrite).toHaveBeenCalledTimes(1);
		expect(onFollowUp).not.toHaveBeenCalled();
		const button = [...target.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Ask model')
		)!;
		button.click();
		await tick();
		expect(onFollowUp).toHaveBeenCalledTimes(1);
		expect(onFollowUp.mock.calls[0][0]).toContain('Budget = 45');
	});
	it('restores persisted control values after remount and keeps blocks separate', async () => {
		const saved: Record<string, any> = {};
		let target = setup({
			initialSource: slider,
			onWrite: (block: string, state: any) => {
				saved[block] = state;
			}
		});
		const input = target.querySelector('input')!;
		input.value = '61';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		await unmount(instance!);
		instance = undefined;
		document.body.innerHTML = '';
		target = setup({ initialSource: slider, saved });
		expect(target.querySelector('input')!.value).toBe('61');
		expect(saved.second).toBeUndefined();
	});
	it('keeps read-only exploration local and disables follow-up', async () => {
		const onWrite = vi.fn();
		const onFollowUp = vi.fn();
		const target = setup({ initialSource: slider, writable: false, onWrite, onFollowUp });
		const input = target.querySelector('input')!;
		input.value = '70';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(onWrite).not.toHaveBeenCalled();
		const button = [...target.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Ask model')
		)!;
		expect(button.disabled).toBe(true);
		button.click();
		expect(onFollowUp).not.toHaveBeenCalled();
	});
	it('renders a complete operation before final completion and preserves surrounding order', async () => {
		const target = setup({ initialSource: slider + '\n{"op":', initialDone: false });
		expect(target.querySelector('input')).not.toBeNull();
		const sequence = target.textContent!;
		expect(sequence.indexOf('Text before')).toBeLessThan(sequence.indexOf('Budget'));
		expect(sequence.indexOf('Budget')).toBeLessThan(sequence.indexOf('Text between'));
		expect(sequence.indexOf('Text between')).toBeLessThan(sequence.indexOf('Second view'));
		expect(sequence.indexOf('Second view')).toBeLessThan(sequence.indexOf('Text after'));
		expect(
			[...target.querySelectorAll('button')].find((button) =>
				button.textContent?.includes('Ask model')
			)!.disabled
		).toBe(true);
		(instance as any).updateSource(slider, true);
		await tick();
		expect(
			[...target.querySelectorAll('button')].find((button) =>
				button.textContent?.includes('Ask model')
			)!.disabled
		).toBe(false);
	});
	it('prevents duplicate follow-ups while a prior action is pending', async () => {
		let resolve!: () => void;
		const onFollowUp = vi.fn(
			() =>
				new Promise<void>((r) => {
					resolve = r;
				})
		);
		const target = setup({ initialSource: slider, onFollowUp });
		const button = [...target.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Ask model')
		)!;
		button.click();
		button.click();
		await tick();
		expect(onFollowUp).toHaveBeenCalledTimes(1);
		resolve();
		await tick();
	});
	it('persists explanation expansion independently of other controls', async () => {
		const onWrite = vi.fn();
		const target = setup({ initialSource: slider, onWrite });
		const details = target.querySelector('details')!;
		details.open = true;
		details.dispatchEvent(new Event('toggle'));
		await tick();
		expect(onWrite).toHaveBeenCalledWith(
			'second',
			expect.objectContaining({
				'["explanation","Explanation","Second view",null,null,null]': true
			})
		);
	});
	it('ignores corrupt imported control state', () => {
		const key = '["slider","ParameterSlider","Budget",0,100,1]';
		const target = setup({ initialSource: slider, saved: { first: { [key]: 'not a number' } } });
		expect(target.querySelector('input')!.value).toBe('20');
	});
});
