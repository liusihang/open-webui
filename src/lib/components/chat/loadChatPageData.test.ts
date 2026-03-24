import { describe, expect, it } from 'vitest';

import { loadChatPageData } from './loadChatPageData';


function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}


describe('loadChatPageData', () => {
	it('returns the chat payload before ancillary requests settle', async () => {
		const calls: string[] = [];
		const tagsDeferred = deferred<string[]>();
		const taskDeferred = deferred<{ task_ids: string[] }>();

		const result = await loadChatPageData({
			token: 'token',
			chatId: 'chat-1',
			deps: {
				getChatById: async () => {
					calls.push('chat');
					return { id: 'chat-1', chat: { title: 'Large Chat' } };
				},
				getTagsById: async () => {
					calls.push('tags');
					return tagsDeferred.promise;
				},
				getTaskIdsByChatId: async () => {
					calls.push('tasks');
					return taskDeferred.promise;
				}
			}
		});

		expect(result.chat).toEqual({ id: 'chat-1', chat: { title: 'Large Chat' } });
		expect(calls).toEqual(['chat', 'tags', 'tasks']);

		let ancillarySettled = false;
		void result.ancillaryPromise.then(() => {
			ancillarySettled = true;
		});

		await Promise.resolve();
		expect(ancillarySettled).toBe(false);

		tagsDeferred.resolve(['alpha']);
		taskDeferred.resolve({ task_ids: ['task-1'] });

		await expect(result.ancillaryPromise).resolves.toEqual({
			chatId: 'chat-1',
			tags: ['alpha'],
			taskIds: ['task-1']
		});
	});

	it('falls back safely when ancillary requests fail', async () => {
		const result = await loadChatPageData({
			token: 'token',
			chatId: 'chat-2',
			deps: {
				getChatById: async () => ({ id: 'chat-2', chat: { title: 'Chat 2' } }),
				getTagsById: async () => {
					throw new Error('tags failed');
				},
				getTaskIdsByChatId: async () => {
					throw new Error('tasks failed');
				}
			}
		});

		await expect(result.ancillaryPromise).resolves.toEqual({
			chatId: 'chat-2',
			tags: [],
			taskIds: null
		});
	});
});
