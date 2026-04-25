import { getChannels } from '$lib/apis/channels';

export const OPENCLAW_LABEL = 'OpenClaw';
export const OPENCLAW_CHANNEL_TYPE = 'openclaw';

type ChannelSummary = {
	id?: string | null;
	type?: string | null;
	name?: string | null;
};

const resolvedChannelIds = new Map<string, string | null>();
const resolvingChannelIds = new Map<string, Promise<string | null>>();

export const resetOpenClawChannelResolver = () => {
	resolvedChannelIds.clear();
	resolvingChannelIds.clear();
};

export const resolveOpenClawChannelId = async (token = '') => {
	if (resolvedChannelIds.has(token)) {
		return resolvedChannelIds.get(token) ?? null;
	}

	if (!resolvingChannelIds.has(token)) {
		const request = getChannels(token)
			.then((channels: ChannelSummary[] | null | undefined) => {
				const channelId =
					channels?.find((channel) => channel?.type === OPENCLAW_CHANNEL_TYPE)?.id ?? null;
				resolvedChannelIds.set(token, channelId);
				return channelId;
			})
			.finally(() => {
				resolvingChannelIds.delete(token);
			});

		resolvingChannelIds.set(token, request);
	}

	return await resolvingChannelIds.get(token)!;
};
