import { describe, expect, it } from 'vitest';

import {
	buildCitationTargets,
	buildCitations,
	calculateShowRelevance,
	shouldShowPercentage
} from './citations';

describe('buildCitations', () => {
	it('groups repeated source chunks under a stable citation id', () => {
		const citations = buildCitations([
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-1', page: 0 }],
				distances: [0.9]
			},
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 2'],
				metadata: [{ source: 'doc-1', page: 1 }],
				distances: [0.8]
			}
		]);

		expect(citations).toHaveLength(1);
		expect(citations[0].document).toEqual(['chunk 1', 'chunk 2']);
	});

	it('keeps different evidence refs from the same source file separate', () => {
		const citations = buildCitations([
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-1', page: 0, evidence_ref: 'ke:doc-1:1' }],
				distances: [0.9]
			},
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 2'],
				metadata: [{ source: 'doc-1', page: 0, evidence_ref: 'ke:doc-1:2' }],
				distances: [0.8]
			}
		]);

		expect(citations).toHaveLength(2);
		expect(citations.map((citation) => citation.id)).toEqual(['ke:doc-1:1', 'ke:doc-1:2']);
		expect(citations.map((citation) => citation.document)).toEqual([['chunk 1'], ['chunk 2']]);
	});

	it('deduplicates identical replayed document payloads', () => {
		const citations = buildCitations([
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-1', page: 0 }],
				distances: [0.9]
			},
			{
				source: { id: 'doc-1', name: 'Doc 1' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-1', page: 0 }],
				distances: [0.9]
			}
		]);

		expect(citations).toHaveLength(1);
		expect(citations[0].document).toEqual(['chunk 1']);
	});

	it('uses metadata name for display while keeping stable grouping', () => {
		const citations = buildCitations([
			{
				source: { id: 'doc-1', name: 'Internal Name' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-1', name: 'Pretty Name' }]
			}
		]);

		expect(citations[0].id).toBe('doc-1');
		expect(citations[0].source?.name).toBe('Pretty Name');
	});

	it('falls back to legacy source grouping when evidence refs are absent', () => {
		const citations = buildCitations([
			{
				source: { id: 'doc-legacy', name: 'Legacy Doc' },
				document: ['chunk 1'],
				metadata: [{ source: 'doc-legacy', page: 0 }],
				distances: [0.9]
			},
			{
				source: { id: 'doc-legacy', name: 'Legacy Doc' },
				document: ['chunk 2'],
				metadata: [{ source: 'doc-legacy', page: 1 }],
				distances: [0.8]
			}
		]);

		expect(citations).toHaveLength(1);
		expect(citations[0].id).toBe('doc-legacy');
		expect(citations[0].document).toEqual(['chunk 1', 'chunk 2']);
	});
});

describe('citation relevance helpers', () => {
	it('only shows percentage when all distances are normalized', () => {
		expect(
			shouldShowPercentage([
				{ id: 'a', source: {}, document: [], metadata: [], distances: [0.2, 0.6] }
			])
		).toBe(true);

		expect(
			shouldShowPercentage([{ id: 'a', source: {}, document: [], metadata: [], distances: [1.2] }])
		).toBe(false);
	});

	it('suppresses mixed in-range/out-of-range singleton anomalies', () => {
		expect(
			calculateShowRelevance([
				{ id: 'a', source: {}, document: [], metadata: [], distances: [0.2, 1.3] }
			])
		).toBe(false);
	});
});

describe('buildCitationTargets', () => {
	it('resolves numeric citations through citation_map evidence refs before source order', () => {
		const targets = buildCitationTargets(
			[
				{
					source: { id: 'doc-1', name: 'Doc 1' },
					document: ['retrieved but uncited'],
					metadata: [{ source: 'doc-1', evidence_ref: 'ke:doc-1:text' }]
				},
				{
					source: { id: 'doc-1', name: 'Doc 1' },
					document: ['cited image'],
					metadata: [
						{
							source: 'doc-1',
							name: 'Figure 1',
							evidence_ref: 'ke:doc-1:image',
							preview: { type: 'image', caption: 'Gel image' },
							thumbnail_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Aimage/thumbnail',
							content_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Aimage/content'
						}
					]
				}
			],
			{
				content: 'The image supports the claim [1].',
				metadata: {
					citation_map: {
						'1': { evidence_ref: 'ke:doc-1:image' }
					}
				}
			}
		);

		expect(targets).toHaveLength(1);
		expect(targets[0]?.id).toBe('ke:doc-1:image');
		expect(targets[0]?.title).toBe('Figure 1');
		expect(targets[0]?.preview).toMatchObject({
			type: 'image',
			caption: 'Gel image',
			thumbnail_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Aimage/thumbnail',
			content_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Aimage/content'
		});
	});

	it('builds plain text hover previews from text evidence metadata', () => {
		const targets = buildCitationTargets(
			[
				{
					source: { id: 'doc-1', name: 'Doc 1' },
					document: ['Full text body'],
					metadata: [
						{
							source: 'doc-1',
							evidence_ref: 'ke:doc-1:text',
							preview: { type: 'text', text: '<b>plain excerpt</b>' },
							content_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Atext/content'
						}
					]
				}
			],
			{
				content: 'The text supports this [1].',
				metadata: { citation_map: { '1': 'ke:doc-1:text' } }
			}
		);

		expect(targets[0]?.preview).toMatchObject({
			type: 'text',
			text: '<b>plain excerpt</b>',
			content_url: '/api/v1/knowledge/kb-1/evidence/ke%3Adoc-1%3Atext/content'
		});
	});

	it('hides retrieved evidence that the final answer did not cite', () => {
		const targets = buildCitationTargets(
			[
				{
					source: { id: 'doc-1', name: 'Doc 1' },
					document: ['cited'],
					metadata: [{ source: 'doc-1', evidence_ref: 'ke:doc-1:cited' }]
				},
				{
					source: { id: 'doc-1', name: 'Doc 1' },
					document: ['uncited'],
					metadata: [{ source: 'doc-1', evidence_ref: 'ke:doc-1:uncited' }]
				}
			],
			{
				content: 'Only one source is cited [1].',
				metadata: { citation_map: { '1': 'ke:doc-1:cited' } }
			}
		);

		expect(targets.map((target) => target.id)).toEqual(['ke:doc-1:cited']);
	});

	it('keeps legacy positional citation behavior when citation_map is absent', () => {
		const targets = buildCitationTargets(
			[
				{
					source: { id: 'doc-legacy', name: 'Legacy Doc' },
					document: ['chunk 1', 'chunk 2'],
					metadata: [
						{ source: 'doc-legacy', page: 0 },
						{ source: 'doc-legacy', page: 1 }
					]
				}
			],
			{ content: 'Legacy citation [1].', metadata: {} }
		);

		expect(targets).toHaveLength(1);
		expect(targets[0]?.id).toBe('doc-legacy');
		expect(targets[0]?.title).toBe('Legacy Doc');
	});
});
