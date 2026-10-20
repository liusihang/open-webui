/** Synthetic data, rendered through the production parser and component catalog. */
const tone = (light: string, dark: string) => ({ light, dark });
export const cityColors = {
	oslo: tone('#16856B', '#48D5AE'),
	porto: tone('#D46B32', '#FFAD78'),
	lyon: tone('#546AC9', '#9BABFF')
};
const surface = {
	foreground: tone('#263D49', '#DCEAEF'),
	background: tone('#F5F9FB', '#18262F'),
	border: tone('#D8E4E9', '#344C59'),
	accent: tone('#29768F', '#71C4DA')
};
const rows = [
	{ id: 'oslo', site: 'Oslo', clean: 94, demand: 72, status: 'On track' },
	{ id: 'porto', site: 'Porto', clean: 68, demand: 86, status: 'Review' },
	{ id: 'lyon', site: 'Lyon', clean: 82, demand: 79, status: 'On track' }
];
const elements = {
	root: {
		type: 'Stack',
		props: {},
		children: ['summary', 'metrics', 'charts', 'operations', 'controls']
	},
	summary: {
		type: 'Text',
		props: {
			text: 'Clean energy is ahead of plan. Current target: {{target}}%.',
			spans: [
				{ text: 'Clean energy ', color: cityColors.oslo, bold: true },
				{ text: 'is ahead of plan. Current target: ' },
				{ text: '{{target}}%', color: tone('#29768F', '#71C4DA'), bold: true },
				{ text: '.' }
			],
			bindings: [{ token: 'target', stateKey: 'target' }]
		},
		children: []
	},
	metrics: { type: 'Grid', props: { columns: 3 }, children: ['clean', 'capacity', 'attention'] },
	clean: {
		type: 'MetricCard',
		props: {
			title: 'Clean energy share',
			value: 81,
			unit: '%',
			description: 'Across all three sites',
			appearance: {
				foreground: tone('#18634F', '#95E8CC'),
				accent: cityColors.oslo,
				background: tone('#EAF7F0', '#152D27'),
				border: tone('#C8E8D8', '#2B5545')
			}
		},
		children: []
	},
	capacity: {
		type: 'MetricCard',
		props: {
			title: 'Operating capacity',
			value: 237,
			unit: 'MWh',
			description: 'Steady week-over-week',
			appearance: surface
		},
		children: []
	},
	attention: {
		type: 'MetricCard',
		props: {
			title: 'Sites to review',
			value: 1,
			description: 'Porto needs a demand check',
			appearance: {
				foreground: tone('#8C4924', '#FFD0AE'),
				accent: cityColors.porto,
				background: tone('#FFF2E8', '#35261D'),
				border: tone('#F1D5C0', '#62412C')
			}
		},
		children: []
	},
	charts: { type: 'Grid', props: { columns: 2 }, children: ['output', 'mix'] },
	output: {
		type: 'Chart',
		props: {
			title: 'Clean energy by site',
			kind: 'bar',
			description: 'Category colors stay attached to each site.',
			unit: '%',
			dataSource: 'sites',
			xField: 'site',
			yField: 'clean',
			idField: 'id',
			categoryColors: Object.entries(cityColors).map(([id, color]) => ({ id, color })),
			palette: 'forest'
		},
		children: []
	},
	mix: {
		type: 'Chart',
		props: {
			title: 'Generation mix',
			kind: 'pie',
			donut: true,
			description: 'Explicit color for every energy source.',
			unit: '%',
			data: [
				{ id: 'wind', label: 'Wind', value: 46, color: cityColors.oslo },
				{ id: 'solar', label: 'Solar', value: 35, color: cityColors.porto },
				{ id: 'hydro', label: 'Hydro', value: 19, color: cityColors.lyon }
			]
		},
		children: []
	},
	operations: {
		type: 'DataTable',
		props: {
			title: 'Site operations',
			dataSource: 'sites',
			rowKey: 'id',
			searchable: true,
			sortable: true,
			appearance: { border: tone('#DFE7EB', '#33434E') },
			columns: [
				{ key: 'site', label: 'Site' },
				{
					key: 'clean',
					label: 'Clean energy (%)',
					appearance: {
						foreground: tone('#17654F', '#9CE6C9'),
						background: tone('#EDF8F1', '#1A3029')
					}
				},
				{ key: 'demand', label: 'Demand (MWh)' },
				{ key: 'status', label: 'Status' }
			],
			rowStyles: [
				{
					id: 'porto',
					appearance: {
						foreground: tone('#8C4924', '#FFD0AE'),
						background: tone('#FFF5EC', '#32271F')
					}
				}
			],
			cellStyles: [
				{
					rowId: 'porto',
					column: 'status',
					appearance: {
						foreground: tone('#9A3412', '#FFCEB5'),
						background: tone('#FFDFCC', '#6B3321')
					}
				}
			]
		},
		children: []
	},
	controls: { type: 'Grid', props: { columns: 2 }, children: ['scenario', 'plan'] },
	scenario: {
		type: 'Card',
		props: {
			title: 'Explore the target',
			description: 'Native controls update the linked text above.',
			appearance: surface
		},
		children: ['target', 'review', 'reset']
	},
	target: {
		type: 'ParameterSlider',
		props: {
			title: 'Clean energy target',
			stateKey: 'target',
			min: 50,
			max: 100,
			step: 5,
			value: 75,
			unit: '%',
			appearance: surface
		},
		children: []
	},
	review: {
		type: 'Checkbox',
		props: {
			title: 'Include demand review',
			stateKey: 'review',
			value: true,
			appearance: { accent: tone('#16856B', '#48D5AE') }
		},
		children: []
	},
	reset: {
		type: 'Button',
		props: {
			label: 'Set target to 85%',
			actions: [{ type: 'set', stateKey: 'target', value: 85 }],
			appearance: {
				foreground: tone('#FFFFFF', '#10262C'),
				background: tone('#29768F', '#71C4DA'),
				accent: tone('#29768F', '#71C4DA')
			}
		},
		children: []
	},
	plan: {
		type: 'Card',
		props: {
			appearance: {
				foreground: tone('#384164', '#DBE0FF'),
				background: tone('#F5F5FC', '#222437'),
				border: tone('#E1E2F2', '#3C405E')
			}
		},
		children: ['flow']
	},
	flow: {
		type: 'Flow',
		props: {
			title: 'Next steps',
			appearance: { foreground: tone('#384164', '#DBE0FF'), accent: tone('#6469B8', '#ADB4FF') },
			steps: [
				{
					title: 'Review Porto demand',
					description: 'Prioritize the highlighted site and status.'
				},
				{
					title: 'Tune the clean energy target',
					description: 'Explore a local scenario before sharing.'
				},
				{
					title: 'Compare the next reporting period',
					description: 'Keep the same colors for each site.'
				}
			]
		},
		children: []
	}
};
export const fixtureSpec = { root: 'root', datasets: { sites: rows }, elements };
export const fixtureSource = [
	{ op: 'add', path: '/root', value: fixtureSpec.root },
	{ op: 'add', path: '/datasets/sites', value: rows },
	...Object.entries(elements).map(([key, value]) => ({
		op: 'add',
		path: `/elements/${key}`,
		value
	}))
]
	.map((operation) => JSON.stringify(operation))
	.join('\n');

