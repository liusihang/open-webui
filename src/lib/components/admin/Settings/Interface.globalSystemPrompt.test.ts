import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./Interface.svelte', import.meta.url), 'utf8');

describe('administrator global system prompt setting', () => {
	it('loads and binds the global system prompt through chat config', () => {
		expect(source).toContain("GLOBAL_SYSTEM_PROMPT: ''");
		expect(source).toContain('bind:value={chatConfig.GLOBAL_SYSTEM_PROMPT}');
	});

	it('explains scope and precedence without presenting the prompt as a security control', () => {
		expect(source).toContain("$i18n.t('Global System Prompt')");
		expect(source).toContain('ordinary chat and Agent Mode');
		expect(source).toContain('Model-specific prompts are applied afterward');
		expect(source).toContain('does not replace backend permissions');
	});
});
