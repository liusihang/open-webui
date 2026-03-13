type KnowledgeSearchStatus = {
	action?: string;
	description?: unknown;
	query?: unknown;
} | null;

type KnowledgeSearchStatusText = {
	key: string;
	values: Record<string, string>;
};

const GENERIC_KNOWLEDGE_SEARCH_DESCRIPTION = 'Searching knowledge base';

const normalizeText = (value: unknown) => (typeof value === 'string' ? value.trim() : '');

export const getKnowledgeSearchStatusText = (
	status: KnowledgeSearchStatus
): KnowledgeSearchStatusText => {
	const description = normalizeText(status?.description);
	const query = normalizeText(status?.query);

	if (description && description !== GENERIC_KNOWLEDGE_SEARCH_DESCRIPTION) {
		if (description.includes('{{searchQuery}}') && query) {
			return {
				key: description,
				values: {
					searchQuery: query
				}
			};
		}

		return {
			key: description,
			values: {}
		};
	}

	if (query) {
		return {
			key: 'Searching Knowledge for "{{searchQuery}}"',
			values: {
				searchQuery: query
			}
		};
	}

	if (description) {
		return {
			key: description,
			values: {}
		};
	}

	return {
		key: 'Searching',
		values: {}
	};
};
