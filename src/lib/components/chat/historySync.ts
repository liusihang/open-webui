type ChatStatusEntry = {
	hidden?: boolean;
	done?: boolean;
	[key: string]: unknown;
};

type ChatSourceMetadata = {
	source?: string;
	name?: string;
	evidence_ref?: string;
	[key: string]: unknown;
};

type ChatSourceEntry = {
	id?: string;
	source?: {
		id?: string;
		url?: string;
		name?: string;
	};
	metadata?: Array<ChatSourceMetadata | undefined>;
	[key: string]: unknown;
};

export type ChatMessage = {
	id?: string;
	role?: string;
	content?: string | null;
	done?: boolean;
	output?: unknown[];
	status?: ChatStatusEntry;
	statusHistory?: ChatStatusEntry[];
	sources?: ChatSourceEntry[];
	citations?: ChatSourceEntry[];
	metadata?: Record<string, unknown>;
	[key: string]: unknown;
};

type ChatHistory = {
	currentId: string | null;
	messages: Record<string, ChatMessage>;
};

const SOCKET_INCREMENTAL_CONTENT_EVENTS = new Set([
	'chat:completion',
	'chat:message:delta',
	'message'
]);

export const shouldApplySocketContentEvent = (
	message: ChatMessage | null | undefined,
	eventType: string | null | undefined
): boolean => {
	if (!message?.agent_run_id || !eventType) {
		return true;
	}

	return !SOCKET_INCREMENTAL_CONTENT_EVENTS.has(eventType);
};

const getSourceMergeKey = (item: ChatSourceEntry, index: number) => {
	const metadata = Array.isArray(item?.metadata) ? item.metadata[0] : undefined;
	return (
		metadata?.evidence_ref ??
		metadata?.source ??
		item?.source?.id ??
		item?.source?.url ??
		item?.source?.name ??
		item?.id ??
		`source-${index}`
	);
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
};

const mergeMessageMetadata = (
	existingMetadata: Record<string, unknown> | undefined,
	incomingMetadata: Record<string, unknown> | undefined
) => {
	if (!existingMetadata && !incomingMetadata) {
		return undefined;
	}

	const mergedMetadata = {
		...(existingMetadata ?? {}),
		...(incomingMetadata ?? {})
	};

	if (
		isPlainObject(existingMetadata?.citation_map) &&
		isPlainObject(incomingMetadata?.citation_map)
	) {
		mergedMetadata.citation_map = {
			...existingMetadata.citation_map,
			...incomingMetadata.citation_map
		};
	}

	return mergedMetadata;
};

export const mergeStableSources = <T extends ChatSourceEntry>(
	existingSources?: T[],
	incomingSources?: T[]
): T[] | undefined => {
	if (!Array.isArray(existingSources) && !Array.isArray(incomingSources)) {
		return incomingSources;
	}

	if (!Array.isArray(incomingSources) || incomingSources.length === 0) {
		return Array.isArray(existingSources) ? existingSources : incomingSources;
	}

	if (!Array.isArray(existingSources) || existingSources.length === 0) {
		return incomingSources;
	}

	const mergedSources: T[] = [];
	const seenKeys = new Set<string>();

	for (const [index, item] of [...existingSources, ...incomingSources].entries()) {
		const key = String(getSourceMergeKey(item, index));
		if (seenKeys.has(key)) {
			continue;
		}

		seenKeys.add(key);
		mergedSources.push(item);
	}

	return mergedSources;
};

