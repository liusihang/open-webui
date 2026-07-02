import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('KnowledgeBase imports', () => {
	it('keeps layered knowledge controls hidden from the page', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).not.toContain('backfillKnowledgeLayers');
		expect(source).not.toContain('getKnowledgeFileLayers');
		expect(source).not.toContain('regenerateKnowledgeFileLayerByType');
		expect(source).not.toContain('regenerateKnowledgeFileLayers');
		expect(source).not.toContain("import LayersPanel from './KnowledgeBase/LayersPanel.svelte';");
		expect(source).not.toContain('<LayersPanel');
	});

	it('renders selected image files as a preview instead of the editable textarea', () => {
		const filePath = resolve(
			process.cwd(),
			'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte'
		);
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('getFileContentById');
		expect(source).toContain('PanzoomContainer');
		expect(source).toContain('isSelectedFileImage');
		expect(source).toContain('selectedFileImageUrl');
		expect(source).toContain('IMAGE_EXTS');
		expect(source).toContain('content_type');
		expect(source).toContain('<img');
		expect(source).toContain('{#if isSelectedFileImage(selectedFile)}');
		expect(source).toContain('{:else}');
		expect(source).toContain('<textarea');
	});
});
