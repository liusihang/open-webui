import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./General.svelte', import.meta.url), 'utf8');
const zhCN = JSON.parse(
	readFileSync(new URL('../../../i18n/locales/zh-CN/translation.json', import.meta.url), 'utf8')
);

describe('administrator announcement popup settings', () => {
	it('initializes every announcement field when older config rows are absent', () => {
		expect(source).toContain('normalizeAnnouncementConfig(loadedAdminConfig)');
		expect(source).toContain("$i18n.t('Failed to load settings')");
		expect(source).toContain('validateAnnouncementConfig(adminConfig)');
	});

	it('lets an administrator enable and version an announcement', () => {
		expect(source).toContain("$i18n.t('Enable Announcement Popup')");
		expect(source).toContain('bind:state={adminConfig.ANNOUNCEMENT_MODAL_ENABLED}');
		expect(source).toContain('bind:value={adminConfig.ANNOUNCEMENT_MODAL_KEY}');
		expect(source).toContain('Each user sees the popup once per key.');
	});

	it('binds the title and Markdown body to the protected admin config payload', () => {
		expect(source).toContain('bind:value={adminConfig.ANNOUNCEMENT_MODAL_TITLE}');
		expect(source).toContain('bind:value={adminConfig.ANNOUNCEMENT_MODAL_CONTENT}');
		expect(source).toContain(
			'Supports Markdown. This content will be shown in a popup after login.'
		);
	});

	it('provides Chinese labels and publishing guidance for administrators', () => {
		expect(zhCN['Enable Announcement Popup']).toBe('启用公告弹窗');
		expect(zhCN['Announcement Version Key']).toBe('公告版本标识');
		expect(
			zhCN[
				'Each user sees the popup once per key. Change this key when publishing a new announcement.'
			]
		).toContain('每位用户');
		expect(zhCN['Announcement Title']).toBe('公告标题');
		expect(zhCN['Announcement Content']).toBe('公告内容');
		expect(zhCN['Announcement version key is required when the popup is enabled.']).toContain(
			'版本标识'
		);
		expect(zhCN['Announcement content is required when the popup is enabled.']).toContain(
			'公告内容'
		);
	});
});
