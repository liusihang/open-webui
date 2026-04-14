import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

describe('MessageInput terminal filesystem entry', () => {
	it('renders filesystem shortcut entry with expected interaction branches', () => {
		const filePath = resolve(process.cwd(), 'src/lib/components/chat/MessageInput.svelte');
		const source = readFileSync(filePath, 'utf-8');

		expect(source).toContain('id="open-terminal-filesystem-button"');
		expect(source).toContain('if ($selectedTerminalId) {');
		expect(source).toContain('showControls.set(true);');
		expect(source).toContain('showTerminalMenu = true;');
	});
});
