import { existsSync, readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

describe('conversation mode profile administrator components compile', () => {
	it.each([
		'ConversationModeProfiles.svelte',
		'ConversationModeProfileEditor.svelte',
		'../Models.svelte'
	])('compiles %s as a real Svelte component', (component) => {
		const filename = new URL(`./${component}`, import.meta.url);

		expect(existsSync(filename)).toBe(true);
		if (!existsSync(filename)) return;
		expect(() =>
			compile(readFileSync(filename, 'utf8'), { filename: filename.pathname, generate: 'client' })
		).not.toThrow();
	});
});
