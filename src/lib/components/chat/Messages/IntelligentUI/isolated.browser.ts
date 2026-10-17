// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import IsolatedBlocksHarness from './IsolatedBlocksHarness.svelte';
import {
	acceptAppMessage,
	buildAppDocument,
	sanitizeAppHTML
} from '$lib/utils/intelligent-ui/app-sandbox';
const mounted: any[] = [];
function render(props: any) {
	const target = document.createElement('div');
	document.body.append(target);
	const app = mount(IsolatedBlocksHarness, { target, props });
	mounted.push(app);
	flushSync();
	return { target, app };
}
afterEach(async () => {
	for (const app of mounted.splice(0)) await unmount(app);
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});
describe('isolated app boundary', () => {
	it('strips navigation, executable markup and external sources', () => {
		const clean = sanitizeAppHTML(
			'<iframe src="https://example.org"></iframe><img src="https://example.org/x"><script>alert(1)</script><form><input></form><a href="https://example.org">Link</a><button id="go" onclick="alert(1)">Go</button><input type="image" src="https://example.org/x"><canvas width="9999999" height="9999999"></canvas>'
		);
		expect(clean).not.toMatch(/iframe|img|script|form|href|onclick|src=/);
		expect(clean).toContain('<button id="go" type="button">Go</button>');
		expect(clean).toContain('width="1200" height="900"');
	});
	it('keeps untrusted code out of the DOM realm and embeds a deny-default CSP', () => {
		const doc = buildAppDocument(
			{
				title: 'Demo',
				html: '<p id="status">Hello</p>',
				css: '</style><img src=x>',
				js: '</script><script>fetch("https://example.org")</script>'
			},
			'abc123'
		);
		expect(doc.match(/<script /g)).toHaveLength(1);
		expect(doc).toContain("default-src 'none'");
		expect(doc).toContain("connect-src 'none'");
		expect(doc).toContain('new Worker(');
		expect(doc).not.toContain('allow-same-origin');
	});
	it('requires source, opaque origin, token, declared key and primitive bound', () => {
		const source = window;
		const valid = {
			source,
			origin: 'null',
			data: {
				channel: 'intelligent-ui-app',
				token: 'secret',
				type: 'write',
				key: 'threshold',
				value: 20
			}
		};
		expect(acceptAppMessage(valid, source, 'secret', ['threshold'])).toEqual({
			key: 'threshold',
			value: 20
		});
		for (const event of [
			{ ...valid, source: null },
			{ ...valid, origin: 'https://example.org' },
			{ ...valid, data: { ...valid.data, token: 'wrong' } },
			{ ...valid, data: { ...valid.data, key: 'other' } },
			{ ...valid, data: { ...valid.data, value: 'x'.repeat(257) } }
		])
			expect(acceptAppMessage(event, source, 'secret', ['threshold'])).toBeNull();
	});
	it('does not mount an executing iframe until done and keeps tokens stable for state writes', async () => {
		const view = { __key: 'demo', title: 'Demo', html: '<p>Hello</p>', stateKeys: ['threshold'] };
		const { target, app } = render({ kind: 'AppBlock', initialView: view, initialDone: false });
		expect(target.querySelector('iframe')).toBeNull();
		app.update(view, true);
		await tick();
		const frame = target.querySelector('iframe')!;
		expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
		const srcdoc = frame.srcdoc;
		app.update({ ...view }, true);
		await tick();
		expect(frame.srcdoc).toBe(srcdoc);
	});
});
describe('writing block', () => {
	it('edits, preserves append-only streams, handles revisions, and expands', async () => {
		const onWrite = vi.fn();
		const view = { __key: 'draft', title: 'Draft', content: 'Hello' };
		const { target, app } = render({ initialView: view, onWrite });
		(target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		const textarea = target.querySelector('textarea')!;
		textarea.value = 'Hi';
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		app.update({ ...view, content: 'Hello world' });
		await tick();
		expect(textarea.value).toBe('Hi world');
		app.update({ ...view, content: 'New revision' });
		await tick();
		expect(textarea.value).toBe('Hi world');
		expect(target.textContent).toContain('Your edits are preserved');
		expect(onWrite).toHaveBeenCalledWith('writing:draft', 'Hi world');
		const expand = Array.from(target.querySelectorAll('button')).find(
			(button) => button.textContent === 'Expand'
		)!;
		expand.click();
		await tick();
		expect(expand.getAttribute('aria-expanded')).toBe('true');
	});
	it('renders Markdown without executable HTML, remote images or links', () => {
		const { target } = render({
			initialView: {
				__key: 'draft',
				title: 'Draft',
				format: 'markdown',
				content:
					'**Strong** <img src="https://example.org/x" onerror="alert(1)"> [Link](javascript:alert(1))'
			}
		});
		expect(target.querySelector('strong')?.textContent).toBe('Strong');
		expect(target.querySelector('img,a,script')).toBeNull();
	});
	it('copies the edited text', async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
		const { target } = render({
			initialView: { __key: 'draft', title: 'Draft', content: 'Original' }
		});
		(target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		const textarea = target.querySelector('textarea')!;
		textarea.value = 'Edited';
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		Array.from(target.querySelectorAll('button'))
			.find((button) => button.textContent === 'Copy')!
			.click();
		await tick();
		expect(writeText).toHaveBeenCalledWith('Edited');
		await vi.waitFor(() => expect(target.textContent).toContain('Copied'));
	});
});

it('persists a draft edited during streaming when the production block finishes', async () => {
	const { default: TestHarness } = await import('./TestHarness.svelte');
	const source = JSON.stringify({
		root: 'doc',
		elements: {
			doc: {
				type: 'WritingBlock',
				props: { title: 'Streaming memo', content: 'Initial', format: 'text' },
				children: []
			}
		}
	});
	const target = document.createElement('div');
	document.body.append(target);
	const onWrite = vi.fn();
	const app = mount(TestHarness, {
		target,
		props: { initialSource: source, initialDone: false, onWrite }
	});
	mounted.push(app);
	flushSync();
	(
		Array.from(target.querySelectorAll('button')).find(
			(b) => b.textContent === 'Edit'
		) as HTMLButtonElement
	).click();
	flushSync();
	const field = target.querySelector('textarea')!;
	field.value = 'Edited while streaming';
	field.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	expect(onWrite).not.toHaveBeenCalled();
	app.updateSource(source, true);
	flushSync();
	await tick();
	expect(onWrite).toHaveBeenCalledWith(
		'first',
		expect.objectContaining({ 'writing:doc': 'Edited while streaming' })
	);
});
