type KnowledgeItem = Record<string, unknown> | null | undefined;
type ModelLike =
	| {
			info?: {
				meta?: {
					knowledge?: KnowledgeItem[] | KnowledgeItem | false | null;
				};
			};
	  }
	| null
	| undefined;

const KNOWLEDGE_ATTACHMENT_SOURCE = 'knowledge_attachment';
const KNOWLEDGE_ATTACHMENT_TYPES = new Set(['collection', 'file', 'note']);

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

export const normalizeKnowledgeItems = (
	items: KnowledgeItem[] | KnowledgeItem | false | null | undefined
) => {
	if (!items) {
		return [];
	}

	const values = Array.isArray(items) ? items : [items];
	return values.filter(isRecord);
};

export const markKnowledgeAttachment = (item: Record<string, unknown>) => ({
	...item,
	source: KNOWLEDGE_ATTACHMENT_SOURCE
});

export const isKnowledgeAttachmentSource = (item: KnowledgeItem) =>
	isRecord(item) && item.source === KNOWLEDGE_ATTACHMENT_SOURCE;

export const isKnowledgeAttachmentItem = (item: KnowledgeItem) => {
	if (!isRecord(item)) {
		return false;
	}

	const type = String(item.type ?? '');
	if (!KNOWLEDGE_ATTACHMENT_TYPES.has(type)) {
		return false;
	}

	if (type !== 'file') {
		return true;
	}

	return (
		isKnowledgeAttachmentSource(item) ||
		'collection' in item ||
		'collection_name' in item ||
		'collection_names' in item ||
		'legacy' in item
	);
};

export const getAttachedKnowledgeScope = (items: KnowledgeItem[] = []) =>
	normalizeKnowledgeItems(items).filter(isKnowledgeAttachmentSource);

export const hasAttachedKnowledgeItems = (items: KnowledgeItem[] = []) =>
	getAttachedKnowledgeScope(items).length > 0;

export const hasModelKnowledgeItems = (items: KnowledgeItem[] = []) =>
	normalizeKnowledgeItems(items).length > 0;

export const getModelKnowledgeScopeFromModels = (models: ModelLike[] = []) => {
	const seen = new Set<string>();
	const scope: Record<string, unknown>[] = [];

	for (const model of models) {
		for (const item of normalizeKnowledgeItems(model?.info?.meta?.knowledge)) {
			const key = JSON.stringify(item);
			if (seen.has(key)) {
				continue;
			}

			seen.add(key);
			scope.push(item);
		}
	}

	return scope;
};

export const getEffectiveKnowledgeQueryEnabled = (
	manualEnabled: boolean,
	modelScope: KnowledgeItem[] = [],
	attachedScope: KnowledgeItem[] = []
) => manualEnabled || hasModelKnowledgeItems(modelScope) || hasAttachedKnowledgeItems(attachedScope);

export const buildAttachedKnowledgeFeatureState = (
	manualEnabled: boolean,
	modelScope: KnowledgeItem[] = [],
	attachedScope: KnowledgeItem[] = []
) => ({
	attached_knowledge_query: getEffectiveKnowledgeQueryEnabled(
		manualEnabled,
		modelScope,
		attachedScope
	)
});

export const filterPersistentChatFiles = (items: KnowledgeItem[] = []) =>
	normalizeKnowledgeItems(items).filter((item) => !isKnowledgeAttachmentSource(item));
