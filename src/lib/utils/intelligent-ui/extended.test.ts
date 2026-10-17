import { describe, it, expect } from 'vitest';
import {
	validateExtendedElement as validate,
	sanitizeSvg,
	safeImageUrl,
	extendedSharedValue,
	runActions,
	boundText,
	extendedStateReferences,
	validatePressableChildren
} from './extended';
const node = (type: string, props: unknown, children: string[] = []) => ({ type, props, children });
describe('bounded extended UI protocol', () => {
	it('normalizes containers and limits children', () => {
		expect(validate(node('Card', {}, ['child'])).children).toEqual(['child']);
		expect(() => validate(node('Carousel', {}))).toThrow();
		expect(() =>
			validate(node('Conditional', { stateKey: 'mode', equals: true }, ['a', 'b', 'c']))
		).toThrow();
	});
	it('rejects callbacks, HTML and executable actions', () => {
		expect(() =>
			validate(
				node('Button', {
					label: 'Run',
					actions: [{ type: 'eval', stateKey: 'x', code: 'alert(1)' }]
				})
			)
		).toThrow();
		expect(() =>
			validate(node('Input', { title: 'Name', stateKey: 'name', value: '', onchange: 'x' }))
		).toThrow();
		expect(() => validate(node('Card', { html: '<script>x</script>' }))).toThrow();
	});
	it('bounds and validates form definitions', () => {
		expect(validate(node('Input', { title: 'Name', stateKey: 'name' })).props).toMatchObject({
			value: '',
			maxLength: 256
		});
		expect(() =>
			validate(node('Textarea', { title: 'Notes', stateKey: 'notes', maxLength: 257 }))
		).toThrow();
		expect(() =>
			validate(node('Checkbox', { title: 'Enabled', stateKey: 'enabled', value: 'true' }))
		).toThrow();
		expect(() =>
			validate(
				node('RadioGroup', {
					title: 'Mode',
					stateKey: 'mode',
					value: 'a',
					options: [
						{ label: 'A', value: 'a' },
						{ label: 'Again', value: 'a' }
					]
				})
			)
		).toThrow();
	});
	it('validates real calendar dates and bounds', () => {
		expect(() =>
			validate(node('DatePicker', { title: 'Date', stateKey: 'day', value: '2025-02-29' }))
		).toThrow();
		expect(
			validate(node('DatePicker', { title: 'Date', stateKey: 'day', value: '2024-02-29' })).props
		).toMatchObject({ value: '2024-02-29' });
		expect(() =>
			validate(
				node('DatePicker', {
					title: 'Date',
					stateKey: 'day',
					value: '2026-01-01',
					min: '2026-02-01'
				})
			)
		).toThrow();
	});
	it('only accepts HTTPS public-host images and source links', () => {
		expect(safeImageUrl('https://example.com/a.png')).toBe('https://example.com/a.png');
		for (const url of [
			'javascript:alert(1)',
			'data:image/svg+xml,x',
			'http://example.com/a',
			'https://127.0.0.1/a',
			'https://2130706433/a',
			'https://[::1]/a',
			'https://user:secret@example.com/a',
			'https://example.com:8080/a',
			'https://host.local/a'
		])
			expect(() => safeImageUrl(url)).toThrow();
	});
	it('strictly allows only inert SVG geometry', () => {
		const svg =
			'<svg viewBox="0 0 20 20"><title>Shape</title><circle cx="10" cy="10" r="8" fill="currentColor"/></svg>';
		expect(sanitizeSvg(svg)).toBe(svg);
		for (const svg of [
			'<svg onload="alert(1)"></svg>',
			'<svg><script>alert(1)</script></svg>',
			'<svg><foreignObject/></svg>',
			'<svg><image href="https://example.com/a"/></svg>',
			'<svg><path fill="url(https://example.com/a)"/></svg>',
			'<!DOCTYPE svg><svg/>',
			'<svg><text>&lt;script&gt;</text></svg>',
			'<svg><g></svg>',
			'<svg/><svg/>',
			'<svg style="color:red"/>'
		])
			expect(() => sanitizeSvg(svg)).toThrow();
	});
	it('normalizes persisted shared values to declared controls', () => {
		const input = validate(
			node('Input', { title: 'Name', stateKey: 'name', value: 'old', maxLength: 4 })
		);
		expect(extendedSharedValue(input, 'name', () => 'long string')).toBe('long');
		expect(extendedSharedValue(input, 'name', () => false)).toBe('old');
		expect(
			extendedSharedValue(node('Conditional', { stateKey: 'name', equals: 'x' }), 'name', () => 'x')
		).toBeUndefined();
	});
	it('executes only bounded declared-state actions', () => {
		const state: Record<string, string | number | boolean> = {
			count: 2,
			on: false,
			from: 'ok',
			to: ''
		};
		runActions(
			[
				{ type: 'increment', stateKey: 'count', amount: 3 },
				{ type: 'toggle', stateKey: 'on' },
				{ type: 'copy-state', stateKey: 'to', from: 'from' },
				{ type: 'set', stateKey: 'missing', value: 'ignored' },
				{ type: 'copy', keys: ['count'] }
			],
			(key) => state[key],
			(key, value) => {
				state[key] = value;
			}
		);
		expect(state).toEqual({ count: 5, on: true, from: 'ok', to: 'ok' });
	});
	it('uses explicit text tokens without recursive substitution', () => {
		expect(
			boundText(
				'Hello {{name}} {{unknown}}',
				[{ token: 'name', stateKey: 'user' }],
				() => '<b>{{unknown}}</b>'
			)
		).toBe('Hello <b>{{unknown}}</b> {{unknown}}');
	});
	it('collects clipboard and action state references', () => {
		const b = validate(
			node('Button', {
				label: 'Copy',
				actions: [
					{ type: 'copy', keys: ['count', 'mode'] },
					{ type: 'copy-state', stateKey: 'x', from: 'y' }
				]
			})
		);
		expect(extendedStateReferences(b)).toEqual(['count', 'mode', 'x', 'y']);
	});
});

it('allows only noninteractive Pressable descendants', () => {
	const press = node('Pressable', { label: 'Choose', actions: [] }, ['card']);
	const valid = {
		press,
		card: node('Card', {}, ['text']),
		text: node('Text', { text: 'Details', bindings: [] })
	};
	expect(() => validatePressableChildren(valid)).not.toThrow();
	expect(() =>
		validatePressableChildren({ ...valid, text: node('Button', { label: 'Nested', actions: [] }) })
	).toThrow();
	expect(() =>
		validatePressableChildren({
			...valid,
			text: node('Image', {
				src: 'https://example.com/image.png',
				alt: 'Example',
				source: 'https://example.com'
			})
		})
	).toThrow();
	expect(() =>
		validatePressableChildren({ ...valid, text: node('Tabs', { labels: ['A'] }) })
	).toThrow();
});
