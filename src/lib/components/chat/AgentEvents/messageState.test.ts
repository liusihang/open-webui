import { describe, expect, it, vi } from 'vitest';

import {
	AGENT_RUN_PENDING_REGISTRY_LIMIT,
	createAgentRunStopController,
	getActiveAgentRunId,
	getAgentRunStopAriaLabel,
	markAgentRunMessageDone
} from './messageState';

describe('markAgentRunMessageDone', () => {
	it('marks an Agent Run assistant message done when the run reaches a terminal state', () => {
		const message = {
			id: 'assistant-1',
			role: 'assistant',
			done: false,
			agent_run_id: 'run-1'
		};

		const changed = markAgentRunMessageDone(message, 'completed');

		expect(changed).toBe(true);
		expect(message.done).toBe(true);
	});

	it('does not mark Agent Run assistant messages done while the run is still active', () => {
		const message = {
			id: 'assistant-1',
			role: 'assistant',
			done: false,
			agent_run_id: 'run-1'
		};

		const changed = markAgentRunMessageDone(message, 'running');

		expect(changed).toBe(false);
		expect(message.done).toBe(false);
	});
});

describe('Agent Run cancellation state', () => {
	it('selects only the current active Agent assistant run', () => {
		expect(
			getActiveAgentRunId({
				currentId: 'assistant-1',
				messages: {
					'assistant-1': { role: 'assistant', done: false, agent_run_id: 'run-1' }
				}
			})
		).toBe('run-1');
		expect(
			getActiveAgentRunId({
				currentId: 'assistant-1',
				messages: {
					'assistant-1': { role: 'assistant', done: true, agent_run_id: 'run-1' }
				}
			})
		).toBeNull();
		expect(
			getActiveAgentRunId({
				currentId: 'assistant-1',
				messages: { 'assistant-1': { role: 'assistant', done: false } }
			})
		).toBeNull();
		expect(
			getActiveAgentRunId({
				currentId: 'assistant-1',
				messages: {
					'assistant-1': { role: 'assistant', done: undefined, agent_run_id: 'run-1' }
				}
			})
		).toBeNull();
	});
});

