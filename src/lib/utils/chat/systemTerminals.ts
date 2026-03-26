type SystemTerminal = {
	id: string;
	name: string;
};

type TerminalSpecEntry = {
	url: string;
	specs?: { name: string; description?: string }[];
	info?: { title?: string; description?: string };
};

export const buildSystemTerminalEntries = ({
	systemTerminals,
	fetchedSpecs,
	apiBaseUrl,
	token
}: {
	systemTerminals: SystemTerminal[];
	fetchedSpecs: TerminalSpecEntry[];
	apiBaseUrl: string;
	token: string;
}) => {
	const specsByUrl = new Map(fetchedSpecs.map((entry) => [entry.url, entry]));

	return systemTerminals.map((terminal) => {
		const url = `${apiBaseUrl}/terminals/${terminal.id}`;
		const specEntry = specsByUrl.get(url);

		return {
			id: terminal.id,
			name: terminal.name,
			url,
			key: token,
			auth_type: 'session',
			...(specEntry ? { specs: specEntry.specs, info: specEntry.info } : {})
		};
	});
};

export const buildSelectedSystemTerminalTools = (
	terminalServers: {
		id?: string;
		specs?: { name: string; description?: string }[];
	}[],
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
