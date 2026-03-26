import { describe, expect, it } from 'vitest';

import {
	buildSelectedSystemTerminalTools,
	buildSystemTerminalEntries
} from './systemTerminals';

describe('buildSystemTerminalEntries', () => {
	it('merges fetched specs onto system terminal entries using proxy urls', () => {
		const systemTerminals = [
			{ id: 'terminals', name: 'terminals' },
			{ id: 'backup', name: 'backup' }
		];

		const fetchedSpecs = [
			{
				url: '/api/v1/terminals/terminals',
				specs: [{ name: 'run_command', description: 'Run shell command' }],
				info: { title: 'Terminals', description: 'System terminal tools' }
			}
		];

		expect(
			buildSystemTerminalEntries({
				systemTerminals,
				fetchedSpecs,
				apiBaseUrl: '/api/v1',
				token: 'jwt-token'
			})
		).toEqual([
			{
				id: 'terminals',
				name: 'terminals',
				url: '/api/v1/terminals/terminals',
				key: 'jwt-token',
				auth_type: 'session',
				specs: [{ name: 'run_command', description: 'Run shell command' }],
				info: { title: 'Terminals', description: 'System terminal tools' }
			},
			{
				id: 'backup',
				name: 'backup',
				url: '/api/v1/terminals/backup',
				key: 'jwt-token',
				auth_type: 'session'
			}
		]);
	});
});

describe('buildSelectedSystemTerminalTools', () => {
	it('returns implicit tools for the selected system terminal', () => {
		const tools = buildSelectedSystemTerminalTools([
			{
				id: 'terminals',
				name: 'terminals',
				specs: [
					{ name: 'run_command', description: 'Run shell command' },
					{ name: 'read_file', description: 'Read file content' }
				]
			}
		], 'terminals');

		expect(tools).toEqual({
			'system_terminal:terminals:run_command': {
				name: 'run_command',
				description: 'Run shell command',
				enabled: true,
				implicit: true,
				terminalId: 'terminals'
			},
			'system_terminal:terminals:read_file': {
				name: 'read_file',
				description: 'Read file content',
				enabled: true,
				implicit: true,
				terminalId: 'terminals'
			}
		});
	});

	it('returns no implicit tools when no system terminal is selected', () => {
		expect(
			buildSelectedSystemTerminalTools(
				[
					{
						id: 'terminals',
						name: 'terminals',
						specs: [{ name: 'run_command', description: 'Run shell command' }]
					}
				],
				null
			)
		).toEqual({});
	});
});
