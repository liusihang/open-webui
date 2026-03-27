import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('Documents settings source', () => {
	it('does not expose Open Notebook layer generation fields in documents settings', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/admin/Settings/Documents.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).not.toContain('Open Notebook Base URL');
		expect(source).not.toContain('OPEN_NOTEBOOK_BASE_URL');
		expect(source).not.toContain('OPEN_NOTEBOOK_API_PASSWORD');
		expect(source).not.toContain('OPEN_NOTEBOOK_TIMEOUT_SECS');
		expect(source).not.toContain('OPEN_NOTEBOOK_TRANSFORMATION_ABSTRACT');
		expect(source).not.toContain('OPEN_NOTEBOOK_TRANSFORMATION_KEY_FINDINGS');
		expect(source).not.toContain('OPEN_NOTEBOOK_TRANSFORMATION_KEY_DATA');
	});

	it('exposes internal layer generation fields in documents settings', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/admin/Settings/Documents.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('Layer Generation');
		expect(source).toContain('Generation Model');
		expect(source).toContain('Max Chunk Tokens');
		expect(source).toContain('Min Tail Tokens');
		expect(source).toContain('Abstract Prompt');
		expect(source).toContain('LAYER_GENERATION_MODEL');
		expect(source).toContain('LAYER_GENERATION_MAX_CHUNK_TOKENS');
		expect(source).toContain('LAYER_GENERATION_MIN_TAIL_TOKENS');
		expect(source).toContain('LAYER_GENERATION_PROMPT_ABSTRACT');
	});
});
