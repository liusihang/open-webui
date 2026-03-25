type SyncableMessage = {
	content?: string | null;
	done?: boolean;
	responseContent?: string | null;
	reasoningContent?: string | null;
	model?: string | null;
	favorite?: unknown;
	error?: unknown;
	annotation?: { type?: string; rating?: number } | null;
	sources?: any[];
	code_executions?: any[];
	statusHistory?: any[];
	files?: any[];
	followUps?: any[];
	output?: any[];
	embeds?: any[];
};

const lastArrayItem = <T>(items?: T[] | null) =>
	Array.isArray(items) && items.length > 0 ? items[items.length - 1] : null;

const stableSourceKey = (source: any) => {
	if (!source) return '';
	const src = source.source ?? {};
	const metadata = Array.isArray(source.metadata) ? source.metadata[0] ?? {} : {};
	return [
		src.id ?? '',
		src.name ?? '',
		src.type ?? '',
		metadata.source ?? '',
		metadata.name ?? '',
		metadata.url ?? '',
		Array.isArray(source.document) ? source.document.length : 0
	].join('|');
};

const stableCodeExecutionKey = (execution: any) => {
	if (!execution) return '';
	return [
		execution.id ?? execution.uuid ?? '',
		execution.name ?? '',
		execution.language ?? '',
		execution.result?.error ?? '',
		Array.isArray(execution.result?.files) ? execution.result.files.length : 0
	].join('|');
};

const stableStatusKey = (status: any) => {
	if (!status) return '';
	return [status.done ?? '', status.action ?? '', status.description ?? ''].join('|');
};

const stableFileKey = (file: any) => {
	if (!file) return '';
	return [file.id ?? '', file.name ?? '', file.url ?? '', file.type ?? ''].join('|');
};

const stableOutputKey = (item: any) => {
	if (!item) return '';
	return [item.type ?? '', item.call_id ?? '', item.status ?? '', item.id ?? ''].join('|');
};

const stableEmbedKey = (embed: any) => {
	if (!embed) return '';
	return [embed.url ?? '', embed.title ?? '', embed.type ?? ''].join('|');
};

const stableFollowUpKey = (followUp: any) => {
	if (!followUp) return '';
	return typeof followUp === 'string' ? followUp : JSON.stringify(followUp);
};

const arrayChanged = (
	currentItems: any[] | undefined,
	nextItems: any[] | undefined,
	getKey: (item: any) => string
) => {
	const currentLength = currentItems?.length ?? 0;
	const nextLength = nextItems?.length ?? 0;
	if (currentLength !== nextLength) {
		return true;
	}

	return getKey(lastArrayItem(currentItems)) !== getKey(lastArrayItem(nextItems));
};

export const shouldSyncRenderedMessage = (
	currentMessage: SyncableMessage = {},
	nextMessage: SyncableMessage = {}
) => {
	return (
		currentMessage.content !== nextMessage.content ||
		currentMessage.done !== nextMessage.done ||
		currentMessage.responseContent !== nextMessage.responseContent ||
		currentMessage.reasoningContent !== nextMessage.reasoningContent ||
		currentMessage.model !== nextMessage.model ||
		currentMessage.favorite !== nextMessage.favorite ||
		JSON.stringify(currentMessage.error ?? null) !== JSON.stringify(nextMessage.error ?? null) ||
		JSON.stringify(currentMessage.annotation ?? null) !==
			JSON.stringify(nextMessage.annotation ?? null) ||
		arrayChanged(currentMessage.sources, nextMessage.sources, stableSourceKey) ||
		arrayChanged(
			currentMessage.code_executions,
			nextMessage.code_executions,
			stableCodeExecutionKey
		) ||
		arrayChanged(currentMessage.statusHistory, nextMessage.statusHistory, stableStatusKey) ||
		arrayChanged(currentMessage.files, nextMessage.files, stableFileKey) ||
		arrayChanged(currentMessage.followUps, nextMessage.followUps, stableFollowUpKey) ||
		arrayChanged(currentMessage.output, nextMessage.output, stableOutputKey) ||
		arrayChanged(currentMessage.embeds, nextMessage.embeds, stableEmbedKey)
	);
};
