import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

describe('conversation mode component compilation', () => {
	it.each([
		'ConversationModeSelector.svelte',
		'ComposerModelSettings.svelte',
		'ReasoningEffortSlider.svelte',
		'ModelSelector.svelte',
		'ModelSelector/Selector.svelte',
		'Navbar.svelte',
		'Chat.svelte',
		'MessageInput.svelte',
		'Placeholder.svelte'
	])('compiles %s as a real Svelte component', (component) => {
		const filename = new URL(`./${component}`, import.meta.url);
		const source = readFileSync(filename, 'utf8');

		expect(() => compile(source, { filename: filename.pathname, generate: false })).not.toThrow();
	});
});
