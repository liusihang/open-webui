export type AnnouncementConfig = Record<string, unknown> & {
	ANNOUNCEMENT_MODAL_ENABLED?: boolean | null;
	ANNOUNCEMENT_MODAL_KEY?: string | null;
	ANNOUNCEMENT_MODAL_TITLE?: string | null;
	ANNOUNCEMENT_MODAL_CONTENT?: string | null;
};

export const normalizeAnnouncementConfig = (config: AnnouncementConfig) => ({
	...config,
	ANNOUNCEMENT_MODAL_ENABLED: config.ANNOUNCEMENT_MODAL_ENABLED ?? false,
	ANNOUNCEMENT_MODAL_KEY: config.ANNOUNCEMENT_MODAL_KEY ?? '',
	ANNOUNCEMENT_MODAL_TITLE: config.ANNOUNCEMENT_MODAL_TITLE ?? '',
	ANNOUNCEMENT_MODAL_CONTENT: config.ANNOUNCEMENT_MODAL_CONTENT ?? ''
});

export const validateAnnouncementConfig = (config: AnnouncementConfig): string | null => {
	if (!(config.ANNOUNCEMENT_MODAL_ENABLED ?? false)) {
		return null;
	}
	if (!(config.ANNOUNCEMENT_MODAL_KEY ?? '').trim()) {
		return 'Announcement version key is required when the popup is enabled.';
	}
	if (!(config.ANNOUNCEMENT_MODAL_CONTENT ?? '').trim()) {
		return 'Announcement content is required when the popup is enabled.';
	}
	return null;
};
