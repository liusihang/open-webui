type ChannelMessage = {
	id: string;
	temp_id?: string | null;
	meta?: {
		streaming?: boolean;
		done?: boolean;
	};
	[key: string]: unknown;
};

export const appendChannelMessage = (
	messages: ChannelMessage[],
	data: ChannelMessage
): ChannelMessage[] => {
	const tempId = data?.temp_id ?? null;

	return [{ ...data, temp_id: null }, ...messages.filter((message) => !tempId || message?.temp_id !== tempId)];
};

export const replaceChannelMessage = (
	messages: ChannelMessage[],
	data: ChannelMessage
): ChannelMessage[] => {
	return messages.map((message) => (message.id === data.id ? { ...message, ...data } : message));
};

export const shouldAutoScrollOnMessageUpdate = ({
	scrollEnd,
	nextMessage
}: {
	scrollEnd: boolean;
	nextMessage?: ChannelMessage | null;
}): boolean => {
	return Boolean(scrollEnd && nextMessage);
};
