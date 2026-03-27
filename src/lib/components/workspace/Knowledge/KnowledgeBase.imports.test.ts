import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('KnowledgeBase imports', () => {
	it('does not import the per-file layer action menu anymore', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).not.toContain("import LayerRegenerateMenu");
		expect(source).not.toContain('<LayerRegenerateMenu');
		expect(source).not.toContain('selectedLayerTypes');
		expect(source).toMatch(
			/import\s+type\s*\{\s*KnowledgeLayerItem,\s*KnowledgeLayerType\s*\}\s+from\s+'\.\/KnowledgeBase\/LayersPanel\.svelte';/s
		);
	});

	it('keeps knowledge-wide layer rebuild on the page toolbar', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('backfillKnowledgeLayers');
		expect(source).toContain('Knowledge layer backfill started.');
		expect(source).toContain('Rebuild Layers');
	});
});
