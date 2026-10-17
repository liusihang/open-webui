import { describe, expect, it } from 'vitest';
import { marked, type Tokens } from 'marked';
import { parseIntelligentUI } from './index';

const root = JSON.stringify({ op: 'add', path: '/root', value: 'main' });
const explanation = {
	type: 'Explanation',
	props: { title: 'First view', content: 'Inline explanation.' },
	children: []
};
const node = JSON.stringify({ op: 'add', path: '/elements/main', value: explanation });
const body = `${root}\n${node}`;
const fence = (source: string) => `\`\`\`intelligent-ui\n${source}\n\`\`\``;
const codeTokens = (source: string) =>
	marked.lexer(source).filter((token): token is Tokens.Code => token.type === 'code');

describe('intelligent-ui Markdown lexer seam', () => {
	it('preserves prose/fence/prose/fence order and gives each view its own body', () => {
		const second = JSON.stringify({
			root: 'second',
			elements: {
				second: { ...explanation, props: { title: 'Second view', content: 'A separate view.' } }
			}
		});
		const source = `Before the first view.\n\n${fence(body)}\n\nBetween the views.\n\n${fence(second)}\n\nAfter both views.`;
		const tokens = marked.lexer(source).filter((token) => token.type !== 'space');
		expect(tokens.map((token) => token.type)).toEqual([
			'paragraph',
			'code',
			'paragraph',
			'code',
			'paragraph'
		]);
		const views = codeTokens(source);
		expect(views.map((token) => token.lang)).toEqual(['intelligent-ui', 'intelligent-ui']);
		expect(views.map((token) => token.text)).toEqual([body, second]);
		expect(views.map((token) => parseIntelligentUI(token.text, true).spec?.root)).toEqual([
			'main',
			'second'
		]);
	});

	it('emits an unterminated intelligent-ui code token throughout the partial second operation', () => {
		for (let split = 0; split <= node.length; split++) {
			const source = `Useful prose first.\n\n\`\`\`intelligent-ui\n${root}\n${node.slice(0, split)}`;
			const [token] = codeTokens(source);
			expect(token?.lang, `split ${split}`).toBe('intelligent-ui');
			const result = parseIntelligentUI(token.text, false);
			expect(result.error, `split ${split}`).toBeNull();
			expect(result.pending).toBe(true);
			if (split < node.length) expect(result.spec).toBeNull();
			else expect(result.spec?.elements.main).toEqual(explanation);
		}
	});

	it('keeps an already rendered view through partial replacement records and closing backticks', () => {
		const updated = {
			...explanation,
			props: { title: 'Updated view', content: 'Updated explanation.' }
		};
		const update = JSON.stringify({ op: 'replace', path: '/elements/main', value: updated });
		const tail = `${update}\n\`\`\``;
		for (let split = 0; split <= tail.length; split++) {
			const source = `\`\`\`intelligent-ui\n${body}\n${tail.slice(0, split)}`;
			const [token] = codeTokens(source);
			const result = parseIntelligentUI(token.text, false);
			expect(result.error, `split ${split}`).toBeNull();
			expect(result.spec?.elements.main).toEqual(split < update.length ? explanation : updated);
		}
	});

	it('rejects a truncated final token once the assistant response is complete', () => {
		const [token] = codeTokens(`\`\`\`intelligent-ui\n${root}\n${node.slice(0, -1)}`);
		const result = parseIntelligentUI(token.text, true);
		expect(result.spec).toBeNull();
		expect(result.error).toBeTruthy();
	});

	it('leaves other code languages identifiable for normal Markdown code rendering', () => {
		const source = `\`\`\`json\n{"hello":"world"}\n\`\`\`\n\n${fence(body)}\n\n\`\`\`javascript\nconsole.log('normal code');\n\`\`\``;
		const tokens = codeTokens(source);
		expect(tokens.map((token) => token.lang)).toEqual(['json', 'intelligent-ui', 'javascript']);
		expect(tokens.filter((token) => token.lang === 'intelligent-ui')).toHaveLength(1);
	});
});

describe('expanded intelligent-ui Markdown streaming seam', () => {
	it('preserves partial dataset replacement and tabs through every arriving character', () => {
		const rows = [{ id: 'one', label: 'A', value: 10 }];
		const updated = [{ id: 'two', label: 'B', value: 20 }];
		const operations = [
			{ op: 'add', path: '/root', value: 'main' },
			{
				op: 'add',
				path: '/elements/main',
				value: { type: 'Tabs', props: { labels: ['Chart'] }, children: ['chart'] }
			},
			{
				op: 'add',
				path: '/elements/chart',
				value: {
					type: 'Chart',
					props: {
						title: 'Amounts',
						kind: 'bar',
						dataSource: 'rows',
						xField: 'label',
						yField: 'value'
					},
					children: []
				}
			},
			{ op: 'add', path: '/datasets/rows', value: rows }
		]
			.map((operation) => JSON.stringify(operation))
			.join('\n');
		const replacement = JSON.stringify({ op: 'replace', path: '/datasets/rows', value: updated });
		const tail = `${replacement}\n\`\`\``;
		for (let index = 0; index <= tail.length; index++) {
			const [token] = codeTokens(
				`Text before.\n\n\`\`\`intelligent-ui\n${operations}\n${tail.slice(0, index)}`
			);
			const result = parseIntelligentUI(token.text, false);
			expect(result.error, `expanded prefix ${index}`).toBeNull();
			expect(result.spec?.elements.main.children).toEqual(['chart']);
			expect(result.spec?.datasets?.rows).toEqual(index < replacement.length ? rows : updated);
		}
		const [token] = codeTokens(fence(`${operations}\n${replacement}`));
		expect(parseIntelligentUI(token.text, true).error).toBeNull();
	});
});
