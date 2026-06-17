import { writable } from 'svelte/store';

import { createAgentRunEventState, foldAgentRunEvent } from './eventFold';
import type { AgentRunEvent, AgentRunEventState } from './types';

export const createAgentRunEventsStore = (
	initialState: AgentRunEventState = createAgentRunEventState()
) => {
	const store = writable(initialState);

	return {
		subscribe: store.subscribe,
		reset: () => store.set(createAgentRunEventState()),
		backfill: (events: AgentRunEvent[]) => {
			store.update((state) => {
				return events.reduce((nextState, event) => foldAgentRunEvent(nextState, event), state);
			});
		},
		fold: (event: AgentRunEvent) => {
			store.update((state) => foldAgentRunEvent(state, event));
		}
	};
};
