import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('KnowledgeBase imports', () => {
	it('imports LAYER_TYPE_ORDER when seeding selected layer types', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('LAYER_TYPE_ORDER');
		expect(source).toMatch(
			/import\s+type\s*\{\s*KnowledgeLayerItem,\s*KnowledgeLayerType\s*\}\s+from\s+'\.\/KnowledgeBase\/LayersPanel\.svelte';/s
		);
		expect(source).toMatch(
			/import\s*\{\s*LAYER_TYPE_ORDER\s*\}\s+from\s+'\.\/KnowledgeBase\/LayersPanel\.svelte';/s
		);
	});
});
