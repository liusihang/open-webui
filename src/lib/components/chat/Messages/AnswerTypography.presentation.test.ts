import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const appCss = readFileSync(new URL('../../../../app.css', import.meta.url), 'utf8');
const messageSource = readFileSync(new URL('./Message.svelte', import.meta.url), 'utf8');
const contentRendererSource = readFileSync(
	new URL('./ContentRenderer.svelte', import.meta.url),
	'utf8'
);

const markdownProse = appCss.match(/\.markdown-prose\s*\{([\s\S]*?)\n\}/)?.[1] ?? '';
const renderedAssistantMarkdownBranch =
	contentRendererSource.match(
		/\{:else if \$settings\?\.renderMarkdownInAssistantMessages[\s\S]*?\}([\s\S]*?)\{:else\}/
	)?.[1] ?? '';

describe('assistant answer typography', () => {
	it('uses the ChatGPT reading size instead of the compact v0.11 typography', () => {
		expect(markdownProse).toContain('!text-base');
		expect(markdownProse).toContain('!leading-6');
		expect(markdownProse).toContain('prose-p:leading-6');
		expect(markdownProse).not.toContain('!text-[0.9375rem]');
		expect(markdownProse).not.toContain('leading-relaxed');
	});

	it('keeps ChatGPT-like paragraph, heading, list, and emphasis rhythm', () => {
		expect(markdownProse).toContain('prose-p:my-1');
		expect(markdownProse).toContain('[&_p+p]:!my-4');
		expect(markdownProse).toContain('prose-headings:font-semibold');
		expect(markdownProse).toContain('prose-h1:text-2xl');
		expect(markdownProse).toContain('prose-h2:text-xl');
		expect(markdownProse).toContain('prose-h3:text-lg');
		expect(markdownProse).toContain('prose-strong:font-semibold');
		expect(markdownProse).toContain('prose-ul:my-0');
		expect(markdownProse).toContain('prose-ol:my-0');
		expect(markdownProse).toContain('prose-li:my-0');
	});

	it('keeps the answer column aligned with the default input width', () => {
		expect(messageSource).toContain("'max-w-[58rem]'");
		expect(messageSource).not.toContain("'max-w-[48rem]'");
	});

	it('applies the answer typography contract to ordinary assistant markdown', () => {
		expect(renderedAssistantMarkdownBranch).toMatch(
			/<div class="markdown-prose">[\s\S]*?<Markdown/
		);
	});
});