describe('Agent Run stop controller', () => {
	const createController = (
		cancelAgentRun: (runId: string) => Promise<unknown>,
		stopResponse: () => Promise<unknown> = async () => undefined
	) => createAgentRunStopController({ cancelAgentRun, stopResponse });

	const activeRunHistory = (suffix: string) => ({
		currentId: `assistant-a-${suffix}`,
		messages: {
			[`assistant-a-${suffix}`]: {
				role: 'assistant',
				done: false,
				agent_run_id: `run-a-${suffix}`
			},
			[`assistant-b-${suffix}`]: {
				role: 'assistant',
				done: false,
				agent_run_id: `run-b-${suffix}`
			}
		}
	});

	it('treats click and Escape as one pending cancel request and disables the control', async () => {
		let resolveRequest: (() => void) | undefined;
		const cancelAgentRun = vi.fn(
			() =>
				new Promise<void>((resolve) => {
					resolveRequest = resolve;
				})
		);
		const controller = createController(cancelAgentRun);
		const history = activeRunHistory('click-escape');

		const click = controller.requestStop(history);
		const escape = await controller.requestStop(history);

		expect(escape).toBe('agent_cancel_pending');
		expect(cancelAgentRun).toHaveBeenCalledTimes(1);
		expect(
			controller.getControlState({
				history,
				isActive: true,
				recording: false,
				prompt: '',
				hasFiles: false
			})
		).toMatchObject({
			visible: true,
			disabled: true,
			ariaBusy: true,
			agentRunId: 'run-a-click-escape'
		});

		resolveRequest?.();
		await expect(click).resolves.toBe('agent_cancel_requested');
		expect(controller.isRunPending('run-a-click-escape')).toBe(true);
		expect(markAgentRunMessageDone(history.messages['assistant-a-click-escape'], 'cancelled')).toBe(
			true
		);
		controller.syncHistory(history);
	});

	it('keeps pending state when switching away and back to the same run', async () => {
		const cancelAgentRun = vi.fn().mockResolvedValue(undefined);
		const controller = createController(cancelAgentRun);
		const history = activeRunHistory('switch');

		await expect(controller.requestStop(history)).resolves.toBe('agent_cancel_requested');
		history.currentId = 'assistant-b-switch';
		controller.syncHistory(history);
		expect(controller.isRunPending('run-a-switch')).toBe(true);

		history.currentId = 'assistant-a-switch';
		await expect(controller.requestStop(history)).resolves.toBe('agent_cancel_pending');
		expect(cancelAgentRun).toHaveBeenCalledTimes(1);
		expect(markAgentRunMessageDone(history.messages['assistant-a-switch'], 'cancelled')).toBe(true);
		controller.syncHistory(history);
	});

	it('preserves a pending run across controller remounts until its terminal event arrives', async () => {
		const firstCancelAgentRun = vi.fn().mockResolvedValue(undefined);
		const firstController = createController(firstCancelAgentRun);
		const history = activeRunHistory('remount');

		await expect(firstController.requestStop(history)).resolves.toBe('agent_cancel_requested');
		const remountedCancelAgentRun = vi.fn().mockResolvedValue(undefined);
		const remountedController = createController(remountedCancelAgentRun);

		await expect(remountedController.requestStop(history)).resolves.toBe('agent_cancel_pending');
		expect(firstCancelAgentRun).toHaveBeenCalledTimes(1);
		expect(remountedCancelAgentRun).not.toHaveBeenCalled();

		expect(markAgentRunMessageDone(history.messages['assistant-a-remount'], 'cancelled')).toBe(
			true
		);
		remountedController.syncHistory(history);
		expect(remountedController.isRunPending('run-a-remount')).toBe(false);
	});

	it('notifies a remounted controller when the original cancel request fails', async () => {
		let rejectOriginalRequest: ((error: Error) => void) | undefined;
		const originalController = createController(
			() =>
				new Promise((_, reject) => {
					rejectOriginalRequest = reject;
				})
		);
		const history = activeRunHistory('remount-rejection');
		const originalRequest = originalController.requestStop(history);
		const remountedController = createController(async () => undefined);
		const pendingStates: boolean[] = [];
		const unsubscribe = remountedController.subscribe(() => {
			pendingStates.push(remountedController.isRunPending('run-a-remount-rejection'));
		});

		expect(remountedController.isRunPending('run-a-remount-rejection')).toBe(true);
		rejectOriginalRequest?.(new Error('cancel failed'));
		await expect(originalRequest).rejects.toThrow('cancel failed');
		expect(remountedController.isRunPending('run-a-remount-rejection')).toBe(false);
		expect(pendingStates.at(-1)).toBe(false);
		unsubscribe();
	});

	it('re-enables cancellation after failure so the same run can retry', async () => {
		const cancelAgentRun = vi
			.fn()
			.mockRejectedValueOnce(new Error('network down'))
			.mockResolvedValueOnce(undefined);
		const controller = createController(cancelAgentRun);
		const history = activeRunHistory('retry');

		await expect(controller.requestStop(history)).rejects.toThrow('network down');
		expect(controller.isRunPending('run-a-retry')).toBe(false);
		expect(
			controller.getControlState({
				history,
				isActive: true,
				recording: false,
				prompt: '',
				hasFiles: false
			})
		).toMatchObject({
			visible: true,
			disabled: false,
			ariaBusy: false,
			agentRunId: 'run-a-retry'
		});
		await expect(controller.requestStop(history)).resolves.toBe('agent_cancel_requested');
		expect(cancelAgentRun).toHaveBeenCalledTimes(2);
		expect(markAgentRunMessageDone(history.messages['assistant-a-retry'], 'cancelled')).toBe(true);
		controller.syncHistory(history);
	});

	it('clears only the matching run after the server terminal state updates its message', async () => {
		const cancelAgentRun = vi.fn().mockResolvedValue(undefined);
		const controller = createController(cancelAgentRun);
		const history = activeRunHistory('terminal');

		await controller.requestStop(history);
		history.currentId = 'assistant-b-terminal';
		await controller.requestStop(history);
		expect(controller.isRunPending('run-a-terminal')).toBe(true);
		expect(controller.isRunPending('run-b-terminal')).toBe(true);

		expect(markAgentRunMessageDone(history.messages['assistant-a-terminal'], 'cancelled')).toBe(
			true
		);
		controller.syncHistory(history);
		expect(controller.isRunPending('run-a-terminal')).toBe(false);
		expect(controller.isRunPending('run-b-terminal')).toBe(true);
		expect(markAgentRunMessageDone(history.messages['assistant-b-terminal'], 'cancelled')).toBe(
			true
		);
		controller.syncHistory(history);
	});

	it('does not enumerate history when no Agent cancellation is pending', () => {
		const controller = createController(async () => undefined);
		let ownKeysCalls = 0;
		const messages = new Proxy(
			{
				'assistant-performance-empty': {
					role: 'assistant',
					done: false,
					agent_run_id: 'run-performance-empty'
				}
			},
			{
				ownKeys: (target) => {
					ownKeysCalls += 1;
					return Reflect.ownKeys(target);
				}
			}
		);

		controller.getControlState({
			history: { currentId: 'assistant-performance-empty', messages },
			isActive: true,
			recording: false,
			prompt: '',
			hasFiles: false
		});

		expect(ownKeysCalls).toBe(0);
	});

	it('checks only registered pending messages instead of enumerating full history', async () => {
		const controller = createController(async () => undefined);
		const history = activeRunHistory('performance-pending');
		await controller.requestStop(history);

		let ownKeysCalls = 0;
		const accessedKeys: string[] = [];
		const messages = new Proxy(
			{
				...history.messages,
				...Object.fromEntries(
					Array.from({ length: 100 }, (_, index) => [
						`unrelated-${index}`,
						{ role: 'assistant', done: true }
					])
				)
			},
			{
				ownKeys: (target) => {
					ownKeysCalls += 1;
					return Reflect.ownKeys(target);
				},
				get: (target, property, receiver) => {
					if (typeof property === 'string') accessedKeys.push(property);
					return Reflect.get(target, property, receiver);
				}
			}
		);
		const proxiedHistory = { ...history, messages };

		controller.getControlState({
			history: proxiedHistory,
			isActive: true,
			recording: false,
			prompt: '',
			hasFiles: false
		});

		expect(ownKeysCalls).toBe(0);
		expect(new Set(accessedKeys)).toEqual(new Set(['assistant-a-performance-pending']));
		expect(
			markAgentRunMessageDone(history.messages['assistant-a-performance-pending'], 'cancelled')
		).toBe(true);
		controller.syncHistory(history);
	});

	it('uses ordinary stop without sending Agent cancel for non-active Agent state', async () => {
		const cancelAgentRun = vi.fn().mockResolvedValue(undefined);
		const stopResponse = vi.fn().mockResolvedValue(undefined);
		const controller = createController(cancelAgentRun, stopResponse);
		const history = {
			currentId: 'assistant-1',
			messages: {
				'assistant-1': { role: 'assistant', done: undefined, agent_run_id: 'run-1' }
			}
		};

		await expect(controller.requestStop(history)).resolves.toBe('legacy_stopped');
		expect(stopResponse).toHaveBeenCalledTimes(1);
		expect(cancelAgentRun).not.toHaveBeenCalled();
	});

	it('hides the response stop control while VoiceRecording owns the composer', () => {
		const controller = createController(async () => undefined);

		expect(
			controller.getControlState({
				history: activeRunHistory('voice'),
				isActive: true,
				recording: true,
				prompt: '',
				hasFiles: false
			})
		).toMatchObject({
			visible: false,
			disabled: false,
			ariaBusy: false,
			agentRunId: 'run-a-voice'
		});
	});

	it('builds the pending accessibility label from existing localized Stop and Pending keys', () => {
		const translate = vi.fn((key: string) => ({ Stop: '停止', Pending: '处理中' })[key] ?? key);

		expect(
			getAgentRunStopAriaLabel(
				{ visible: true, disabled: true, ariaBusy: true, agentRunId: 'run-label' },
				translate
			)
		).toBe('停止: 处理中');
		expect(translate.mock.calls).toEqual([['Stop'], ['Pending']]);
	});

	it('bounds unreachable pending runs by evicting the oldest registry entry', async () => {
		const controller = createController(async () => ({ state: 'cancelled' }));
		const histories = Array.from({ length: AGENT_RUN_PENDING_REGISTRY_LIMIT + 1 }, (_, index) => ({
			currentId: `assistant-capacity-${index}`,
			messages: {
				[`assistant-capacity-${index}`]: {
					role: 'assistant',
					done: false,
					agent_run_id: `run-capacity-${index}`
				}
			}
		}));

		for (const history of histories) {
			await controller.requestStop(history);
		}

		expect(controller.isRunPending('run-capacity-0')).toBe(false);
		expect(controller.isRunPending('run-capacity-1')).toBe(true);
		expect(controller.isRunPending(`run-capacity-${AGENT_RUN_PENDING_REGISTRY_LIMIT}`)).toBe(true);
		expect(
			histories.filter((_, index) => controller.isRunPending(`run-capacity-${index}`))
		).toHaveLength(AGENT_RUN_PENDING_REGISTRY_LIMIT);

		for (const history of histories) {
			const message = history.messages[history.currentId];
			markAgentRunMessageDone(message, 'cancelled');
			controller.syncHistory(history);
		}
	});
});
