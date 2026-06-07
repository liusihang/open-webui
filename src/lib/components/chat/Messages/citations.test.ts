import { describe, expect, it } from 'vitest';

import { buildCitations, calculateShowRelevance, shouldShowPercentage } from './citations';

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
		expect(citations.map((citation) => citation.id)).toEqual([
			'ke:doc-1:1',
			'ke:doc-1:2'
		]);
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
