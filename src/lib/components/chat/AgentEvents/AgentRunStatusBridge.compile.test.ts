import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

describe('Agent transcript component compilation', () => {
	it.each([
		'AgentRunStatusBridge.svelte',
		'AgentTranscript.svelte',
		'TranscriptPart.svelte',
		'ApprovalPart.svelte',
		'UserInputPart.svelte'
	])('compiles %s as a real Svelte component', (component) => {
		const filename = new URL(`./${component}`, import.meta.url);
		const source = readFileSync(filename, 'utf8');

		expect(() => compile(source, { filename: filename.pathname, generate: false })).not.toThrow();
	});
});
