import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('KnowledgeBase Files source', () => {
	it('exposes a per-file regenerate action in the file list', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('onRegenerateLayer');
		expect(source).toContain('Regenerate Layer');
		expect(source).toContain('<ArrowPath');
	});
});