export const mergeServerMessage = (
	existingMessage: ChatMessage = {},
	incomingMessage: ChatMessage = {}
): ChatMessage => {
	const existingContent =
		typeof existingMessage?.content === 'string' ? existingMessage.content : '';
	const incomingContent = incomingMessage.content;
	const incomingContentString =
		typeof incomingContent === 'string' ? incomingContent : incomingContent == null ? '' : null;
	const incomingContentTrimmed = incomingContentString?.trim() ?? '';

	const incomingContentIsEmpty =
		incomingContent === undefined ||
		incomingContent === null ||
		(typeof incomingContentString === 'string' && incomingContentTrimmed.length === 0);

	const incomingContentIsPlaceholder =
		typeof incomingContentString === 'string' &&
		(incomingContentTrimmed === '[RESPONSE]' || incomingContentTrimmed.startsWith('[RESPONSE]'));

	const incomingContentIsShorterStreamingSnapshot =
		typeof incomingContentString === 'string' &&
		Boolean(existingContent) &&
		incomingMessage.role === 'assistant' &&
		incomingMessage.done !== true &&
		incomingContentString.length < existingContent.length;

	const mergedMessage = {
		...existingMessage,
		...(!incomingContentIsEmpty &&
		typeof incomingContentString === 'string' &&
		typeof existingMessage?.content === 'string' &&
		existingMessage.content !== incomingContentString
			? { originalContent: existingMessage.content }
			: {}),
		...incomingMessage
	};

	mergedMessage.metadata = mergeMessageMetadata(
		isPlainObject(existingMessage.metadata) ? existingMessage.metadata : undefined,
		isPlainObject(incomingMessage.metadata) ? incomingMessage.metadata : undefined
	);

	if (
		(existingContent && incomingContentIsEmpty) ||
		(existingContent && incomingContentIsPlaceholder) ||
		incomingContentIsShorterStreamingSnapshot
	) {
		mergedMessage.content = existingContent;
	}

	if (
		Array.isArray(incomingMessage.output) &&
		incomingMessage.output.length === 0 &&
		Array.isArray(existingMessage.output) &&
		existingMessage.output.length > 0
	) {
		mergedMessage.output = existingMessage.output;
	}

	if (
		Array.isArray(incomingMessage.statusHistory) &&
		Array.isArray(existingMessage.statusHistory) &&
		incomingMessage.statusHistory.length < existingMessage.statusHistory.length
	) {
		mergedMessage.statusHistory = existingMessage.statusHistory;
	}

	mergedMessage.sources = mergeStableSources(existingMessage.sources, incomingMessage.sources);
	mergedMessage.citations = mergeStableSources(
		existingMessage.citations,
		incomingMessage.citations
	);

	return mergedMessage;
};

export const mergeHistorySnapshot = (
	currentHistory: ChatHistory,
	latestHistory: Partial<ChatHistory> | null | undefined
) => {
	const mergedHistory: ChatHistory = structuredClone(currentHistory);
	let hasAssistantProgress = false;
	let hasRenderableAssistantUpdate = false;
	let changed = false;

	if (!latestHistory?.messages) {
		return {
			history: mergedHistory,
			hasAssistantProgress,
			hasRenderableAssistantUpdate,
			changed
		};
	}

	for (const incomingMessage of Object.values(latestHistory.messages)) {
		if (!incomingMessage?.id) {
			continue;
		}

		const existingMessage = mergedHistory.messages?.[incomingMessage.id] ?? {};
		const mergedMessage = mergeServerMessage(existingMessage, incomingMessage);
		const previousContent =
			typeof existingMessage.content === 'string' ? existingMessage.content : '';
		const nextContent = typeof mergedMessage.content === 'string' ? mergedMessage.content : '';

		if (
			previousContent !== nextContent ||
			existingMessage.done !== mergedMessage.done ||
			JSON.stringify(existingMessage.output ?? null) !==
				JSON.stringify(mergedMessage.output ?? null) ||
			JSON.stringify(existingMessage.statusHistory ?? null) !==
				JSON.stringify(mergedMessage.statusHistory ?? null) ||
			JSON.stringify(existingMessage.sources ?? null) !==
				JSON.stringify(mergedMessage.sources ?? null) ||
			JSON.stringify(existingMessage.citations ?? null) !==
				JSON.stringify(mergedMessage.citations ?? null)
		) {
			changed = true;
		}

		if (
			mergedMessage.role === 'assistant' &&
			nextContent.trim() !== '' &&
			!nextContent.startsWith('[RESPONSE]')
		) {
			if (
				nextContent.length > previousContent.length ||
				existingMessage.done !== mergedMessage.done
			) {
				hasAssistantProgress = true;
			}

			if (mergedMessage.done === true) {
				hasRenderableAssistantUpdate = true;
			}
		}

		mergedHistory.messages[incomingMessage.id] = mergedMessage;
	}

	if (latestHistory.currentId && mergedHistory.currentId !== latestHistory.currentId) {
		mergedHistory.currentId = latestHistory.currentId;
		changed = true;
	}

	return {
		history: mergedHistory,
		hasAssistantProgress,
		hasRenderableAssistantUpdate,
		changed
	};
};
