import { describe, expect, it } from 'vitest';
import { Marked } from 'marked';
import citationExtension from '$lib/utils/marked/citation-extension';
import { inlineText } from './Markdown/inlineText';
import { createSearchCitationFormatter, removeNativeCitations } from './searchCitations';

const result = (results: any[], content = '', call_id = 'call_search') => [
	{ type: 'function_call', name: 'search_web', call_id },
	{
		type: 'function_call_output',
		call_id,
		output: [{ type: 'input_text', text: JSON.stringify({ results, content }) }]
	}
];

describe('saved search citations', () => {
	it('uses exact multi-turn refs, not source array indices; deduplicates multi-cite', () => {
		const output = result([
			{ ref_id: 'turn1search0', url: 'https://example.org/old', title: 'Old' },
			{ ref_id: 'turn2search0', url: 'https://example.org/current', title: 'Current' },
			{ ref_id: 'turn2search1', url: 'https://example.org/injury', title: 'Injury' }
		]);
		const format = createSearchCitationFormatter(output);
		expect(format('甲citeturn2search1turn2search0turn2search1；乙citeturn1search0')).toBe(
			'甲[Injury](https://example.org/injury) [Current](https://example.org/current)；乙[Old](https://example.org/old)'
		);
	});
	it('reads source headers when structured results were capped', () => {
		const format = createSearchCitationFormatter(
			result([], 'Outside cap (https://example.org/four)\nciteturn2search4 text')
		);
		expect(format('事实citeturn2search4')).toBe('事实[Outside cap](https://example.org/four)');
	});
	it('does not alter input or code, Unicode, existing citations, Markdown or images', () => {
		const output = result([{ ref_id: 'turn2search0', url: 'https://example.org/a', title: 'A' }]);
		const before = JSON.stringify(output);
		const format = createSearchCitationFormatter(output);
		const literal =
			'中文 [1] [链接](https://example.org/) ![图](https://example.org/i.png) `citeturn2search0`\n```text\nciteturn2search0\n```';
		expect(format(literal)).toBe(literal);
		expect(JSON.stringify(output)).toBe(before);
	});
	it('rejects unrelated and unmatched tool outputs and unsafe URLs', () => {
		const output = result([
			{ ref_id: 'turn2search0', url: 'javascript:alert(1)', title: 'Unsafe' }
		]);
		output[0].name = 'other_tool';
		expect(createSearchCitationFormatter(output)('citeturn2search0')).toBe(
			'（来源未解析：turn2search0）'
		);
		expect(createSearchCitationFormatter(output.slice(1))('citeturn2search0')).not.toContain(
			']('
		);
	});
	it('does not pick one of conflicting mappings or share state between messages', () => {
		const format = createSearchCitationFormatter(
			result([
				{ ref_id: 'turn0search0', url: 'https://example.org/a' },
				{ ref_id: 'turn0search0', url: 'https://example.org/b' }
			])
		);
		expect(format('citeturn0search0')).toContain('来源未解析');
		expect(createSearchCitationFormatter([])('citeturn0search0')).toContain('来源未解析');
	});
	it('retains utility data, navigation labels and incomplete-token meaning', () => {
		const format = createSearchCitationFormatter([]);
		const out = format('financeturn0finance0 AAPL 100\ncite0†Domains\nciteturn0search9');
		expect(out).not.toContain('');
		expect(out).toContain('AAPL 100');
		expect(out).toContain('Domains (link id: 0)');
		expect(out).toContain('来源未解析：turn0search9');
	});
	it('ignores source headers inside literal code and malformed outputs', () => {
		const output = result([], '```text\nFake (https://example.org/fake)\nciteturn0search0\n```');
		expect(createSearchCitationFormatter(output)('citeturn0search0')).toContain('来源未解析');
		expect(
			createSearchCitationFormatter([{ type: 'function_call_output', output: 'not json' }])('plain')
		).toBe('plain');
	});
});

describe('adjacent Markdown formats', () => {
	it('gives numeric Markdown links and reference links their real href', () => {
		const parser = new Marked(citationExtension());
		for (const source of [
			'[1](https://example.org/real)',
			'[1][ref]\n\n[ref]: https://example.org/real'
		]) {
			const links: any[] = [];
			parser.walkTokens(parser.lexer(source), (token) => {
				if (token.type === 'link') links.push(token);
				expect(token.type).not.toBe('citation');
			});
			expect(links.map((token) => token.href)).toEqual(['https://example.org/real']);
		}
	});
	it('stops merging native citations before a following numeric link', () => {
		const parser = new Marked(citationExtension());
		const tokens = parser.Lexer.lexInline('[1][2](https://example.org/real)', parser.defaults);
		expect(tokens.map((token) => token.type)).toEqual(['citation', 'link']);
		expect(tokens[0].raw).toBe('[1]');
	});
	it('disabling citations preserves numeric links, definitions, whitespace and code', () => {
		const source =
			'Keep [1] [2](https://example.org/real) `[3]`\n\n[4][ref]\n\n[ref]: https://example.org/ref';
		expect(removeNativeCitations(source)).toBe(source.replace('[1]', ''));
	});
	it('escapes source labels and URL delimiters inside Markdown tables', () => {
		const format = createSearchCitationFormatter(
			result([
				{
					ref_id: 'turn0search0',
					url: 'https://example.org/a|(b)',
					title: 'A | B\n[2026] `code` <img>'
				}
			])
		);
		const parser = new Marked(citationExtension());
		const source = format('| Source | Value |\n| --- | --- |\n| citeturn0search0 | 1 |');
		const table: any = parser.lexer(source)[0];
		expect(table.type).toBe('table');
		expect(table.rows[0]).toHaveLength(2);
		const links: any[] = [];
		parser.walkTokens([table], (token) => {
			if (token.type === 'link') links.push(token);
			expect(token.type).not.toBe('citation');
		});
		expect(links).toHaveLength(1);
		expect(links[0].href).toBe('https://example.org/a%7C%28b%29');
		expect(parser.parse(source)).not.toContain('<img>');
	});
	it('preserves parsed code literals including multiline and unclosed fences', () => {
		const marker = 'citeturn0search0';
		const format = createSearchCitationFormatter(
			result([{ ref_id: 'turn0search0', url: 'https://example.org/a', title: 'A' }])
		);
		for (const source of [
			`    ${marker}\n`,
			`>     ${marker}\n`,
			`\`\`line\n${marker}\`\``,
			`\`\`\`text\n${marker}`,
			`~~~text\n${marker}`,
			`<pre>${marker}</pre>`
		])
			expect(format(source)).toBe(source);
	});
	it('retains exact native citation markers in nested CSV inline text', () => {
		const parser = new Marked(citationExtension());
		const tokens = parser.Lexer.lexInline('**Evidence [2,3#part]** [4]', parser.defaults);
		expect(inlineText(tokens)).toBe('Evidence [2,3#part] [4]');
	});
});
