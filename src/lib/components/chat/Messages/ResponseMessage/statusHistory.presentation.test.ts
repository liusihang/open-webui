import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const readSource = (relativePath: string) =>
	readFileSync(new URL(relativePath, import.meta.url), 'utf8');

describe('StatusHistory presentation guardrails', () => {
	it('keeps the summary shell light and neutral', () => {
		const source = readSource('./StatusHistory.svelte');

		expect(source).not.toContain('min-h-11 rounded-xl border');
		expect(source).not.toContain('bg-violet-50/60');
		expect(source).not.toContain('border-violet-300/70');
		expect(source).toContain('text-xs');
	});

	it('keeps status text compact instead of text-base', () => {
		const statusItem = readSource('./StatusHistory/StatusItem.svelte');
		const thinking = readSource('./StatusHistory/ThinkingStatusRow.svelte');
		const artifact = readSource('./StatusHistory/ArtifactStatusRow.svelte');
		const subagent = readSource('./StatusHistory/SubagentStatusRow.svelte');
		const approval = readSource('./StatusHistory/ApprovalStatusRow.svelte');

		expect(statusItem).not.toContain('text-base');
		expect(thinking).not.toContain('text-base');
		expect(artifact).not.toContain('text-base');
		expect(subagent).not.toContain('text-base');
		expect(approval).not.toContain('text-base');
	});

	it('hides tool debug details behind a disclosure', () => {
		const source = readSource('./StatusHistory/ToolStatusRow.svelte');

		expect(source).toContain('<details');
		expect(source).toContain('Dev details');
	});

	it('keeps artifact, subagent, and approval rows visually quiet', () => {
		const artifact = readSource('./StatusHistory/ArtifactStatusRow.svelte');
		const subagent = readSource('./StatusHistory/SubagentStatusRow.svelte');
		const approval = readSource('./StatusHistory/ApprovalStatusRow.svelte');

		expect(artifact).not.toContain('emerald');
		expect(subagent).not.toContain('violet');
		expect(approval).toContain('bg-amber-50');
		expect(approval).toContain('text-gray-500');
		expect(approval).not.toContain('text-amber-700');
	});
});
