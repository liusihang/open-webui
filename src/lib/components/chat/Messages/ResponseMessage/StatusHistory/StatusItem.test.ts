import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import StatusItem from './StatusItem.svelte';
import { createTestI18n } from '../../IntelligentUI/test-i18n';

function html(description: string, done: boolean, action = 'terminal_startup') {
	return render(StatusItem, {
		props: { status: { action, description, done } },
		context: new Map([['i18n', createTestI18n('zh-CN')]])
	}).body;
}

describe('terminal preparation status', () => {
	it.each([
		['Preparing terminal', false, '正在准备终端'],
		['Terminal ready', true, '终端已就绪'],
		['Terminal preparation failed', true, '终端准备失败']
	])('localizes %s and stops the pending animation when done', (description, done, expected) => {
		const result = html(description, done);
		expect(result).toContain(expected);
		expect(result.includes('shimmer')).toBe(!done);
	});

	it('does not reinterpret an unrelated status description', () => {
		expect(html('Preparing terminal', true, 'other')).toContain('Preparing terminal');
	});
});
