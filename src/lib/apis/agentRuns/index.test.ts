import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
	buildAgentRunEventsListUrl,
	buildAgentRunEventsUrl,
	createAgentRunEventsSource,
	getAgentRunEvents,
	getAgentRuns
} from './index';

class FakeEventSource {
	static instances: FakeEventSource[] = [];

	url: string | URL;
	options?: EventSourceInit;
	close = vi.fn();

	constructor(url: string | URL, options?: EventSourceInit) {
		this.url = url;
		this.options = options;
		FakeEventSource.instances.push(this);
	}
}

describe('agentRuns api helpers', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
		FakeEventSource.instances = [];
		vi.stubGlobal('EventSource', FakeEventSource);
	});

	it('lists agent runs for a chat with bearer auth', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ items: [{ id: 'run-1' }], total: 1 })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await getAgentRuns('token-1', { chatId: 'chat-1', limit: 20 });

		expect(result).toEqual({ items: [{ id: 'run-1' }], total: 1 });
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0]?.[0]).toContain('/api/v1/agent/runs?');
		expect(fetchMock.mock.calls[0]?.[0]).toContain('chat_id=chat-1');
		expect(fetchMock.mock.calls[0]?.[0]).toContain('limit=20');
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'GET',
			credentials: 'include',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: 'Bearer token-1'
			}
		});
	});

	it('backfills events after a seq without using the OpenAI stream parser', async () => {
		const events = [
			{ run_id: 'run-1', seq: 8, event_type: 'action.summary', payload: {}, created_at: 1 }
		];
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ events })
		});
		vi.stubGlobal('fetch', fetchMock);

		const result = await getAgentRunEvents('token-1', 'run-1', { afterSeq: 7 });

		expect(result).toEqual(events);
		expect(fetchMock.mock.calls[0]?.[0]).toContain(
			'/api/v1/agent/runs/run-1/events/list?after_seq=7'
		);
		expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
			method: 'GET',
			credentials: 'include'
		});
	});

	it('builds JSON backfill URLs separately from the SSE stream URL', () => {
		expect(buildAgentRunEventsListUrl('run 1', { afterSeq: 12 })).toContain(
			'/api/v1/agent/runs/run%201/events/list?after_seq=12'
		);
		expect(buildAgentRunEventsUrl('run 1', { afterSeq: 12 })).toContain(
			'/api/v1/agent/runs/run%201/events?after_seq=12'
		);
	});

	it('builds EventSource URLs with after_seq and Last-Event-ID equivalent query state', () => {
		const url = buildAgentRunEventsUrl('run 1', { afterSeq: 12, lastEventId: '12' });

		expect(url).toContain('/api/v1/agent/runs/run%201/events?');
		expect(url).toContain('after_seq=12');
		expect(url).toContain('last_event_id=12');
	});

	it('creates an EventSource subscription using cookie credentials', () => {
		const source = createAgentRunEventsSource('run-1', { afterSeq: 2 });

		expect(source).toBe(FakeEventSource.instances[0]);
		expect(String(FakeEventSource.instances[0]?.url)).toContain(
			'/api/v1/agent/runs/run-1/events?after_seq=2'
		);
		expect(FakeEventSource.instances[0]?.options).toEqual({ withCredentials: true });
	});
});
