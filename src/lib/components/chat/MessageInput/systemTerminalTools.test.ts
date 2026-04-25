import { describe, expect, it } from 'vitest';

import {
	buildSelectedSystemTerminalTools,
	convertTerminalOpenApiToSpecs
} from './systemTerminalTools';

describe('systemTerminalTools', () => {
	it('converts terminal openapi operations into unique tool specs', () => {
		const specs = convertTerminalOpenApiToSpecs({
			paths: {
				'/run': {
					post: {
						operationId: 'run_command',
						description: 'Run a shell command'
					}
				},
				'/files': {
					get: {
						operationId: 'list_files',
						summary: 'List files'
					},
					post: {
						operationId: 'run_command',
						description: 'Duplicate operation should be ignored'
					}
				}
			}
		});

		expect(specs).toEqual([
			{ name: 'run_command', description: 'Run a shell command' },
			{ name: 'list_files', description: 'List files' }
		]);
	});

	it('builds implicit tool entries for the selected system terminal only', () => {
		const tools = buildSelectedSystemTerminalTools(
			[
				{
					id: 'system-terminal',
					specs: [{ name: 'run_command', description: 'Run a shell command' }]
				},
				{
					id: 'other-terminal',
					specs: [{ name: 'list_files', description: 'List files' }]
				}
			],
			'system-terminal'
		);

		expect(tools).toEqual({
			'system_terminal:system-terminal:run_command': {
				name: 'run_command',
				description: 'Run a shell command',
				enabled: true,
				implicit: true,
				terminalId: 'system-terminal'
			}
		});
	});
});
