import { describe, expect, it } from 'vitest';
import {
	buildAttachedKnowledgeFeatureState,
	getEffectiveKnowledgeQueryEnabled,
	hasAttachedKnowledgeItems,
	hasModelKnowledgeItems,
	markKnowledgeAttachment
} from './attachedKnowledge';

describe('attached knowledge helpers', () => {
	it('auto-enables knowledge query when attached knowledge exists', () => {
		expect(
			getEffectiveKnowledgeQueryEnabled(
				false,
				[],
				[{ id: 'kb-1', type: 'collection', source: 'knowledge_attachment' }]
			)
		).toBe(true);
	});

	it('treats model knowledge as active knowledge scope', () => {
		expect(getEffectiveKnowledgeQueryEnabled(false, [{ id: 'kb-1', type: 'collection' }], [])).toBe(
			true
		);
	});

	it('detects attached knowledge items without treating plain uploads as knowledge scope', () => {
		expect(
			hasAttachedKnowledgeItems([
				{ id: 'plain-file', type: 'file' },
				markKnowledgeAttachment({ id: 'kb-1', type: 'collection' })
			])
		).toBe(true);
		expect(hasAttachedKnowledgeItems([{ id: 'plain-file', type: 'file' }])).toBe(false);
	});

	it('detects model knowledge items', () => {
		expect(hasModelKnowledgeItems([{ id: 'kb-1', type: 'collection' }])).toBe(true);
		expect(hasModelKnowledgeItems([])).toBe(false);
	});

	it('serializes the effective feature flag for submit payloads', () => {
		expect(buildAttachedKnowledgeFeatureState(true)).toEqual({ attached_knowledge_query: true });
		expect(buildAttachedKnowledgeFeatureState(false)).toEqual({ attached_knowledge_query: false });
	});
});
