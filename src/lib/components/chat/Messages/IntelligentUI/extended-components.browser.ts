import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, tick } from 'svelte';
import ExtendedHarness from './ExtendedHarness.svelte';
let app: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
afterEach(async () => {
	if (app) await unmount(app);
	target?.remove();
	vi.restoreAllMocks();
});
const node = (type: string, props: any) => ({ type, props });
async function render(elements: any[], initial = {}) {
	target = document.createElement('div');
	document.body.append(target);
	app = mount(ExtendedHarness, { target, props: { elements, initial } });
	await tick();
}
describe('extended components', () => {
	it('links multiline controls, toggles and explicit text safely', async () => {
		await render(
			[
				node('Textarea', { title: 'Notes', stateKey: 'notes', value: '', maxLength: 256 }),
				node('Checkbox', { title: 'Enabled', stateKey: 'enabled', value: false }),
				node('Text', {
					text: 'Summary: {{notes}} / {{enabled}}',
					bindings: [
						{ token: 'notes', stateKey: 'notes' },
						{ token: 'enabled', stateKey: 'enabled' }
					]
				})
			],
			{ notes: '', enabled: false }
		);
		const input = target.querySelector('textarea')!;
		input.value = 'One\n<script>two</script>';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		target.querySelector<HTMLInputElement>('input')!.click();
		await tick();
		expect(target.textContent).toContain('Summary: One\n<script>two</script> / true');
		expect(target.querySelector('script')).toBeNull();
	});
	it('opens popovers with focus, closes via Escape/outside and restores trigger', async () => {
		await render([node('Popover', { label: 'Details' })]);
		const button = target.querySelector('button')!;
		button.click();
		await tick();
		await tick();
		expect(target.querySelector('[role=dialog]')).not.toBeNull();
		expect(document.activeElement).toBe(target.querySelector('[role=dialog]'));
		target
			.querySelector('[role=dialog]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await tick();
		expect(target.querySelector('[role=dialog]')).toBeNull();
		expect(document.activeElement).toBe(button);
		button.click();
		await tick();
		window.dispatchEvent(new Event('pointerdown'));
		await tick();
		expect(target.querySelector('[role=dialog]')).toBeNull();
	});
	it('changes carousel local state with keyboard and arrows', async () => {
		await render([node('Carousel', { __key: 'cards', __index: 0, __count: 3 })]);
		target
			.querySelector('[tabindex="0"]')!
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
		await tick();
		expect(target.querySelector('output')!.textContent).toContain('"carousel:cards":2');
	});
	it('copies only selected parameters after explicit click', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
		await render(
			[node('Button', { label: 'Copy parameters', actions: [{ type: 'copy', keys: ['count'] }] })],
			{ count: 3, secret: 'omit' }
		);
		target.querySelector('button')!.click();
		await tick();
		await tick();
		expect(writeText).toHaveBeenCalledWith('{\n  "count": 3\n}');
		await vi.waitFor(() => expect(target.textContent).toContain('Copied'));
	});

	it('isolates native radio groups between independently rendered controls', async () => {
		const props = {
			title: 'Choice',
			__key: 'same',
			stateKey: 'mode',
			value: 'a',
			options: [
				{ label: 'A', value: 'a' },
				{ label: 'B', value: 'b' }
			]
		};
		await render([node('RadioGroup', props), node('RadioGroup', props)], { mode: 'a' });
		const radios = target.querySelectorAll<HTMLInputElement>('input[type=radio]');
		expect(radios[0].name).not.toBe(radios[2].name);
		radios[1].click();
		await tick();
		expect(radios[1].checked).toBe(true);
		expect(radios[3].checked).toBe(true);
	});
	it('renders trusted-off KaTeX and image failure fallback', async () => {
		await render([
			node('Math', { expression: 'x^2', display: true }),
			node('Image', { src: 'https://example.com/a.png', alt: 'Example', caption: 'Caption' })
		]);
		expect(target.querySelector('.katex')).not.toBeNull();
		target.querySelector('img')!.dispatchEvent(new Event('error'));
		await tick();
		expect(target.textContent).toContain('Image unavailable: Example');
	});
});

it('activates the entire Pressable surface with content click and keyboard', async () => {
	await render(
		[
			node('Pressable', {
				label: 'Choose card',
				actions: [{ type: 'increment', stateKey: 'count', amount: 1 }]
			})
		],
		{ count: 0 }
	);
	const surface = target.querySelector<HTMLElement>('[role=button]')!;
	Array.from(surface.querySelectorAll('p'))
		.find((p) => p.textContent === 'Panel contents')!
		.click();
	await tick();
	expect(target.querySelector('output')!.textContent).toContain('"count":1');
	for (const key of ['Enter', ' ']) {
		surface.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		await tick();
	}
	expect(target.querySelector('output')!.textContent).toContain('"count":3');
	expect(surface.querySelector('button')).toBeNull();
});
