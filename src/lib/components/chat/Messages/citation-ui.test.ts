import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));

const readComponent = (relativePath: string) => {
	return readFileSync(resolve(here, relativePath), 'utf8');
};

describe('citation UI stopgap regressions', () => {
	it('keeps inline source badges numeric instead of echoing the source title inline', () => {
		const source = readComponent('./Markdown/Source.svelte');

		expect(source).not.toContain('getDisplayTitle(formattedTitle(decodeString(title)))');
	});

	it('delays source hover previews instead of opening immediately', () => {
		const sourceToken = readComponent('./Markdown/SourceToken.svelte');

		expect(sourceToken).not.toContain('openDelay={0}');
		expect(sourceToken).toContain('openDelay={300}');
		expect(sourceToken).toContain('closeDelay={100}');
	});

	it('starts citation modal debug content collapsed by default', () => {
		const modal = readComponent('./Citations/CitationModal.svelte');

		expect(modal).not.toContain('export let showRelevance = true;');
		expect(modal).toContain('<details');
	});
});
