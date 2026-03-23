import { getOpenClawMe } from '$lib/apis/channels';

export const OPENCLAW_LABEL = 'OpenClaw';
export const OPENCLAW_CHANNEL_TYPE = 'openclaw';

let resolvedOpenClawChannelId: string | null = null;
let resolvingOpenClawChannelId: Promise<string | null> | null = null;

type OpenClawIdentity = {
	meta?: {
		openclaw?: {
			name?: string;
		};
	};
	user?: {
		name?: string;
		role?: string;
	};
	name?: string;
	role?: string;
};

export const resetOpenClawChannelResolver = () => {
	resolvedOpenClawChannelId = null;
	resolvingOpenClawChannelId = null;
};

export const resolveOpenClawChannelId = async (token: string = '') => {
	if (resolvedOpenClawChannelId) {
		return resolvedOpenClawChannelId;
	}

	if (!resolvingOpenClawChannelId) {
		resolvingOpenClawChannelId = getOpenClawMe(token)
			.then((channel) => {
				resolvedOpenClawChannelId = channel?.id ?? null;
				return resolvedOpenClawChannelId;
			})
			.finally(() => {
				resolvingOpenClawChannelId = null;
			});
	}

	return await resolvingOpenClawChannelId;
};

export const isOpenClawChannel = (channel?: { type?: string } | null) =>
	channel?.type === OPENCLAW_CHANNEL_TYPE;

export const isOpenClawIdentity = (identity?: OpenClawIdentity | null) =>
	Boolean(
		identity?.meta?.openclaw ||
			identity?.role === OPENCLAW_CHANNEL_TYPE ||
			identity?.user?.role === OPENCLAW_CHANNEL_TYPE
	);

export const getOpenClawDisplayName = (identity?: OpenClawIdentity | null) => {
	if (identity?.meta?.openclaw?.name) {
		return identity.meta.openclaw.name;
	}

	if (identity?.name) {
		return identity.name;
	}

	if (identity?.user?.name) {
		return identity.user.name;
	}

	return OPENCLAW_LABEL;
};
