import { describe, expect, it } from 'vitest';

import { normalizeAnnouncementConfig, validateAnnouncementConfig } from './announcementConfig';

describe('administrator announcement config state', () => {
	it('preserves a complete response and normalizes absent or null announcement fields', () => {
		expect(
			normalizeAnnouncementConfig({
				ENABLE_SIGNUP: true,
				ANNOUNCEMENT_MODAL_ENABLED: null,
				ANNOUNCEMENT_MODAL_KEY: null
			})
		).toMatchObject({
			ENABLE_SIGNUP: true,
			ANNOUNCEMENT_MODAL_ENABLED: false,
			ANNOUNCEMENT_MODAL_KEY: '',
			ANNOUNCEMENT_MODAL_TITLE: '',
			ANNOUNCEMENT_MODAL_CONTENT: ''
		});
	});

	it('allows a disabled announcement without content', () => {
		expect(validateAnnouncementConfig({ ANNOUNCEMENT_MODAL_ENABLED: false })).toBeNull();
	});

	it('rejects enabled announcements without a displayable key or body', () => {
		expect(
			validateAnnouncementConfig({
				ANNOUNCEMENT_MODAL_ENABLED: true,
				ANNOUNCEMENT_MODAL_KEY: '  ',
				ANNOUNCEMENT_MODAL_CONTENT: 'Release notes'
			})
		).toBe('Announcement version key is required when the popup is enabled.');

		expect(
			validateAnnouncementConfig({
				ANNOUNCEMENT_MODAL_ENABLED: true,
				ANNOUNCEMENT_MODAL_KEY: '2026-07-release',
				ANNOUNCEMENT_MODAL_CONTENT: '\n'
			})
		).toBe('Announcement content is required when the popup is enabled.');
	});
});
