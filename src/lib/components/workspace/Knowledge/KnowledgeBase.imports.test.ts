import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('KnowledgeBase layered imports', () => {
	it('wires layered knowledge api and panel into the page', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('backfillKnowledgeLayers');
		expect(source).toContain('getKnowledgeFileLayers');
		expect(source).toContain('regenerateKnowledgeFileLayerByType');
		expect(source).toContain('regenerateKnowledgeFileLayers');
		expect(source).toContain("import LayersPanel from './KnowledgeBase/LayersPanel.svelte';");
		expect(source).toContain('Knowledge layer backfill started.');
		expect(source).toContain('Layer regeneration started.');
		expect(source).toContain('<LayersPanel');
	});
});
