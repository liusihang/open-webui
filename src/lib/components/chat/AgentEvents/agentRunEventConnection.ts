import {
	AGENT_RUN_EVENT_TYPES,
	type AgentConnectionState,
	type AgentRunEvent,
	type AgentRunEventType
} from './types';

export type AgentRunEventSource = {
	onmessage: ((event: MessageEvent<string>) => void) | null;
	onerror: ((event: Event) => void) | null;
	onopen: ((event: Event) => void) | null;
	addEventListener: (
		type: string,
		listener: EventListenerOrEventListenerObject,
		options?: boolean | AddEventListenerOptions
	) => void;
	close: () => void;
};

type AgentRunEventConnectionOptions = {
	runId: string;
	getAfterSeq: () => number;
	getEvents: (runId: string, afterSeq: number) => Promise<AgentRunEvent[]>;
	createSource: (runId: string, afterSeq: number) => AgentRunEventSource;
	onEvent: (event: AgentRunEvent) => void;
	isTerminal: () => boolean;
	onConnectionState?: (state: AgentConnectionState) => void;
	eventTypes?: readonly AgentRunEventType[];
	retryDelaysMs?: readonly number[];
	maxConsecutiveFailures?: number;
	jitterRatio?: number;
	random?: () => number;
};

const DEFAULT_RETRY_DELAYS_MS = [250, 500, 1000, 2000, 5000] as const;
const DEFAULT_MAX_CONSECUTIVE_FAILURES = 6;
const DEFAULT_JITTER_RATIO = 0.2;

export const createAgentRunEventConnection = ({
	runId,
	getAfterSeq,
	getEvents,
	createSource,
	onEvent,
	isTerminal,
	onConnectionState,
	eventTypes = AGENT_RUN_EVENT_TYPES,
	retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
	maxConsecutiveFailures = DEFAULT_MAX_CONSECUTIVE_FAILURES,
	jitterRatio = DEFAULT_JITTER_RATIO,
	random = Math.random
}: AgentRunEventConnectionOptions): (() => void) => {
	let cancelled = false;
	let connecting = false;
	let source: AgentRunEventSource | null = null;
	let retryTimer: ReturnType<typeof setTimeout> | null = null;
	let consecutiveFailures = 0;
	let retryAttempt = 0;
	let connectionState: AgentConnectionState = 'connected';
	const seenSeqs = new Set<number>();
	const failureLimit = Math.max(1, Math.floor(maxConsecutiveFailures));

	const closeSource = () => {
		const currentSource = source;
		source = null;
		currentSource?.close();
	};

	const setConnectionState = (next: AgentConnectionState) => {
		if (connectionState === next) return;
		connectionState = next;
		onConnectionState?.(next);
	};

	const ingest = (event: AgentRunEvent) => {
		if (!Number.isFinite(event.seq) || event.seq <= 0 || seenSeqs.has(event.seq)) return;
		seenSeqs.add(event.seq);
		consecutiveFailures = 0;
		onEvent(event);
		if (isTerminal()) {
			closeSource();
			setConnectionState('connected');
		}
	};

	const handleMessage = (event: Event) => {
		try {
			retryAttempt = 0;
			ingest(JSON.parse((event as MessageEvent<string>).data) as AgentRunEvent);
		} catch {
			// A malformed event must not tear down an otherwise healthy SSE stream.
		}
	};

	const retryDelay = (failureIndex: number): number => {
		const baseDelay =
			retryDelaysMs.length === 0
				? (DEFAULT_RETRY_DELAYS_MS.at(-1) ?? 5000)
				: (retryDelaysMs[Math.min(failureIndex, retryDelaysMs.length - 1)] ?? 0);
		const boundedRatio = Math.min(1, Math.max(0, jitterRatio));
		const boundedRandom = Math.min(1, Math.max(0, random()));
		const multiplier = 1 + boundedRatio * (boundedRandom * 2 - 1);
		return Math.max(0, Math.round(baseDelay * multiplier));
	};

	const scheduleRetry = (error?: unknown) => {
		if (cancelled || isTerminal() || retryTimer !== null) return;
		if (isPermanentConnectionError(error)) {
			setConnectionState('disconnected');
			return;
		}
		consecutiveFailures += 1;
		if (consecutiveFailures >= failureLimit) {
			setConnectionState('disconnected');
			return;
		}
		setConnectionState('reconnecting');
		const delay = retryDelay(retryAttempt);
		retryAttempt += 1;
		retryTimer = setTimeout(() => {
			retryTimer = null;
			void connect();
		}, delay);
	};

	const connect = async () => {
		if (cancelled || connecting || isTerminal()) return;
		connecting = true;
		closeSource();

		try {
			const events = await getEvents(runId, getAfterSeq());
			if (cancelled) return;
			for (const event of events) ingest(event);
			if (isTerminal()) {
				setConnectionState('connected');
				return;
			}

			const nextSource = createSource(runId, getAfterSeq());
			source = nextSource;
			nextSource.onopen = () => {
				if (cancelled || source !== nextSource) return;
				consecutiveFailures = 0;
				retryAttempt = 0;
				setConnectionState('connected');
			};
			nextSource.onmessage = handleMessage;
			for (const eventType of eventTypes) {
				nextSource.addEventListener(eventType, handleMessage);
			}
			nextSource.onerror = () => {
				if (cancelled || source !== nextSource) return;
				closeSource();
				setConnectionState('disconnected');
				scheduleRetry();
			};
		} catch (error) {
			if (!cancelled) scheduleRetry(error);
		} finally {
			connecting = false;
		}
	};

	void connect();

	return () => {
		cancelled = true;
		if (retryTimer !== null) {
			clearTimeout(retryTimer);
			retryTimer = null;
		}
		closeSource();
	};
};

const isPermanentConnectionError = (error: unknown): boolean => {
	if (typeof error !== 'object' || error === null || !('status' in error)) return false;
	const status = Number((error as { status: unknown }).status);
	if (!Number.isInteger(status) || status < 400 || status >= 500) return false;
	return status !== 408 && status !== 425 && status !== 429;
};
