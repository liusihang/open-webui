import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it, vi } from 'vitest';

// Verify the settings form's actual API payload when number inputs are cleared.
function settingsSave(values: unknown[]) {
	const source = readFileSync('src/lib/components/admin/Settings/Subagents.svelte', 'utf8');
	const tree = ts.createSourceFile(
		'settings.ts',
		source.split('<script lang="ts">')[1].split('</script>')[0],
		ts.ScriptTarget.Latest,
		true
	);
	const node = tree.statements.find(
		(node) =>
			ts.isVariableStatement(node) &&
			node.declarationList.declarations.some((decl) => decl.name.getText(tree) === 'save')
	)!;
	const code = ts.transpileModule(node.getText(tree), {
		compilerOptions: { target: ts.ScriptTarget.ES2022 }
	}).outputText;
	const setSubagentsConfig = vi.fn().mockResolvedValue({});
	const save = new Function(
		'setSubagentsConfig',
		'values',
		`
		let saving, enabled = true, backgroundEnabled = true, systemPrompt = '';
		const [maxConcurrent, maxAsync, maxIterations, maxOutput] = values;
		const localStorage = {token: 'fixture'}, $i18n = {t: text => text};
		const appConfig = {set: async () => {}}, getBackendConfig = async () => ({});
		const toast = {success: () => {}, error: () => {}};
		${code}; return save;
	`
	)(setSubagentsConfig, values);
	return { save, request: setSubagentsConfig };
}

describe('subagent settings default policy', () => {
	it.each([null, undefined, '', 0])(
		'saves finite defaults for empty/zero input %s',
		async (empty) => {
			const form = settingsSave([empty, empty, empty, empty]);
			await form.save();
			expect(form.request).toHaveBeenCalledWith(
				'fixture',
				expect.objectContaining({
					SUBAGENTS_MAX_CONCURRENT: 20,
					SUBAGENTS_MAX_ASYNC: 20,
					SUBAGENTS_MAX_ITERATIONS: 30,
					SUBAGENTS_MAX_OUTPUT: 30000
				})
			);
		}
	);
	it('preserves explicit -1 concurrency/background settings and configured finite limits', async () => {
		const form = settingsSave([-1, -1, 50, 40000]);
		await form.save();
		expect(form.request).toHaveBeenCalledWith(
			'fixture',
			expect.objectContaining({
				SUBAGENTS_MAX_CONCURRENT: -1,
				SUBAGENTS_MAX_ASYNC: -1,
				SUBAGENTS_MAX_ITERATIONS: 50,
				SUBAGENTS_MAX_OUTPUT: 40000
			})
		);
	});
});
