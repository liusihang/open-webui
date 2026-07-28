import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createAgentRunEventConnection } from './agentRunEventConnection';
import { agentRunEventFixture } from './fixtures';
import type { AgentRunEvent } from './types';

class FakeEventSource {
	onmessage: ((event: MessageEvent<string>) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onopen: ((event: Event) => void) | null = null;
	close = vi.fn();
	listeners = new Map<string, (event: Event) => void>();

	addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
		this.listeners.set(type, listener as (event: Event) => void);
	}

	emit(type: string, event: AgentRunEvent) {
		const message = { data: JSON.stringify(event) } as MessageEvent<string>;
		this.listeners.get(type)?.(message);
	}

	fail() {
		this.onerror?.(new Event('error'));
	}

	open() {
		this.onopen?.(new Event('open'));
	}
}

describe('createAgentRunEventConnection', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('retries a failed initial backfill before creating EventSource', async () => {
		const event = agentRunEventFixture({ seq: 1, event_type: 'run.running' });
		const getEvents = vi
			.fn()
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce([event]);
		const sources: FakeEventSource[] = [];
		let lastSeq = 0;

		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => lastSeq,
			getEvents,
			createSource: () => {
				const source = new FakeEventSource();
				sources.push(source);
				return source;
			},
			onEvent: (received) => {
				lastSeq = received.seq;
			},
			isTerminal: () => false,
			retryDelaysMs: [100, 200],
			jitterRatio: 0
		});

		await vi.advanceTimersByTimeAsync(0);
		expect(getEvents).toHaveBeenCalledTimes(1);
		expect(sources).toHaveLength(0);

		await vi.advanceTimersByTimeAsync(100);
		expect(getEvents).toHaveBeenCalledTimes(2);
		expect(sources).toHaveLength(1);
		expect(lastSeq).toBe(1);
		expect([...sources[0].listeners.keys()]).toEqual(
			expect.arrayContaining([
				'user_input.requested',
				'user_input.completed',
				'user_input.declined',
				'user_input.cancelled',
				'user_input.expired'
			])
		);

		cleanup();
	});

	it('backfills and reconnects after SSE error without replaying duplicate events', async () => {
		const first = agentRunEventFixture({ seq: 1, event_type: 'run.running' });
		const second = agentRunEventFixture({ seq: 2, event_type: 'action.summary' });
		const getEvents = vi
			.fn()
			.mockResolvedValueOnce([first])
			.mockResolvedValueOnce([first, second])
			.mockResolvedValueOnce([first, second]);
		const sources: FakeEventSource[] = [];
		const received: number[] = [];
		let lastSeq = 0;

		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => lastSeq,
			getEvents,
			createSource: () => {
				const source = new FakeEventSource();
				sources.push(source);
				return source;
			},
			onEvent: (event) => {
				received.push(event.seq);
				lastSeq = Math.max(lastSeq, event.seq);
			},
			isTerminal: () => false,
			retryDelaysMs: [50, 100],
			jitterRatio: 0
		});

		await vi.advanceTimersByTimeAsync(0);
		expect(sources).toHaveLength(1);

		sources[0].fail();
		expect(sources[0].close).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(50);

		expect(getEvents).toHaveBeenCalledTimes(2);
		expect(sources).toHaveLength(2);
		expect(received).toEqual([1, 2]);

		sources[1].fail();
		await vi.advanceTimersByTimeAsync(50);
		expect(getEvents).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(50);
		expect(getEvents).toHaveBeenCalledTimes(3);
		expect(sources).toHaveLength(3);

		cleanup();
		sources[2].fail();
		await vi.runAllTimersAsync();
		expect(getEvents).toHaveBeenCalledTimes(3);
		expect(sources[2].close).toHaveBeenCalledTimes(1);
	});

	it('stops reconnecting when the backfill after an SSE error returns a permanent HTTP status', async () => {
		const permanent = Object.assign(new Error('forbidden'), { status: 403 });
		const getEvents = vi.fn().mockResolvedValueOnce([]).mockRejectedValueOnce(permanent);
		const sources: FakeEventSource[] = [];
		const connectionStates: string[] = [];

		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => 0,
			getEvents,
			createSource: () => {
				const source = new FakeEventSource();
				sources.push(source);
				return source;
			},
			onEvent: () => undefined,
			isTerminal: () => false,
			onConnectionState: (state) => connectionStates.push(state),
			retryDelaysMs: [100],
			jitterRatio: 0
		});

		await vi.advanceTimersByTimeAsync(0);
		expect(sources).toHaveLength(1);
		sources[0].fail();
		await vi.advanceTimersByTimeAsync(100);
		expect(getEvents).toHaveBeenCalledTimes(2);

		await vi.runAllTimersAsync();
		expect(getEvents).toHaveBeenCalledTimes(2);
		expect(sources).toHaveLength(1);
		expect(connectionStates).toEqual(['disconnected', 'reconnecting', 'disconnected']);
		cleanup();
	});

	it('stops after the configured maximum number of consecutive failures', async () => {
		const getEvents = vi.fn().mockRejectedValue(new Error('offline'));
		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => 0,
			getEvents,
			createSource: () => new FakeEventSource(),
			onEvent: () => undefined,
			isTerminal: () => false,
			retryDelaysMs: [100],
			jitterRatio: 0,
			maxConsecutiveFailures: 3
		});

		await vi.advanceTimersByTimeAsync(0);
		await vi.advanceTimersByTimeAsync(100);
		await vi.advanceTimersByTimeAsync(100);
		await vi.advanceTimersByTimeAsync(1000);

		expect(getEvents).toHaveBeenCalledTimes(3);
		cleanup();
	});

	it('adds bounded jitter so simultaneous connections do not retry in lockstep', async () => {
		const firstGetEvents = vi.fn().mockRejectedValue(new Error('offline'));
		const secondGetEvents = vi.fn().mockRejectedValue(new Error('offline'));
		const common = {
			runId: 'run-1',
			getAfterSeq: () => 0,
			createSource: () => new FakeEventSource(),
			onEvent: () => undefined,
			isTerminal: () => false,
			retryDelaysMs: [100],
			jitterRatio: 0.2,
			maxConsecutiveFailures: 2
		};
		const cleanupFirst = createAgentRunEventConnection({
			...common,
			getEvents: firstGetEvents,
			random: () => 0
		});
		const cleanupSecond = createAgentRunEventConnection({
			...common,
			getEvents: secondGetEvents,
			random: () => 1
		});

		await vi.advanceTimersByTimeAsync(0);
		await vi.advanceTimersByTimeAsync(79);
		expect(firstGetEvents).toHaveBeenCalledTimes(1);
		expect(secondGetEvents).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(1);
		expect(firstGetEvents).toHaveBeenCalledTimes(2);
		expect(secondGetEvents).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(40);
		expect(secondGetEvents).toHaveBeenCalledTimes(2);
		cleanupFirst();
		cleanupSecond();
	});

	it('does not schedule a retry when cleanup happens during an in-flight backfill', async () => {
		let rejectBackfill: (reason?: unknown) => void = () => undefined;
		const getEvents = vi.fn(
			() =>
				new Promise<AgentRunEvent[]>((_resolve, reject) => {
					rejectBackfill = reject;
				})
		);
		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => 0,
			getEvents,
			createSource: () => new FakeEventSource(),
			onEvent: () => undefined,
			isTerminal: () => false,
			retryDelaysMs: [100],
			jitterRatio: 0
		});

		expect(getEvents).toHaveBeenCalledTimes(1);
		cleanup();
		rejectBackfill(new Error('offline'));
		await vi.runAllTimersAsync();

		expect(getEvents).toHaveBeenCalledTimes(1);
	});

	it('resets consecutive failures after an SSE connection opens successfully', async () => {
		const sources: FakeEventSource[] = [];
		const cleanup = createAgentRunEventConnection({
			runId: 'run-1',
			getAfterSeq: () => 0,
			getEvents: vi.fn().mockResolvedValue([]),
			createSource: () => {
				const source = new FakeEventSource();
				sources.push(source);
				return source;
			},
			onEvent: () => undefined,
			isTerminal: () => false,
			retryDelaysMs: [100],
			jitterRatio: 0,
			maxConsecutiveFailures: 2
		});

		await vi.advanceTimersByTimeAsync(0);
		sources[0].fail();
		await vi.advanceTimersByTimeAsync(100);
		expect(sources).toHaveLength(2);

		sources[1].open();
		sources[1].fail();
		await vi.advanceTimersByTimeAsync(100);
		expect(sources).toHaveLength(3);
		cleanup();
	});
});
