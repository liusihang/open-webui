import { describe, expect, it } from 'vitest';
import {
	validateExtendedElement as validate,
	boundText,
	extendedStateReferences
} from './extended';

const node = (type: string, props: Record<string, unknown>, children: string[] = []) => ({
	type,
	props,
	children
});
const appearance = {
	foreground: { light: '#123', dark: '#fedcba' },
	background: '#abc',
	border: '#456789',
	accent: '#abcdef',
	palette: 'ocean'
};
const control = { title: 'Choice', stateKey: 'choice', value: 'a' };
const choices = { ...control, options: [{ label: 'A', value: 'a' }] };
const actions = [{ type: 'toggle', stateKey: 'enabled' }];
const styledNodes = [
	node('Card', { title: 'Card' }),
	node('Carousel', { title: 'Carousel' }, ['slide']),
	node('Flow', { steps: [{ title: 'First' }] }),
	node('Popover', { label: 'Details' }),
	node('Icon', { name: 'info', label: 'Information' }),
	node('Text', { text: 'Text' }),
	node('Button', { label: 'Toggle', actions }),
	node('Pressable', { label: 'Toggle', actions }),
	node('Input', control),
	node('Textarea', control),
	node('Checkbox', { title: 'Enabled', stateKey: 'enabled', value: false }),
	node('RadioGroup', choices),
	node('SegmentedControl', choices),
	node('DatePicker', { title: 'Date', stateKey: 'date', value: '2026-10-20' })
];

describe('extended native color protocol', () => {
	it.each(styledNodes)('accepts only normalized appearance on $type', (element) => {
		const parsed = validate({ ...element, props: { ...element.props, appearance } });
		expect(parsed.props).toMatchObject({ appearance });
		expect((parsed.props as { appearance: unknown }).appearance).not.toBe(appearance);
	});

	it.each([
		'color:red',
		{ foreground: 'red' },
		{ background: 'url(https://example.com/track)' },
		{ border: '#123;position:fixed' },
		{ accent: 'var(--private)' },
		{ foreground: { light: '#123' } },
		{ foreground: { light: '#123', dark: '#456', style: 'display:none' } },
		{ background: ['#123', '#456'] },
		{ palette: 'constructor' },
		{ style: 'background:red' },
		{ class: 'hidden' }
	])('rejects unsafe appearance %j', (invalid) => {
		for (const element of styledNodes)
			expect(() =>
				validate({ ...element, props: { ...element.props, appearance: invalid } })
			).toThrow();
	});

	it.each([
		node('SVG', { label: 'Shape', svg: '<svg><circle r="1"/></svg>' }),
		node('Math', { expression: 'x^2' }),
		node('Image', { src: 'https://example.com/image.png', alt: 'Image' }),
		node('ImageGallery', { images: [{ src: 'https://example.com/image.png', alt: 'Image' }] }),
		node('Conditional', { stateKey: 'enabled', equals: true }, ['child'])
	])('rejects unsupported appearance on $type', (element) => {
		expect(() => validate({ ...element, props: { ...element.props, appearance } })).toThrow();
	});

	it('validates each Flow step independently', () => {
		const flow = validate(
			node('Flow', {
				appearance,
				steps: [{ title: 'First', appearance: { background: '#135' } }, { title: 'Second' }]
			})
		);
		expect(flow.props).toMatchObject({
			appearance,
			steps: [{ title: 'First', appearance: { background: '#135' } }, { title: 'Second' }]
		});
		for (const step of [
			{ title: 'First', appearance: { foreground: 'expression(alert(1))' } },
			{ title: 'First', color: '#123' },
			{ title: 'First', appearance: { css: 'display:none' } }
		])
			expect(() => validate(node('Flow', { steps: [step] }))).toThrow();
	});
});

describe('bounded rich Text spans', () => {
	it('preserves fallback, normalizes spans, and keeps explicit state references', () => {
		const parsed = validate(
			node('Text', {
				text: 'Fallback',
				bindings: [{ token: 'name', stateKey: 'user' }],
				spans: [
					{ text: 'Hello ', bold: false },
					{ text: '{{name}}', color: { light: '#246', dark: '#ace' }, bold: true },
					{ text: ' <b>literal</b>' }
				]
			})
		);
		expect(parsed.type).toBe('Text');
		if (parsed.type !== 'Text') throw new Error('Unexpected component');
		expect(parsed.props.text).toBe('Fallback');
		expect(parsed.props.spans).toHaveLength(3);
		expect(extendedStateReferences(parsed)).toEqual(['user']);
		expect(
			parsed.props
				.spans!.map((span) =>
					boundText(span.text, parsed.props.bindings, () => '<script>{{name}}</script>')
				)
				.join('')
		).toBe('Hello <script>{{name}}</script> <b>literal</b>');
	});

	it('keeps fallback text required and backward compatible', () => {
		expect(validate(node('Text', { text: 'Plain' })).props).toEqual({
			text: 'Plain',
			bindings: []
		});
		expect(validate(node('Text', { text: '', spans: [] })).props).toEqual({
			text: '',
			bindings: [],
			spans: []
		});
		expect(() => validate(node('Text', { spans: [{ text: 'Missing fallback' }] }))).toThrow();
	});

	it('bounds count and combined span text', () => {
		expect(() =>
			validate(node('Text', { text: '', spans: Array.from({ length: 64 }, () => ({ text: '' })) }))
		).not.toThrow();
		expect(() =>
			validate(node('Text', { text: '', spans: Array.from({ length: 65 }, () => ({ text: '' })) }))
		).toThrow();
		expect(() =>
			validate(
				node('Text', { text: '', spans: [{ text: 'x'.repeat(8000) }, { text: 'y'.repeat(8000) }] })
			)
		).not.toThrow();
		expect(() =>
			validate(
				node('Text', { text: '', spans: [{ text: 'x'.repeat(8000) }, { text: 'y'.repeat(8001) }] })
			)
		).toThrow();
	});

	it.each([
		null,
		'text',
		{},
		[null],
		[1],
		[{ text: 42 }],
		[{ text: 'x', bold: 'true' }],
		[{ text: 'x', bold: 1 }],
		[{ text: 'x', color: '#fff;background-image:url(https://example.com/x)' }],
		[{ text: 'x', color: { light: '#fff' } }],
		[{ text: 'x', color: { light: '#fff', dark: '#000', fallback: '#123' } }],
		[{ text: 'x', style: 'color:red' }],
		[{ text: 'x', html: '<script>alert(1)</script>' }],
		[{ text: 'x', href: 'https://example.com' }],
		[{ text: 'x', onclick: 'alert(1)' }]
	])('rejects malformed or executable spans %j', (spans) => {
		expect(() => validate(node('Text', { text: '', spans }))).toThrow();
	});
});
