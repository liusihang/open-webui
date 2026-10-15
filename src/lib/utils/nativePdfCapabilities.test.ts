import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { DEFAULT_CAPABILITIES } from '../constants';

describe('native PDF capability configuration', () => {
	it('is opt-in independently of upstream vision and file context defaults', () => {
		expect(DEFAULT_CAPABILITIES.native_pdf).toBe(false);
		expect(DEFAULT_CAPABILITIES.vision).toBe(true);
		expect(DEFAULT_CAPABILITIES.file_upload).toBe(true);
	});

	it('exposes the opt-in capability in the existing model editor without replacing its toggles', () => {
		const source = readFileSync(
			new URL('../components/workspace/Models/Capabilities.svelte', import.meta.url),
			'utf8'
		);
		expect(source).toContain('native_pdf: {');
		expect(source).toContain("label: $i18n.t('Native PDF input')");
		expect(source).toContain('this model and its current route support native PDF input');
		expect(source).toContain('PDFs use text extraction');
		expect(source).toContain('bind:checked={capabilities[key]}');
		expect(() => compile(source, { filename: 'Capabilities.svelte', generate: 'client' })).not.toThrow();
	});
});
