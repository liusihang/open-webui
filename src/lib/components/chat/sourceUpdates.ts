type ChatMessage = {
	sources?: unknown[];
	[key: string]: unknown;
};

type ChatHistory = {
	messages: Record<string, ChatMessage>;
	[key: string]: unknown;
};

type SourceQueue = Map<string, unknown[]>;

export const enqueueSourceUpdate = (
	queue: SourceQueue,
	messageId: string,
	source: unknown
) => {
	const existing = queue.get(messageId) ?? [];
	queue.set(messageId, [...existing, source]);
	return queue;
};

export const enqueueSourceBatch = (
	queue: SourceQueue,
	messageId: string,
	sources: unknown[]
) => {
	if (!Array.isArray(sources) || sources.length === 0) {
		return queue;
	}

	const existing = queue.get(messageId) ?? [];
	queue.set(messageId, [...existing, ...sources]);
	return queue;
};

export const flushQueuedSourceUpdates = (
	messages: Record<string, ChatMessage>,
	queue: SourceQueue
) => {
	if (queue.size === 0) {
		return messages;
	}

	const nextMessages = { ...messages };

	for (const [messageId, queuedSources] of queue.entries()) {
		const currentMessage = nextMessages[messageId];
		if (!currentMessage) {
			continue;
		}

		nextMessages[messageId] = {
			...currentMessage,
			sources: [...(currentMessage.sources ?? []), ...queuedSources]
		};
	}

	queue.clear();
	return nextMessages;
};

export const flushQueuedSourceHistory = (history: ChatHistory, queue: SourceQueue) => {
	if (!history?.messages || queue.size === 0) {
		return history;
	}

	return {
		...history,
		messages: flushQueuedSourceUpdates(history.messages, queue)
	};
};
