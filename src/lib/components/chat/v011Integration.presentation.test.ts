import { existsSync, readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

const source = (relativePath: string) =>
	readFileSync(new URL(relativePath, import.meta.url), 'utf8');

describe('v0.11 frontend integration guardrails', () => {
	it.each([
		'../admin/Settings/Documents.svelte',
		'../admin/Settings/General.svelte',
		'../admin/Settings/Interface.svelte',
		'../admin/Settings/Models.svelte',
		'./Chat.svelte',
		'./MessageInput.svelte',
		'./Messages.svelte',
		'./Messages/Citations.svelte',
		'./Messages/ContentRenderer.svelte',
		'./Messages/ResponseMessage/StatusHistory.svelte',
		'./ModelSelector.svelte',
		'./ModelSelector/Selector.svelte',
		'./Navbar.svelte',
		'../common/ToolCallDisplay.svelte',
		'../layout/Sidebar.svelte',
		'../workspace/Knowledge/KnowledgeBase.svelte',
		'../../../routes/(app)/+layout.svelte'
	])('compiles conflict-owned component %s', (relativePath) => {
		const componentUrl = new URL(relativePath, import.meta.url);
		expect(() =>
			compile(readFileSync(componentUrl, 'utf8'), { filename: componentUrl.pathname })
		).not.toThrow();
	});

	it('keeps the reconciled Interface settings DOM structurally explicit', () => {
		const componentUrl = new URL('../admin/Settings/Interface.svelte', import.meta.url);
		const result = compile(readFileSync(componentUrl, 'utf8'), {
			filename: componentUrl.pathname
		});

		expect(result.warnings.map((warning) => warning.code)).not.toContain(
			'element_implicitly_closed'
		);
	});

	it('keeps the reconciled Navbar markup explicit for real button elements', () => {
		const componentUrl = new URL('./Navbar.svelte', import.meta.url);
		const result = compile(readFileSync(componentUrl, 'utf8'), {
			filename: componentUrl.pathname
		});

		expect(result.warnings.map((warning) => warning.code)).not.toContain(
			'element_invalid_self_closing_tag'
		);
	});

	it('keeps the official subagents runtime and excluded chat-file tools out of the frontend', () => {
		const adminSettings = source('../admin/Settings.svelte');
		const adminTabIcon = source('../admin/Settings/AdminTabIcon.svelte');
		const settingsModal = source('./SettingsModal.svelte');
		const configsApi = source('../../apis/configs/index.ts');
		const structuredOutput = source('./Messages/structuredOutput.ts');
		const userMessage = source('./Messages/UserMessage.svelte');
		const builtinTools = source('../workspace/Models/BuiltinTools.svelte');

		expect(adminSettings).not.toContain('Subagents');
		expect(adminTabIcon).not.toContain("id === 'subagents'");
		expect(adminTabIcon).not.toContain('UserCircle');
		expect(settingsModal).not.toContain('AdminSubagents');
		expect(configsApi).not.toContain('getSubagentsConfig');
		expect(configsApi).not.toContain('setSubagentsConfig');
		expect(structuredOutput).not.toContain("name === 'delegate_task'");
		expect(userMessage).not.toContain('SubagentResultRow');
		expect(builtinTools).not.toMatch(/^\s*(files|subagents):\s*\{/m);
		expect(existsSync(new URL('../admin/Settings/Subagents.svelte', import.meta.url))).toBe(false);
		expect(existsSync(new URL('./Messages/SubagentResultRow.svelte', import.meta.url))).toBe(false);
	});

	it('reconciles redesigned admin settings with custom announcements and retrieval controls', () => {
		const general = source('../admin/Settings/General.svelte');
		const interfaceSettings = source('../admin/Settings/Interface.svelte');
		const documents = source('../admin/Settings/Documents.svelte');

		expect(general).toContain('validateAnnouncementConfig');
		expect(general).toContain('RESPONSE_WATERMARK');
		expect(general).toContain('AdminSettingSection');
		expect(interfaceSettings).toContain('GLOBAL_SYSTEM_PROMPT');
		expect(interfaceSettings).toContain('CONTEXT_COMPACTION_MODEL');
		expect(documents).toContain('PDF_LOADER_MODE');
		expect(documents).toContain('DATALAB_MARKER_API_BASE_URL');
		expect(documents).toContain('DATALAB_MARKER_OUTPUT_FORMAT');
		expect(documents).toContain('EXTERNAL_DOCUMENT_LOADER_URL');
	});

	it('keeps custom Agent authority while restoring official chat navigation behavior', () => {
		const chat = source('./Chat.svelte');
		const navbar = source('./Navbar.svelte');
		const responseMessage = source('./Messages/ResponseMessage.svelte');

		expect(chat).toContain('serializeConversationModeCapabilityRequest');
		expect(chat).toContain('mode_profile_revision_id');
		expect(responseMessage).toContain('<AgentTranscript');
		expect(chat).toContain('<EmbeddedChatHistoryDropdown');
		expect(chat).toContain('useChatVariablesFallback');
		expect(chat).toContain('scheduleResponseScrollToBottom');
		expect(chat).toContain('refreshChatList');
		expect(navbar).toContain("export let title = ''");
	});

	it('keeps custom settings announcements while restoring URL-driven v0.11 settings', () => {
		const layout = source('../../../routes/(app)/+layout.svelte');

		expect(layout).toContain('showAdminAnnouncementModal');
		expect(layout).toContain('pendingAdminAnnouncementModal');
		expect(layout).toContain('handledSettingsUrl');
	});

	it('keeps custom and official locale keys from both sides of translation conflicts', () => {
		for (const locale of ['en-US', 'zh-CN']) {
			const messages = JSON.parse(
				source(`../../i18n/locales/${locale}/translation.json`)
			) as Record<string, string>;

			expect(messages).toHaveProperty('Light reasoning');
			expect(messages).toHaveProperty('Limit API keys to configured endpoints.');
			expect(messages).toHaveProperty('Model and reasoning settings');
			expect(messages).toHaveProperty('Model added to pinned models');
			expect(messages).toHaveProperty('Model added to selected models');
		}
	});
});
