import { afterEach, describe, expect, it } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import ExtendedHarness from './ExtendedHarness.svelte';
import TestHarness from './TestHarness.svelte';
import { validateExtendedElement } from '$lib/utils/intelligent-ui/extended';
import { contrastRatio } from '$lib/utils/intelligent-ui/colors';
import './colors.css';

let app: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
afterEach(async () => {
	if (app) await unmount(app);
	target?.remove();
});
const node = (type: string, props: any, children: string[] = []) =>
	validateExtendedElement({ type, props, children });
async function render(elements: ReturnType<typeof node>[], initial = {}) {
	target = document.createElement('div');
	document.body.append(target);
	app = mount(ExtendedHarness, { target, props: { elements, initial } });
	await tick();
}
const background = { light: '#112233', dark: '#eeddcc' };
const appearance = {
	background,
	foreground: background,
	accent: { light: '#eab308', dark: '#0369a1' },
	border: { light: '#234567', dark: '#987654' }
};
const variable = (element: HTMLElement, channel: string, theme: 'light' | 'dark') =>
	element.style.getPropertyValue(`--iui-${channel}-${theme}`);
const expectReadable = (element: HTMLElement) => {
	for (const theme of ['light', 'dark'] as const)
		expect(
			contrastRatio(variable(element, 'fg', theme), variable(element, 'bg', theme))
		).toBeGreaterThanOrEqual(4.5);
};

