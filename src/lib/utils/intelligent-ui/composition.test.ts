import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseIntelligentUI } from './index';
import { resolveSharedValue } from './data';
import { validInteractionValue } from './state-value';
const node = (type: string, props: any, children: string[] = []) => ({ type, props, children });
const spec = () => ({
	root: 'root',
	elements: {
		root: node('Stack', {}, ['input', 'flag', 'button', 'text', 'condition', 'app']),
		input: node('Input', { title: 'Name', stateKey: 'name', value: 'Demo' }),
		flag: node('Checkbox', { title: 'Enabled', stateKey: 'enabled', value: true }),
		button: node('Button', { label: 'Toggle', actions: [{ type: 'toggle', stateKey: 'enabled' }] }),
		text: node('Text', { text: 'Hello {{name}}', bindings: [{ token: 'name', stateKey: 'name' }] }),
		condition: node('Conditional', { stateKey: 'enabled', equals: true }, ['card']),
		card: node('Card', { title: 'Shown' }),
		app: node('AppBlock', {
			title: 'Local',
			html: '<p id="name"></p>',
			stateKeys: ['name'],
			js: "ui.onState(s=>ui.setText('name',s.name));"
		})
	}
});
describe('expanded catalog integration', () => {
	it('accepts documented end-to-end JSONL compositions', () => {
		const docs = readFileSync('docs/intelligent-ui.md', 'utf8');
		const examples = [...docs.matchAll(/```intelligent-ui\n([\s\S]*?)\n```/g)];
		expect(examples.length).toBeGreaterThanOrEqual(2);
		for (const [, body] of examples) expect(parseIntelligentUI(body, true).error, body).toBeNull();
	});
	it('links new controls, bounded actions, conditional views and app keys', () => {
		const parsed = parseIntelligentUI(JSON.stringify(spec()), true);
		expect(parsed.error).toBeNull();
		expect(resolveSharedValue(parsed.spec!, 'name', () => 'Updated')).toBe('Updated');
		expect(resolveSharedValue(parsed.spec!, 'enabled', () => false)).toBe(false);
	});
	it('rejects missing references, orphan providers, incompatible definitions and excluded components', () => {
		const missing = spec();
		missing.elements.app.props.stateKeys = ['unknown'];
		expect(parseIntelligentUI(JSON.stringify(missing), true).error).not.toBeNull();
		const orphan = spec();
		orphan.elements.root.children = orphan.elements.root.children.filter((id) => id !== 'input');
		expect(parseIntelligentUI(JSON.stringify(orphan), true).error).not.toBeNull();
		const conflict: any = spec();
		conflict.elements.other = node('Input', {
			title: 'Different',
			stateKey: 'name',
			value: 'Other'
		});
		expect(parseIntelligentUI(JSON.stringify(conflict), true).error).not.toBeNull();
		for (const type of ['Video', 'MapWidget', 'AsyncVideo'])
			expect(
				parseIntelligentUI(JSON.stringify({ root: 'x', elements: { x: node(type, {}) } }), true)
					.error
			).not.toBeNull();
	});
	it('keeps code inert and exposes only supported code props', () => {
		expect(
			parseIntelligentUI(
				JSON.stringify({
					root: 'x',
					elements: { x: node('CodeBlock', { language: 'mermaid', code: 'graph LR; A-->B' }) }
				}),
				true
			).error
		).toBeNull();
		expect(
			parseIntelligentUI(
				JSON.stringify({
					root: 'x',
					elements: {
						x: node('CodeBlock', { language: 'javascript', code: 'alert(1)', run: true })
					}
				}),
				true
			).error
		).not.toBeNull();
	});
	it('allows only bounded multiline shared values and writing drafts', () => {
		expect(validInteractionValue('writing:doc', 'x'.repeat(16384) + '\n')).toBe(false);
		expect(validInteractionValue('writing:doc', 'Long\n\tdraft')).toBe(true);
		expect(validInteractionValue('shared:notes', 'Line one\nLine two')).toBe(true);
		expect(validInteractionValue('shared:notes', 'x'.repeat(257))).toBe(false);
		expect(validInteractionValue('tab:root', 'bad\nvalue')).toBe(false);
		expect(validInteractionValue('writing:doc', 'bad\u0000value')).toBe(false);
		expect(validInteractionValue('shared:n', 1e13)).toBe(false);
	});
});
