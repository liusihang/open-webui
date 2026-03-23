type ChatMessage = {
	id?: string;
	role?: string;
	content?: string | null;
	done?: boolean;
	output?: unknown[];
	[key: string]: unknown;
};

type ChatHistory = {
	currentId: string | null;
	messages: Record<string, ChatMessage>;
};

export const mergeServerMessage = (
	existingMessage: ChatMessage = {},
	incomingMessage: ChatMessage = {}
): ChatMessage => {
	const existingContent =
		typeof existingMessage?.content === 'string' ? existingMessage.content : '';
	const incomingContent = incomingMessage.content;
	const incomingContentIsEmpty =
		incomingContent === undefined ||
		incomingContent === null ||
		(typeof incomingContent === 'string' && incomingContent.trim().length === 0);

	const mergedMessage = {
		...existingMessage,
		...(!incomingContentIsEmpty &&
		typeof incomingContent === 'string' &&
		typeof existingMessage?.content === 'string' &&
		existingMessage.content !== incomingContent
			? { originalContent: existingMessage.content }
			: {}),
		...incomingMessage
	};

	if (incomingContentIsEmpty && existingContent) {
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
				JSON.stringify(mergedMessage.output ?? null)
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