describe('native content colors and rich text', () => {
	it('renders independently scoped light/dark surfaces and readable foregrounds', async () => {
		await render([
			node('Card', {
				title: 'Dark light theme card',
				description: 'Readable description',
				appearance
			}),
			node('Card', { title: 'Independent default' }),
			node('Text', { text: 'Inherit ordinary ink' })
		]);
		const cards = target.querySelectorAll<HTMLElement>('section');
		expect(cards[0].classList.contains('iui-surface')).toBe(true);
		expect(variable(cards[0], 'bg', 'light')).toBe(background.light);
		expect(variable(cards[0], 'bg', 'dark')).toBe(background.dark);
		expect(variable(cards[0], 'border', 'light')).toBe('#234567');
		expect(variable(cards[0], 'border', 'dark')).toBe('#987654');
		expectReadable(cards[0]);
		expect(variable(cards[1], 'bg', 'light')).not.toBe(background.light);
		expect(cards[0].querySelector('p')!.className).not.toContain('text-gray');
		const plain = Array.from(target.querySelectorAll('p')).find(
			(p) => p.textContent === 'Inherit ordinary ink'
		)!;
		expect(plain.getAttribute('style')).toBeNull();
	});

	it('escapes colored inline text and bound values while preserving token updates', async () => {
		await render(
			[
				node('Input', { title: 'Name', stateKey: 'name', value: 'Ada', appearance }),
				node('Text', {
					text: 'Hidden fallback',
					appearance: { background: '#fff', foreground: '#123' },
					bindings: [{ token: 'name', stateKey: 'name' }],
					spans: [
						{ text: 'Hello ' },
						{ text: '{{name}}', color: { light: '#0f0', dark: '#00f' }, bold: true },
						{ text: '<img src=x onerror=alert(1)><script>bad</script>', color: '#d40' }
					]
				})
			],
			{ name: 'Ada' }
		);
		const text = target.querySelector<HTMLElement>('p.iui-ink')!;
		const spans = text.querySelectorAll<HTMLSpanElement>('span');
		expect(spans).toHaveLength(3);
		expect(spans[1].textContent).toBe('Ada');
		expect(spans[1].classList.contains('font-bold')).toBe(true);
		expectReadable(spans[1]);
		expect(text.textContent).toContain('<img src=x onerror=alert(1)><script>bad</script>');
		expect(target.querySelector('img, script')).toBeNull();
		expect(target.textContent).not.toContain('Hidden fallback');
		const input = target.querySelector<HTMLInputElement>('input')!;
		input.value = '<b>{{name}}</b>';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(spans[1].textContent).toBe('<b>{{name}}</b>');
		expect(target.querySelector('b')).toBeNull();
	});

	it('paints explicitly requested text and icon backgrounds used for contrast', async () => {
		await render([
			node('Text', { text: 'Highlighted text', appearance }),
			node('Icon', { name: 'info', label: 'Information', appearance })
		]);
		const elements = [
			target.querySelector<HTMLElement>('p.iui-ink')!,
			target.querySelector<HTMLElement>('[role=img]')!
		];
		for (const element of elements) {
			expect(element.classList.contains('iui-surface')).toBe(true);
			expect(variable(element, 'bg', 'light')).toBe(background.light);
			expect(variable(element, 'bg', 'dark')).toBe(background.dark);
			expectReadable(element);
		}
	});

	it('colors individual Flow steps without leaking into sibling steps', async () => {
		await render([
			node('Flow', {
				appearance: { background: '#fefefe' },
				steps: [{ title: 'Special', appearance }, { title: 'Ordinary' }]
			})
		]);
		const steps = target.querySelectorAll<HTMLElement>('li');
		expect(variable(steps[0], 'bg', 'light')).toBe(background.light);
		expect(variable(steps[1], 'bg', 'light')).toBe('#fefefe');
		expect(steps[0].querySelector('span')!.classList.contains('iui-accent')).toBe(true);
		expectReadable(steps[0]);
	});

	it('preserves action state and selected controls with explicit colors', async () => {
		await render(
			[
				node('Checkbox', { title: 'Enabled', stateKey: 'enabled', value: false, appearance }),
				node('Button', {
					label: 'Toggle enabled',
					actions: [{ type: 'toggle', stateKey: 'enabled' }],
					appearance: { accent: '#ffff00', foreground: '#ffff00' }
				}),
				node('SegmentedControl', {
					title: 'Mode',
					stateKey: 'mode',
					value: 'a',
					appearance,
					options: [
						{ label: 'A', value: 'a' },
						{ label: 'B', value: 'b' }
					]
				}),
				node('Text', {
					text: 'Fallback',
					bindings: [
						{ token: 'enabled', stateKey: 'enabled' },
						{ token: 'mode', stateKey: 'mode' }
					],
					spans: [{ text: '{{enabled}} / {{mode}}', color: '#345', bold: true }]
				})
			],
			{ enabled: false, mode: 'a' }
		);
		const button = target.querySelector<HTMLButtonElement>('button')!;
		expect(button.classList.contains('iui-strong')).toBe(true);
		expectReadable(button);
		button.click();
		await tick();
		expect(target.querySelector<HTMLInputElement>('[type=checkbox]')!.checked).toBe(true);
		const radios = target.querySelectorAll<HTMLInputElement>('[type=radio]');
		radios[1].click();
		await tick();
		expect(radios[1].closest('label')!.classList.contains('is-selected')).toBe(true);
		expect(radios[0].closest('label')!.classList.contains('is-selected')).toBe(false);
		expect(target.querySelector('p.iui-ink')!.textContent).toBe('true / b');
	});

	it('uses the actual ancestor surface for colored spans without painting inherited Text backgrounds', async () => {
		const elements = {
			card: node(
				'Card',
				{
					title: 'Custom surface',
					appearance: { background: { light: '#101010', dark: '#eeeeee' } }
				},
				['layout']
			),
			layout: { type: 'Stack', props: {}, children: ['text', 'icon'] },
			text: node('Text', {
				text: 'Fallback',
				appearance: { foreground: { light: '#111111', dark: '#dddddd' } },
				spans: [
					{ text: 'Nested color', color: { light: '#101010', dark: '#eeeeee' } },
					{ text: ' inherited text' }
				]
			}),
			icon: node('Icon', {
				name: 'star',
				label: 'Inherited star',
				appearance: { foreground: '#999999' }
			})
		};
		const source = [
			JSON.stringify({ op: 'add', path: '/root', value: 'card' }),
			...Object.entries(elements).map(([key, value]) =>
				JSON.stringify({ op: 'add', path: `/elements/${key}`, value })
			)
		].join('\n');
		target = document.createElement('div');
		document.body.append(target);
		app = mount(TestHarness, { target, props: { initialSource: source } });
		await tick();
		const block = target.querySelector('[data-intelligent-ui="first"]')!;
		const card = block.querySelector<HTMLElement>('section')!;
		const text = block.querySelector<HTMLElement>('p.iui-ink')!;
		const span = text.querySelector<HTMLElement>('span.iui-ink')!;
		const icon = block.querySelector<HTMLElement>('[role=img]')!;
		for (const element of [text, span, icon]) {
			expect(element.classList.contains('iui-surface')).toBe(false);
			for (const theme of ['light', 'dark'] as const)
				expect(
					contrastRatio(variable(element, 'fg', theme), variable(card, 'bg', theme))
				).toBeGreaterThanOrEqual(4.5);
		}
		expect(variable(span, 'bg', 'light')).toBe('#101010');
		expect(variable(span, 'bg', 'dark')).toBe('#eeeeee');
		expect(text.textContent).toBe('Nested color inherited text');
	});

	it('keeps shared Text DAG instances independent under different ancestor backgrounds', async () => {
		const shared = node('Text', {
			text: 'Shared fallback',
			appearance: { foreground: '#777777' },
			spans: [{ text: 'Shared colored text', color: '#888888', bold: true }]
		});
		const source = (firstBackground = '#111111') =>
			[
				JSON.stringify({ op: 'add', path: '/root', value: 'root' }),
				...Object.entries({
					root: { type: 'Stack', props: {}, children: ['darkCard', 'lightCard'] },
					darkCard: node(
						'Card',
						{
							title: 'Dark parent',
							appearance: { background: { light: firstBackground, dark: '#eeeeee' } }
						},
						['shared']
					),
					lightCard: node(
						'Card',
						{
							title: 'Light parent',
							appearance: { background: { light: '#eeeeee', dark: '#111111' } }
						},
						['shared']
					),
					shared
				}).map(([key, value]) => JSON.stringify({ op: 'add', path: `/elements/${key}`, value }))
			].join('\n');
		target = document.createElement('div');
		document.body.append(target);
		const harness = mount(TestHarness, { target, props: { initialSource: source() } });
		app = harness;
		await tick();
		const block = target.querySelector('[data-intelligent-ui="first"]')!;
		const inspect = () => {
			const cards = [...block.querySelectorAll<HTMLElement>('section.iui-surface')];
			expect(cards).toHaveLength(2);
			for (const card of cards) {
				const text = card.querySelector<HTMLElement>('p.iui-ink')!;
				const span = text.querySelector<HTMLElement>('span.iui-ink')!;
				expect(text.classList.contains('iui-surface')).toBe(false);
				for (const element of [text, span]) {
					for (const theme of ['light', 'dark'] as const) {
						expect(variable(element, 'bg', theme)).toBe(variable(card, 'bg', theme));
						expect(
							contrastRatio(variable(element, 'fg', theme), variable(card, 'bg', theme))
						).toBeGreaterThanOrEqual(4.5);
					}
				}
			}
			return cards.map((card) => card.querySelector<HTMLElement>('span.iui-ink')!);
		};
		const before = inspect();
		expect(before[0]).not.toBe(before[1]);
		expect(variable(before[0], 'bg', 'light')).toBe('#111111');
		expect(variable(before[1], 'bg', 'light')).toBe('#eeeeee');
		expect(variable(before[0], 'fg', 'light')).not.toBe(variable(before[1], 'fg', 'light'));
		const independentStyle = before[1].getAttribute('style');
		harness.updateSource(source('#333333'), true);
		await tick();
		const after = inspect();
		expect(variable(after[0], 'bg', 'light')).toBe('#333333');
		expect(after[1].getAttribute('style')).toBe(independentStyle);
		expect(shared.props).toMatchObject({
			appearance: { foreground: '#777777' },
			spans: [{ color: '#888888' }]
		});
		expect(shared.props).not.toHaveProperty('__appearance');
	});

	it('keeps a transparent ComparisonCards title readable on its ancestor surface', async () => {
		const source = [
			JSON.stringify({ op: 'add', path: '/root', value: 'card' }),
			...Object.entries({
				card: node(
					'Card',
					{ title: 'Parent', appearance: { background: { light: '#101010', dark: '#eeeeee' } } },
					['comparison']
				),
				comparison: {
					type: 'ComparisonCards',
					props: {
						title: 'Nested comparison',
						items: [
							{
								title: 'First',
								description: 'Details',
								appearance: { background: '#ffffff', foreground: '#222222' }
							}
						]
					},
					children: []
				}
			}).map(([key, value]) => JSON.stringify({ op: 'add', path: `/elements/${key}`, value }))
		].join('\n');
		target = document.createElement('div');
		document.body.append(target);
		app = mount(TestHarness, { target, props: { initialSource: source } });
		await tick();
		const block = target.querySelector('[data-intelligent-ui="first"]')!;
		const card = block.querySelector<HTMLElement>('section.iui-surface')!;
		const comparison = block.querySelector<HTMLElement>('section.iui-ink')!;
		expect(comparison.classList.contains('iui-surface')).toBe(false);
		expect(comparison.querySelector('h3')!.textContent).toBe('Nested comparison');
		for (const theme of ['light', 'dark'] as const)
			expect(
				contrastRatio(variable(comparison, 'fg', theme), variable(card, 'bg', theme))
			).toBeGreaterThanOrEqual(4.5);
		expectReadable(comparison.querySelector<HTMLElement>('article')!);
	});

	it('renders plain fallback text when spans are absent and empty content when spans are empty', async () => {
		await render([
			node('Text', { text: 'Plain text' }),
			node('Text', { text: 'Not visible', spans: [] })
		]);
		const text = target.querySelectorAll('p.iui-ink');
		expect(text[0].textContent).toBe('Plain text');
		expect(text[1].textContent).toBe('');
	});
});
