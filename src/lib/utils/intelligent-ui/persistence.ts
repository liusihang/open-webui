/** A narrow metadata snapshot, captured independently of reactive navigation state. */
export type PersistedIntelligentUIState = {
	version: 1;
	blocks: Record<string, Record<string, string | number | boolean>>;
};

export type MessageStateSnapshot = {
	chatId: string;
	messageId: string;
	token: string;
	state: PersistedIntelligentUIState;
};

export function createMessageStateSaver(
	save: (snapshot: MessageStateSnapshot) => Promise<unknown>,
	onError: (error: unknown) => void,
	delay = 350
) {
	let pending: MessageStateSnapshot | undefined;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let queue = Promise.resolve();
	function flush(): Promise<void> {
		if (timer) clearTimeout(timer);
		timer = undefined;
		const snapshot = pending;
		pending = undefined;
		if (snapshot)
			queue = queue
				.then(() => save(snapshot))
				.then(() => undefined)
				.catch(onError);
		return queue;
	}
	return {
		schedule(snapshot: MessageStateSnapshot) {
			// Capture before navigation/re-render can replace the host's reactive history and IDs.
			pending = structuredClone(snapshot);
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => void flush(), delay);
		},
		flush
	};
}
