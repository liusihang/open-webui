import { describe, expect, it } from 'vitest';

import {
	buildCitations,
	calculateShowRelevance,
	shouldShowPercentage,
	summarizeCitations
} from './citations';


describe('summarizeCitations', () => {
	it('summarizes citation entries without duplicating documents', () => {
		const sources = [
			{
				source: { id: 'src-1', name: 'https://example.com' },
				document: ['doc-1', 'doc-2'],
				metadata: [
					{ source: 'https://example.com', name: 'https://example.com' },
					{ source: 'https://example.com', name: 'https://example.com' }
				],
				distances: [0.2, 0.4]
			},
			{
				source: { id: 'src-2', name: 'Paper' },
				document: ['doc-3'],
				metadata: [{ source: 'paper-1', name: 'Paper' }],
				distances: [0.1]
			}
		];

		const summary = summarizeCitations(sources);

		expect(summary.count).toBe(2);
		expect(summary.urlSources).toHaveLength(1);
		expect(summary.items.map((item) => item.id)).toEqual(['https://example.com', 'paper-1']);
		expect(summary.distances).toEqual([0.2, 0.4, 0.1]);
	});

	it('keeps titled URL citations in the URL summary bucket', () => {
		const sources = [
			{
				source: { id: 'search_web', name: 'search_web' },
				document: ['Example Paper\ndoc-1'],
				metadata: [
					{
						source: 'https://example.com/paper',
						name: 'Example Paper',
						url: 'https://example.com/paper'
					}
				]
			}
		];

		const summary = summarizeCitations(sources);

		expect(summary.count).toBe(1);
		expect(summary.urlSources).toHaveLength(1);
		expect(summary.items[0].source.name).toBe('Example Paper');
		expect(summary.items[0].source.url).toBe('https://example.com/paper');
	});
});


describe('buildCitations', () => {
	it('builds grouped citation buckets only when needed', () => {
		const sources = [
			{
				source: { id: 'src-1', name: 'Paper' },
				document: ['doc-1', 'doc-2'],
				metadata: [
					{ source: 'paper-1', name: 'Paper' },
					{ source: 'paper-1', name: 'Paper' }
				],
				distances: [0.1, 0.2]
			}
		];

		const citations = buildCitations(sources);

		expect(citations).toHaveLength(1);
		expect(citations[0].document).toEqual(['doc-1', 'doc-2']);
		expect(citations[0].distances).toEqual([0.1, 0.2]);
	});

	it('preserves page title when citation id is a URL', () => {
		const sources = [
			{
				source: { id: 'search_web', name: 'search_web' },
				document: ['Example Paper\ndoc-1'],
				metadata: [
					{
						source: 'https://example.com/paper',
						name: 'Example Paper',
						url: 'https://example.com/paper'
					}
				]
			}
		];

		const citations = buildCitations(sources);

		expect(citations).toHaveLength(1);
		expect(citations[0].source.name).toBe('Example Paper');
		expect(citations[0].source.url).toBe('https://example.com/paper');
	});
});


describe('citation relevance helpers', () => {
	it('computes relevance and percentage flags from distances', () => {
		expect(calculateShowRelevance([0.1, 0.2, 0.3])).toBe(true);
		expect(shouldShowPercentage([0.1, 0.2, 0.3])).toBe(true);
		expect(calculateShowRelevance([0.1, 4])).toBe(false);
		expect(shouldShowPercentage([0.1, 4])).toBe(false);
	});
});
