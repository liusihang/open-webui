// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import StructuredOutputHarness from './StructuredOutputHarness.svelte';
import { applyResponseStreamEvent, type OutputItem } from './structuredOutput';
import {
	config,
	selectedTerminalId,
	settings,
	terminalServers,
	showTabs,
	showFileNavPath
} from '$lib/stores';
import { getCommentSurfaces } from '$lib/components/comments/anchors';
import { getFileContentById } from '$lib/apis/files';
import { downloadFileBlob, downloadFilePreview, readFile } from '$lib/apis/terminal';

vi.mock('$lib/apis/files', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	getFileContentById: vi.fn()
}));

// Editor/document providers are unrelated to commentary; keep real Markdown and Collapsible.
vi.mock('$lib/components/chat/Messages/CodeBlock.svelte', async () => ({
	default: (await import('./IntelligentUI/CodeFallbackHarness.svelte')).default
}));
// jsdom has no Web Animations API; test real collapse state without transition timing.
vi.mock('svelte/transition', () => ({ slide: () => ({ duration: 0 }) }));
vi.mock('$lib/components/chat/FileNav/FilePreview.svelte', async () => ({
	default: (await import('./IntelligentUI/CodeFallbackHarness.svelte')).default
}));
vi.mock('$lib/apis/terminal', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	downloadFileBlob: vi.fn(),
	downloadFilePreview: vi.fn(),
	readFile: vi.fn()
}));

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
	vi.restoreAllMocks();
	settings.set({});
	terminalServers.set([]);
	selectedTerminalId.set(null);
	showTabs.set(false);
	showFileNavPath.set(null);
	localStorage.removeItem('token');
	config.set(undefined);
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

