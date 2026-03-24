export type StreamingTextState = {
	rendered: string;
	target: string;
	queue: string;
};

export const createStreamingTextState = (text = ''): StreamingTextState => ({
	rendered: text,
	target: text,
	queue: ''
});

export const syncStreamingTextState = (
	state: StreamingTextState,
	nextTarget: string
): StreamingTextState => {
	if (nextTarget === state.target) {
		return state;
	}

	if (nextTarget.startsWith(state.target)) {
		return {
			...state,
			target: nextTarget,
			queue: state.queue + nextTarget.slice(state.target.length)
		};
	}

	if (nextTarget.startsWith(state.rendered)) {
		return {
			rendered: state.rendered,
			target: nextTarget,
			queue: nextTarget.slice(state.rendered.length)
		};
	}

	return createStreamingTextState(nextTarget);
};

export const getStreamingTextChunkSize = (queuedLength: number): number => {
	if (queuedLength <= 0) {
		return 0;
	}

	return Math.min(32, Math.max(6, Math.ceil(queuedLength / 6)));
};

export const drainStreamingTextState = (
	state: StreamingTextState,
	chunkSize: number
): StreamingTextState => {
	if (!state.queue || chunkSize <= 0) {
		return state;
	}

	const drained = state.queue.slice(0, chunkSize);

	return {
		rendered: state.rendered + drained,
		target: state.target,
		queue: state.queue.slice(drained.length)
	};
};