/** Adversarial contrast fixture: foregrounds must resolve against their actual local surface. */
const contrastElements = {
	root: {
		type: 'Card',
		props: {
			title: 'Custom dark surface',
			description: 'Description remains legible on the authored surface.',
			appearance: { background: '#101010', foreground: '#EEEEEE' }
		},
		children: ['stack', 'chart']
	},
	stack: {
		type: 'Stack',
		props: { title: 'Inherited stack heading' },
		children: ['text', 'input', 'checkbox']
	},
	text: {
		type: 'Text',
		props: {
			text: 'Readable yellow span',
			spans: [{ text: 'Readable yellow span', color: '#FFFF00', bold: true }],
			bindings: []
		},
		children: []
	},
	input: {
		type: 'Input',
		props: {
			title: 'Inherited input label',
			stateKey: 'probeInput',
			value: 'Editable value',
			maxLength: 50
		},
		children: []
	},
	checkbox: {
		type: 'Checkbox',
		props: { title: 'Inherited checkbox label', stateKey: 'probeCheck', value: true },
		children: []
	},
	chart: {
		type: 'Chart',
		props: {
			title: 'Custom chart surface',
			description: 'Chart description remains readable.',
			kind: 'bar',
			appearance: { background: '#112233' },
			data: [{ id: 'probe', label: 'Matching fill', value: 10, color: '#112233' }]
		},
		children: []
	}
};
export const contrastSource = [
	{ op: 'add', path: '/root', value: 'root' },
	...Object.entries(contrastElements).map(([key, value]) => ({
		op: 'add',
		path: `/elements/${key}`,
		value
	}))
]
	.map((operation) => JSON.stringify(operation))
	.join('\n');