describe('terminal file cards mounted rendering', () => {
	beforeEach(() => {
		localStorage.setItem('token', 'fixture-session-token');
		vi.mocked(downloadFileBlob).mockReset();
		config.set({ features: { enable_tool_servers: true } } as any);
	});
	function fileOutput(paths: string[]): OutputItem[] {
		return paths.flatMap((path, index) => [
			{
				type: 'function_call',
				name: 'display_file',
				call_id: `file-${index}`,
				arguments: JSON.stringify({ path })
			},
			{
				type: 'function_call_output',
				call_id: `file-${index}`,
				status: 'completed',
				output: [
					{
						type: 'input_text',
						text: JSON.stringify({
							type: 'file',
							source: 'open_terminal',
							exists: true,
							path,
							terminal_selector: 'terminals',
							session_id: 'original-session'
						})
					}
				]
			}
		]);
	}
	const servers = [
		{
			id: 'terminals',
			url: '/api/v1/terminals/terminals',
			contexts: { chat: { context_id: 'chat_id' } }
		}
	];
	const download = (target: HTMLElement) =>
		target.querySelector<HTMLButtonElement>('[aria-label="Download"]')!;

	it.each([{}, { terminalFileDisplay: 'sidebar' as const }])(
		'shows three historical path-only cards with settings %o',
		(preference) => {
			settings.set(preference);
			const target = setup({
				initialOutput: fileOutput(['/report.docx', '/progress.docx', '/package.zip']),
				initialDone: true
			});
			expect(target.querySelectorAll('[data-terminal-file]')).toHaveLength(3);
			expect(target.textContent).toContain('package.zip');
		}
	);

	it('uses the recorded terminal even when another terminal is selected, and keeps ZIP download-only', async () => {
		vi.mocked(readFile).mockClear();
		vi.mocked(downloadFileBlob).mockReset();
		terminalServers.set(servers);
		selectedTerminalId.set('another-terminal');
		const target = setup({ initialOutput: fileOutput(['/package.zip']), initialDone: true });
		expect(download(target).disabled).toBe(false);
		expect(target.textContent).toContain('No preview available');
		expect(readFile).not.toHaveBeenCalled();
		expect(downloadFileBlob).not.toHaveBeenCalled();
		const NativeURL = URL;
		vi.stubGlobal(
			'URL',
			class extends NativeURL {
				static createObjectURL = vi.fn(() => 'blob:download');
				static revokeObjectURL = vi.fn();
			}
		);
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		vi.mocked(downloadFileBlob).mockResolvedValue({
			blob: new Blob(['zip']),
			filename: 'package.zip'
		});
		download(target).click();
		await tick();
		expect(downloadFileBlob).toHaveBeenCalledWith(
			'/api/v1/terminals/terminals',
			expect.any(String),
			'/package.zip',
			'original-session'
		);
		await vi.waitFor(() => expect(click).toHaveBeenCalledOnce());
		expect(URL.revokeObjectURL).not.toHaveBeenCalled();
		await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download'), {
			timeout: 2000
		});
	});

	it('downloads uploaded files through the authorized file API without terminal discovery', async () => {
		config.set(undefined);
		terminalServers.set(null);
		const output = fileOutput(['/package.zip']);
		output[1].output![0].text = JSON.stringify({
			type: 'file',
			source: 'open_webui',
			exists: true,
			displayed: true,
			path: '/api/v1/files/upload-id/content',
			name: 'package.zip'
		});
		const target = setup({ initialOutput: output, initialDone: true });
		expect(download(target).disabled).toBe(false);
		expect(target.querySelector('[aria-label="Open"]')).toBeNull();
		const NativeURL = URL;
		vi.stubGlobal(
			'URL',
			class extends NativeURL {
				static createObjectURL = vi.fn(() => 'blob:uploaded');
				static revokeObjectURL = vi.fn();
			}
		);
		const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		vi.mocked(getFileContentById).mockResolvedValue(new Uint8Array([1, 2, 3]) as any);
		download(target).click();
		await vi.waitFor(() => expect(click).toHaveBeenCalledOnce());
		expect(getFileContentById).toHaveBeenCalledWith('upload-id');
		expect(downloadFileBlob).not.toHaveBeenCalled();
		await vi.waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:uploaded'), {
			timeout: 2000
		});
	});

	it('becomes downloadable when terminal discovery finishes after the message renders', async () => {
		terminalServers.set([]);
		const target = setup({ initialOutput: fileOutput(['/package.zip']), initialDone: true });
		expect(download(target).disabled).toBe(true);
		terminalServers.set(servers);
		await tick();
		expect(download(target).disabled).toBe(false);
	});

	it.each([{ initialServers: null }, { initialServers: [] }])(
		'can download a recorded system file without waiting for tool discovery: $initialServers',
		({ initialServers }) => {
			terminalServers.set(initialServers);
			const output = fileOutput(['/package.zip']);
			const result = JSON.parse(String(output[1].output?.[0].text));
			output[1].output![0].text = JSON.stringify({ ...result, terminal_id: 'terminals' });
			const target = setup({ initialOutput: output, initialDone: true });
			expect(download(target).disabled).toBe(false);
		}
	);

	it('waits for application configuration before starting a preview, then loads automatically', async () => {
		config.set(undefined);
		terminalServers.set(null);
		vi.mocked(downloadFilePreview)
			.mockReset()
			.mockResolvedValue({
				blob: { arrayBuffer: async () => new ArrayBuffer(4) } as Blob,
				filename: 'report.pdf'
			});
		const output = fileOutput(['/report.docx']);
		const result = JSON.parse(String(output[1].output?.[0].text));
		output[1].output![0].text = JSON.stringify({ ...result, terminal_id: 'terminals' });
		const target = setup({ initialOutput: output, initialDone: true });
		expect(download(target).disabled).toBe(true);
		expect(downloadFilePreview).not.toHaveBeenCalled();
		config.set({ features: { enable_tool_servers: true } } as any);
		await tick();
		expect(download(target).disabled).toBe(false);
		await vi.waitFor(() => expect(downloadFilePreview).toHaveBeenCalledOnce());
		expect(target.textContent).not.toContain('Tool servers are disabled');
	});

	it('loads DOCX previews from the original file session without requiring terminal selection', async () => {
		terminalServers.set(servers);
		vi.mocked(downloadFilePreview).mockReset().mockResolvedValue(null);
		vi.mocked(downloadFileBlob)
			.mockReset()
			.mockResolvedValue({
				blob: { arrayBuffer: async () => new ArrayBuffer(4) } as Blob,
				filename: 'report.docx'
			});
		const target = setup({ initialOutput: fileOutput(['/report.docx']), initialDone: true });
		await tick();
		expect(download(target).disabled).toBe(false);
		expect(downloadFilePreview).toHaveBeenCalledWith(
			'/api/v1/terminals/terminals',
			expect.any(String),
			'/report.docx',
			'original-session'
		);
		await vi.waitFor(() =>
			expect(downloadFileBlob).toHaveBeenCalledWith(
				'/api/v1/terminals/terminals',
				expect.any(String),
				'/report.docx',
				'original-session'
			)
		);
	});
});

describe('native commentary mounted rendering', () => {
	it('keeps separate comment anchors on answer and expanded commentary', async () => {
		const target = setup({
			initialOutput: [progress, answer],
			initialDone: true,
			chatId: 'saved-chat',
			messageId: 'assistant-id'
		});
		toggle(target).click();
		await tick();
		const surfaces = [...getCommentSurfaces().entries()].filter(([node]) => target.contains(node));
		expect(surfaces.map(([, options]) => options.surface_id).sort()).toEqual([
			'answer',
			'progress'
		]);
		for (const [, options] of surfaces) {
			expect(options).toMatchObject({
				resource_id: 'saved-chat',
				target_id: 'assistant-id',
				enabled: true
			});
		}
	});

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
