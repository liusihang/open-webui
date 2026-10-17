import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import IntelligentUIBlock from './IntelligentUIBlock.svelte';
import { createTestI18n } from './test-i18n';

const block = (type: string, props: Record<string, unknown>) =>
	[
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
		['zh-CN', '交互视图', '查看数据', '正在生成交互视图…'],
		['zh-TW', '互動檢視', '檢視資料', '正在產生互動檢視…']
	])(
		'localizes system labels and pending state with the bundled %s dictionary',
		(locale, title, data, pending) => {
			const result = html('', false, locale);
			expect(result).toContain(title);
			expect(result).toContain(data);
			expect(result).toContain(pending);
			expect(result).not.toContain('Preparing interactive view');
		}
	);
	it('localizes the invalid-data fallback without translating or executing model content', () => {
		const result = html(block('Iframe', { src: '<script>example</script>' }), true, 'zh-CN');
		expect(result).toContain('无法显示此交互视图。下方保留了原始数据。');
		expect(result).toContain('Iframe');
		expect(result).not.toContain('<script>example</script>');
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
	it('fails closed with readable source on unsupported components', () => {
		const result = html(block('Iframe', { src: 'https://example.com' }));
		expect(result).not.toContain('<iframe');
		expect(result).toContain('could not be displayed');
		expect(result).toContain('Iframe');
	});
});
