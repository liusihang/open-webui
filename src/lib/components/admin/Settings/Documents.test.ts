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
});
