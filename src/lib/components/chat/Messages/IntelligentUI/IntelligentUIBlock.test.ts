import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import IntelligentUIBlock from './IntelligentUIBlock.svelte';
import { createTestI18n } from './test-i18n';
import { parseIntelligentUI } from '$lib/utils/intelligent-ui';

const block = (
	type: string,
	props: Record<string, unknown>,
	datasets: Record<string, unknown[]> = {}
) =>
	[
		...Object.entries(datasets).map(([key, value]) =>
			JSON.stringify({ op: 'add', path: `/datasets/${key}`, value })
		),
		JSON.stringify({ op: 'add', path: '/root', value: 'view' }),
		JSON.stringify({ op: 'add', path: '/elements/view', value: { type, props, children: [] } })
	].join('\n');

function html(source: string, done = true, locale = 'en-US') {
	return render(IntelligentUIBlock, {
		props: { source, done, blockId: 'message:block:0' },
		context: new Map([['i18n', createTestI18n(locale)]])
	}).body;
}

describe('trusted Intelligent UI rendering', () => {
	it.each([
		['zh-CN', '正在生成内容…'],
		['zh-TW', '正在產生內容…']
	])('localizes pending state without exposing a generic toolbar in %s', (locale, pending) => {
		const result = html('', false, locale);
		expect(result).toContain(pending);
		expect(result).not.toMatch(/Interactive view|View data|Hide data|查看数据|檢視資料/);
		expect(result).not.toContain('<button');
	});
	it('localizes the invalid-data fallback without translating or executing model content', () => {
		const result = html(block('Iframe', { src: '<script>example</script>' }), true, 'zh-CN');
		expect(result).toContain('暂时无法显示此内容，请让模型改用文字回答。');
		expect(result).not.toContain('Iframe');
		expect(result).not.toContain('<pre');
		expect(result).not.toContain('<script>example</script>');
	});

	it.each([
		{ type: 'WritingBlock', props: { title: 'Draft', content: 'Editable document.' } },
		{ type: 'CodeBlock', props: { title: 'Code example', language: 'python', code: 'print(42)' } },
		{ type: 'AppBlock', props: { title: 'Application', html: '<p>Content</p>' } },
		{ type: 'Chart', props: { title: 'Results', kind: 'bar', data: [{ label: 'A', value: 2 }] } },
		{
			type: 'DataTable',
			props: { title: 'Results', dataSource: 'rows', columns: [{ key: 'value', label: 'Value' }] },
			datasets: { rows: [{ value: 2 }] }
		},
		{ type: 'Math', props: { expression: 'x^2', display: true } },
		{ type: 'Image', props: { src: 'https://example.org/image.png', alt: 'Example image' } },
		{ type: 'MetricCard', props: { title: 'Count', value: 2 } },
		{
			type: 'NumberInput',
			props: { title: 'Count', stateKey: 'count', min: 0, max: 10, step: 1, value: 2 }
		},
		{ type: 'Explanation', props: { title: 'Explanation', content: 'Readable content.' } }
	])('renders $type without a generic toolbar, source viewer or outer card', (sample) => {
		const source = block(sample.type, sample.props, sample.datasets);
		expect(parseIntelligentUI(source, true).error).toBeNull();
		const result = html(source);
		expect(result).toContain('data-intelligent-ui="message:block:0"');
		expect(result).not.toMatch(/Interactive view|View data|Hide data/);
		expect(result).not.toContain('"op":"add"');
		expect(result).not.toMatch(/class="intelligent-ui[^"\n]*(?:\bborder\b|\bshadow-sm\b|\bp-4\b)/);
	});
	it('renders a chart through json-render with accessible exact values', () => {
		const result = html(
			block('Chart', {
				title: 'Revenue',
				kind: 'bar',
				data: [
					{ label: 'Q1', value: 12 },
					{ label: 'Q2', value: -3 }
				]
			})
		);
		expect(result).toContain('<svg');
		expect(result).toContain('Revenue');
		expect(result).toContain('Q1: 12');
		expect(result).toContain('Q2: -3');
		expect(result).toContain('View values');
	});
	it('renders completed operations before the response finishes', () => {
		expect(
			html(
				block('Explanation', { title: 'Why this works', content: 'The explanation.' }) + '\n{"op":',
				false
			)
		).toContain('Why this works');
	});
	it('escapes model text instead of inserting executable markup', () => {
		const result = html(
			block('Explanation', { title: 'Details', content: '<script>alert(1)</script>' })
		);
		expect(result).not.toContain('<script>alert(1)</script>');
		expect(result).toContain('&lt;script>');
	});
	it('renders comparison cards', () => {
		expect(
			html(
				block('ComparisonCards', {
					title: 'Options',
					items: [
						{
							title: 'A',
							description: 'First option',
							attributes: [{ label: 'Cost', value: '$5' }]
						}
					]
				})
			)
		).toContain('First option');
	});
	it('disables model actions when there is no writable assistant host', () => {
		const result = html(
			block('ParameterSlider', {
				title: 'Budget',
				min: 0,
				max: 100,
				step: 1,
				value: 20,
				followUp: 'Reconsider the plan.'
			})
		);
		expect(result).toContain('type="range"');
		expect(result).toContain('disabled');
		expect(result).toContain('Ask model with this value');
	});
	it('fails closed without exposing the UI source on unsupported components', () => {
		const result = html(block('Iframe', { src: 'https://example.com' }));
		expect(result).not.toContain('<iframe');
		expect(result).toContain('could not be displayed');
		expect(result).not.toContain('Iframe');
		expect(result).not.toContain('https://example.com');
		expect(result).not.toContain('<pre');
	});
});
