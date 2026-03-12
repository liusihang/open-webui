import { describe, expect, it } from 'vitest';

import { getKnowledgeSearchStatusText } from './knowledgeSearchStatus';

describe('knowledge search status text', () => {
	it('uses the query for the generic knowledge search status', () => {
		expect(
			getKnowledgeSearchStatusText({
				action: 'knowledge_search',
				description: 'Searching knowledge base',
				query: 'bm25 rebuild flow'
			})
		).toEqual({
			key: 'Searching Knowledge for "{{searchQuery}}"',
			values: {
				searchQuery: 'bm25 rebuild flow'
			}
		});
	});

	it('falls back to the backend description when query is blank', () => {
		expect(
			getKnowledgeSearchStatusText({
				action: 'knowledge_search',
				description: 'Preparing BM25 index (first query may be slower)',
				query: '   '
			})
		).toEqual({
			key: 'Preparing BM25 index (first query may be slower)',
			values: {}
		});
	});

	it('returns a generic searching fallback when no status text is available', () => {
		expect(
			getKnowledgeSearchStatusText({
				action: 'knowledge_search'
			})
		).toEqual({
			key: 'Searching',
			values: {}
		});
	});
});
