// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import StructuredOutputHarness from './StructuredOutputHarness.svelte';
import { applyResponseStreamEvent, type OutputItem } from './structuredOutput';

// Editor/document providers are unrelated to commentary; keep real Markdown and Collapsible.
vi.mock('$lib/components/chat/Messages/CodeBlock.svelte', async () => ({
	default: (await import('./IntelligentUI/CodeFallbackHarness.svelte')).default
}));
// jsdom has no Web Animations API; test real collapse state without transition timing.
vi.mock('svelte/transition', () => ({ slide: () => ({ duration: 0 }) }));

const message = (id: string, text: string, phase?: string): OutputItem => ({
	id,
	type: 'message',
	role: 'assistant',
	...(phase === undefined ? {} : { phase }),
	content: [{ type: 'output_text', text }]
});
const progress = message('progress', 'Checking **inputs**.', 'commentary');
const answer = message('answer', 'The final **answer**.', 'final_answer');
let app: ReturnType<typeof mount> | undefined;

afterEach(async () => {
	if (app) await unmount(app);
	app = undefined;
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
});

function setup(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	app = mount(StructuredOutputHarness, { target, props });
	flushSync();
	return target;
}

async function update(output: OutputItem[], done: boolean) {
	(app as unknown as { update: (output: OutputItem[], done: boolean) => void }).update(
		output,
		done
	);
	await tick();
	// Streaming Markdown is intentionally parsed on the next animation frame.
	await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
	await tick();
	flushSync();
}

const text = (target: HTMLElement) =>
	(target.textContent ?? '').replace(/\s+/g, ' ').replace(/\s+([.,])/g, '$1');

const toggle = (target: HTMLElement) =>
	target.querySelector<HTMLButtonElement>('[data-commentary] button')!;

describe('native commentary mounted rendering', () => {
	it('shows real streaming commentary and collapses only after the answer completes', async () => {
		let output = applyResponseStreamEvent([], {
			type: 'response.output_item.added',
			item: message('progress', '', 'commentary')
		});
		const target = setup({ initialOutput: output });
		output = applyResponseStreamEvent(output, {
			type: 'response.output_text.delta',
			item_id: 'progress',
			delta: 'Checking **inputs**.'
		});
		await update(output, false);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		expect(target.querySelector('[data-commentary] strong')?.textContent?.trim()).toBe('inputs');
		await update([...output, answer], false);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		expect(text(target)).toContain('The final answer.');
		await update([...output, answer], true);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('false');
		expect(text(target)).toContain('The final answer.');
	});

	it('defaults completed history to collapsed and lets the native button expand/collapse', async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal('fetch', fetchSpy);
		const target = setup({ initialOutput: [progress, answer], initialDone: true });
		expect(toggle(target).type).toBe('button');
		expect(toggle(target).getAttribute('aria-expanded')).toBe('false');
		toggle(target).focus();
		expect(document.activeElement).toBe(toggle(target));
		toggle(target).click();
		await tick();
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		expect(target.querySelector('[data-commentary] strong')?.textContent?.trim()).toBe('inputs');
		await update([message('progress', 'Updated progress.', 'commentary'), answer], true);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		toggle(target).click();
		await tick();
		expect(toggle(target).getAttribute('aria-expanded')).toBe('false');
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it.each([{ tail: [] }, { tail: [message('empty', ' ', 'final_answer')] }])(
		'keeps commentary visible when completed without a nonempty answer ($tail)',
		({ tail }) => {
			const target = setup({ initialOutput: [progress, ...tail], initialDone: true });
			expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
			expect(text(target)).toContain('Checking inputs.');
		}
	);

	it.each([undefined, 'final_answer', 'unknown'])(
		'preserves phase %s as normal body rendering',
		(phase) => {
			const target = setup({
				initialOutput: [message('ordinary', 'Ordinary **body**.', phase)],
				initialDone: true
			});
			expect(target.querySelector('[data-commentary]')).toBeNull();
			expect(target.querySelector('.markdown-prose strong')?.textContent?.trim()).toBe('body');
		}
	);

	it('keeps commentary and ordinary messages in source order', async () => {
		const target = setup({
			initialOutput: [
				progress,
				message('middle', 'Middle body.'),
				message('second', 'Second update.', 'commentary'),
				answer
			]
		});
		await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
		await tick();
		const body = text(target);
		expect(body).toContain('Checking inputs.');
		expect(body.indexOf('Checking inputs.')).toBeLessThan(body.indexOf('Middle body.'));
		expect(body.indexOf('Middle body.')).toBeLessThan(body.indexOf('Second update.'));
		expect(body.indexOf('Second update.')).toBeLessThan(body.indexOf('The final answer.'));
	});

	it('applies a late native phase and then an authoritative final_answer replacement', async () => {
		const target = setup({ initialOutput: [message('late', 'Checking.')] });
		expect(target.querySelector('[data-commentary]')).toBeNull();
		let output = applyResponseStreamEvent([message('late', 'Checking.')], {
			type: 'response.output_item.done',
			item: message('late', 'Checking.', 'commentary')
		});
		await update(output, true);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		output = applyResponseStreamEvent(output, {
			type: 'response.output_item.done',
			item: message('late', 'Final body.', 'final_answer')
		});
		await update(output, true);
		expect(target.querySelector('[data-commentary]')).toBeNull();
		expect(target.textContent).toContain('Final body.');
	});

	it('uses literal escaped text in text-only mode, including compact preview', async () => {
		const target = setup({
			initialOutput: [
				message('progress', '<script>bad()</script> **literal**', 'commentary'),
				answer
			],
			initialDone: true,
			renderMarkdown: false,
			preview: true,
			compactPreview: true
		});
		toggle(target).click();
		await tick();
		expect(target.querySelector('[data-commentary] script')).toBeNull();
		expect(target.querySelector('[data-commentary] strong')).toBeNull();
		expect(target.textContent).toContain('<script>bad()</script> **literal**');
		expect(target.textContent).toContain('The final **answer**.');
	});

	it('uses the existing Chinese i18n context in Markdown preview', () => {
		const target = setup({
			initialOutput: [progress],
			initialDone: true,
			preview: true,
			compactPreview: true,
			locale: 'zh-CN'
		});
		expect(toggle(target).textContent).toContain('进度说明');
		expect(target.querySelector('[data-commentary] strong')?.textContent?.trim()).toBe('inputs');
	});

	it('collapses when an ordinary legacy body arrives after a commentary-only completion', async () => {
		const target = setup({ initialOutput: [progress], initialDone: true });
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
		await update([progress, message('legacy-answer', 'Legacy body.')], true);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('false');
		expect(target.textContent).toContain('Legacy body.');
		await update([progress], true);
		expect(toggle(target).getAttribute('aria-expanded')).toBe('true');
	});

	it('never labels a reasoning summary as progress even with a commentary phase', () => {
		const target = setup({
			initialOutput: [
				{
					type: 'reasoning',
					phase: 'commentary',
					status: 'completed',
					summary: [{ text: 'Private reasoning summary.' }]
				},
				answer
			],
			initialDone: true
		});
		expect(target.querySelector('[data-commentary]')).toBeNull();
		expect(text(target)).not.toContain('Progress updates');
		expect(text(target)).toContain('The final answer.');
	});
});
