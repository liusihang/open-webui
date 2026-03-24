type TaskIdsResponse = { task_ids?: string[] } | null;

type ChatPageDataDeps<TChat> = {
	getChatById: (token: string, chatId: string) => Promise<TChat>;
	getTagsById: (token: string, chatId: string) => Promise<string[]>;
	getTaskIdsByChatId: (token: string, chatId: string) => Promise<TaskIdsResponse>;
};

type LoadChatPageDataOptions<TChat> = {
	token: string;
	chatId: string;
	deps: ChatPageDataDeps<TChat>;
};

export const loadChatPageData = async <TChat>({
	token,
	chatId,
	deps
}: LoadChatPageDataOptions<TChat>) => {
	const chat = await deps.getChatById(token, chatId);

	const ancillaryPromise = Promise.allSettled([
		deps.getTagsById(token, chatId),
		deps.getTaskIdsByChatId(token, chatId)
	]).then(([tagsResult, taskIdsResult]) => ({
		chatId,
		tags: tagsResult.status === 'fulfilled' ? tagsResult.value : [],
		taskIds:
			taskIdsResult.status === 'fulfilled' ? (taskIdsResult.value?.task_ids ?? []) : null
	}));

	return { chat, ancillaryPromise };
};
