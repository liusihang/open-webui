// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { config, settings } from '$lib/stores';
import MarkdownTokens from '../Markdown/MarkdownTokens.svelte';
import { createTestI18n } from './test-i18n';

// The fallback is ordinary code; editor/provider and document integration are out of scope.
vi.mock('$lib/components/chat/Messages/CodeBlock.svelte', async () => ({
	default: (await import('./CodeFallbackHarness.svelte')).default
}));
vi.mock('$lib/utils', () => ({ copyToClipboard: vi.fn(), unescapeHtml: (s: string) => s }));

const source = [
	JSON.stringify({ op: 'add', path: '/root', value: 'view' }),
	JSON.stringify({
		op: 'add',
		path: '/elements/view',
		value: {
			type: 'Explanation',
			props: { title: 'Native config view', content: 'Details.' },
			children: []
		}
	})
].join('\n');
let instance: ReturnType<typeof mount> | undefined;
afterEach(async () => {
	if (instance) await unmount(instance);
	instance = undefined;
	config.set(undefined);
	settings.set({});
	document.body.innerHTML = '';
});

function setup(enabled: boolean | undefined, personalValue?: boolean, documentMode = false) {
	config.set({ features: { enable_intelligent_ui: enabled } } as any);
	// Deliberately emulate JSON persisted before this became an administrator setting.
	settings.set(personalValue === undefined ? {} : ({ intelligentUI: personalValue } as any));
	const target = document.createElement('div');
	document.body.append(target);
	instance = mount(MarkdownTokens, {
		target,
		props: {
			id: 'message',
			done: true,
			tokens: [{ type: 'code', lang: 'intelligent-ui', text: source, raw: source }]
		},
		context: new Map<string, unknown>([
			['i18n', createTestI18n('en-US')],
			['markdown', { document: documentMode }]
		])
	});
	flushSync();
	return target;
}

describe('administrator-controlled Intelligent UI Markdown rendering', () => {
	it('renders with shared config enabled and no personal setting', () => {
		const target = setup(true);
		expect(target.textContent).toContain('Native config view');
		expect(target.querySelector('[data-code-fallback]')).toBeNull();
	});
	it('renders with shared config enabled despite a legacy personal false', () => {
		const target = setup(true, false);
		expect(target.textContent).toContain('Native config view');
		expect(target.querySelector('[data-code-fallback]')).toBeNull();
	});
	it.each([false, undefined])(
		'uses ordinary code when shared config is %s despite personal true',
		(enabled) => {
			const target = setup(enabled, true);
			expect(target.querySelector('[data-code-fallback]')?.textContent).toContain(source);
			expect(target.textContent).not.toContain('Interactive view');
		}
	);
	it('reacts to backend configuration refreshes without changing personal settings', async () => {
		const target = setup(false, false);
		config.set({ features: { enable_intelligent_ui: true } } as any);
		await tick();
		expect(target.querySelector('[data-code-fallback]')).toBeNull();
		expect(target.textContent).toContain('Native config view');
		config.set({ features: { enable_intelligent_ui: false } } as any);
		await tick();
		expect(target.querySelector('[data-code-fallback]')?.textContent).toContain(source);
	});
	it('preserves the ordinary-code behavior of document rendering', () => {
		const target = setup(true, false, true);
		expect(target.querySelector('[data-code-fallback]')?.textContent).toContain(source);
	});
});
