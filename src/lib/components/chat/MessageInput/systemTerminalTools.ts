export type SystemTerminalToolSpec = {
	name: string;
	description?: string;
};

type SystemTerminalServer = {
	id?: string;
	specs?: SystemTerminalToolSpec[];
};

export const convertTerminalOpenApiToSpecs = (openapi: Record<string, any> | null | undefined) => {
	if (
		!openapi ||
		typeof openapi !== 'object' ||
		!openapi.paths ||
		typeof openapi.paths !== 'object'
	) {
		return [];
	}

	const specs: SystemTerminalToolSpec[] = [];
	const seen = new Set<string>();

	for (const methods of Object.values(openapi.paths as Record<string, Record<string, any>>)) {
		if (!methods || typeof methods !== 'object') {
			continue;
		}

		for (const operation of Object.values(methods)) {
			if (!operation || typeof operation !== 'object') {
				continue;
			}

			const name = typeof operation.operationId === 'string' ? operation.operationId.trim() : '';
			if (!name || seen.has(name)) {
				continue;
			}

			seen.add(name);
			specs.push({
				name,
				description:
					(typeof operation.description === 'string' && operation.description.trim()) ||
					(typeof operation.summary === 'string' && operation.summary.trim()) ||
					''
			});
		}
	}

	return specs;
};

export const buildSelectedSystemTerminalTools = (
	terminalServers: SystemTerminalServer[],
	selectedTerminalId: string | null
) => {
	if (!selectedTerminalId) {
		return {};
	}

	const selectedTerminal = terminalServers.find((terminal) => terminal.id === selectedTerminalId);
	if (!selectedTerminal?.specs?.length) {
		return {};
	}

	return selectedTerminal.specs.reduce(
		(tools, spec) => {
			tools[`system_terminal:${selectedTerminalId}:${spec.name}`] = {
				name: spec.name,
				description: spec.description ?? '',
				enabled: true,
				implicit: true,
				terminalId: selectedTerminalId
			};
			return tools;
		},
		{} as Record<
			string,
			{
				name: string;
				description: string;
				enabled: true;
				implicit: true;
				terminalId: string;
			}
		>
	);
};
