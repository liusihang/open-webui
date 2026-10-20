import { describe, expect, it } from 'vitest';
import {
	appearanceStyle,
	chartColor,
	colorPair,
	colorVariables,
	contrastRatio,
	isColorValue,
	readableColor,
	tableCellAppearance,
	validateAppearance,
	validateColor
} from './colors';
import { parseIntelligentUI } from './index';
import { tableRows } from './data';
const node = (type: string, props: any, children: string[] = []) => ({ type, props, children });
const tableSpec = () => ({
	root: 'table',
	elements: {
		table: node('DataTable', {
			title: 'Plans',
			dataSource: 'plans',
			rowKey: 'id',
			searchable: true,
			sortable: true,
			appearance: { foreground: '#123', background: '#fff' },
			columns: [
				{ key: 'name', label: 'Plan' },
				{ key: 'cost', label: 'Cost', appearance: { foreground: '#456' } }
			],
			rowStyles: [{ id: 'b', appearance: { background: '#abc', foreground: '#789' } }],
			cellStyles: [{ rowId: 'b', column: 'cost', appearance: { foreground: '#012' } }]
		})
	},
	datasets: {
		plans: [
			{ id: 'a', name: 'Alpha', cost: 20 },
			{ id: 'b', name: 'Beta', cost: 10 }
		]
	}
});
describe('bounded color protocol and readable rendering', () => {
	it('accepts freely chosen hex colors and exact theme pairs, not only presets', () => {
		for (const value of ['#ace', '#1A2b3C', { light: '#123', dark: '#def' }])
			expect(validateColor(value)).toEqual(value);
		expect(
			validateAppearance({
				foreground: '#e91e63',
				background: { light: '#fff', dark: '#123' },
				palette: 'forest'
			}).foreground
		).toBe('#e91e63');
		expect(colorPair('#AbC')).toEqual({ light: '#aabbcc', dark: '#aabbcc' });
	});
	it('rejects CSS, URLs, alpha, malformed pairs and inherited fields at each boundary', () => {
		for (const value of [
			'red',
			'transparent',
			'#1234',
			'#12345678',
			'#abc; color:red',
			'url(https://x)',
			'var(--x)',
			'rgb(1,2,3)',
			'#fff\n',
			{ light: '#fff' },
			{ light: '#fff', dark: '#000', other: '#fff' },
			{ light: 'url(x)', dark: '#000' },
			Object.create({ light: '#fff', dark: '#000' }),
			null
		]) {
			expect(isColorValue(value), JSON.stringify(value)).toBe(false);
			expect(() => validateColor(value)).toThrow();
		}
		expect(() => validateAppearance({ style: 'background:url(x)' })).toThrow();
		expect(colorVariables('#fff', 'x;background:red')).toBe('');
		expect(appearanceStyle({ foreground: 'url(x)' })).not.toContain('url');
	});
	it('adjusts foreground on opaque light/dark surfaces with measurable contrast', () => {
		for (const background of ['#ffffff', '#17191f', '#ff0', '#111', '#f00', '#00ff00']) {
			for (const foreground of ['#eee', '#111', '#ff0', '#7c3aed', '#f00'])
				expect(
					contrastRatio(readableColor(foreground, background), background)
				).toBeGreaterThanOrEqual(4.5);
		}
		const vars = Object.fromEntries(
			appearanceStyle(
				{ foreground: '#eee', background: { light: '#fff', dark: '#000' } },
				{ surface: true }
			)
				.split(';')
				.filter(Boolean)
				.map((entry) => entry.split(':'))
		);
		for (const theme of ['light', 'dark'])
			expect(
				contrastRatio(vars[`--iui-fg-${theme}`], vars[`--iui-bg-${theme}`])
			).toBeGreaterThanOrEqual(4.5);
	});
	it('keeps categorical palette colors independent from view order', () => {
		const ids = ['North', 'South', 'West'];
		const colors = new Map(ids.map((id) => [id, chartColor(undefined, id, 'sunset')]));
		for (const id of ids.reverse())
			expect(chartColor(undefined, id, 'sunset')).toEqual(colors.get(id));
		expect(chartColor('#123456', 'anything')).toEqual(chartColor('#123456', 'other'));
	});
});
describe('colored native parser and keyed table precedence', () => {
	it('preserves valid color options through the real parser', () => {
		const parsed = parseIntelligentUI(JSON.stringify(tableSpec()), true);
		expect(parsed.error).toBeNull();
		expect(parsed.spec?.elements.table.props).toEqual(tableSpec().elements.table.props);
		for (const type of [
			'Stack',
			'Grid',
			'Tabs',
			'MetricCard',
			'ComparisonCards',
			'Explanation',
			'Select',
			'NumberInput',
			'ParameterSlider'
		]) {
			const props: Record<string, any> = {
				Stack: {},
				Grid: { columns: 2 },
				Tabs: { labels: [] },
				MetricCard: { title: 'Metric', value: 10 },
				ComparisonCards: { title: 'Compare', items: [] },
				Explanation: { title: 'Why', content: 'Text' },
				Select: {
					title: 'Plan',
					stateKey: 'plan',
					value: 'a',
					options: [{ label: 'A', value: 'a' }]
				},
				NumberInput: { title: 'Count', stateKey: 'n', min: 0, max: 10, step: 1, value: 1 },
				ParameterSlider: { title: 'Count', min: 0, max: 10, step: 1, value: 1 }
			};
			const spec = {
				root: 'x',
				elements: {
					x: node(type, {
						...props[type],
						appearance: { foreground: { light: '#123', dark: '#def' }, accent: '#bada55' }
					})
				}
			};
			expect(parseIntelligentUI(JSON.stringify(spec), true).error, type).toBeNull();
		}
	});
	it('resolves styles by unique row identity after sorting and searching, per channel', () => {
		const parsed = parseIntelligentUI(JSON.stringify(tableSpec()), true).spec!;
		const view = parsed.elements.table.props as any;
		const rows = tableRows(parsed, view, (_, v) => v, '', { field: 'cost', direction: 'asc' });
		expect(rows.map((row) => row.id)).toEqual(['b', 'a']);
		expect(tableCellAppearance(view, rows[0], view.columns[1])).toEqual({
			foreground: '#012',
			background: '#abc'
		});
		expect(tableCellAppearance(view, rows[0], view.columns[0])).toEqual({
			foreground: '#789',
			background: '#abc'
		});
		const search = tableRows(parsed, view, (_, v) => v, 'Beta');
		expect(tableCellAppearance(view, search[0], view.columns[1])).toEqual({
			foreground: '#012',
			background: '#abc'
		});
		expect(tableCellAppearance(view, rows[1], view.columns[1])).toEqual({
			foreground: '#456',
			background: '#fff'
		});
	});
	it('rejects missing/duplicate row IDs, unknown style targets, and unsupported CSS', () => {
		const mutations = [
			(s: any) => delete s.elements.table.props.rowKey,
			(s: any) => (s.datasets.plans[1].id = 'a'),
			(s: any) => (s.datasets.plans[1].id = null),
			(s: any) => s.elements.table.props.rowStyles.push({ id: 'b', appearance: {} }),
			(s: any) => (s.elements.table.props.rowStyles[0].id = 'unknown'),
			(s: any) => (s.elements.table.props.cellStyles[0].column = 'unknown'),
			(s: any) => (s.elements.table.props.cellStyles[0].rowId = 'unknown'),
			(s: any) => s.elements.table.props.cellStyles.push(s.elements.table.props.cellStyles[0]),
			(s: any) => (s.elements.table.props.appearance.foreground = 'url(x)'),
			(s: any) => (s.elements.table.props.columns[0].appearance = { color: '#fff' })
		];
		for (const mutate of mutations) {
			const spec = tableSpec();
			mutate(spec);
			expect(parseIntelligentUI(JSON.stringify(spec), true).error).not.toBeNull();
		}
	});
	it('accepts chart metadata but rejects duplicate IDs, invalid palettes and colors', () => {
		const spec = {
			root: 'chart',
			elements: {
				chart: node('Chart', {
					title: 'A',
					kind: 'bar',
					data: [{ id: 'alpha', label: 'A', value: 1, color: { light: '#c00', dark: '#faa' } }],
					categoryColors: [{ id: 'alpha', color: '#369' }],
					palette: 'ocean'
				})
			}
		};
		expect(parseIntelligentUI(JSON.stringify(spec), true).error).toBeNull();
		for (const mutate of [
			(s: any) => s.elements.chart.props.categoryColors.push({ id: 'alpha', color: '#fff' }),
			(s: any) => s.elements.chart.props.data.push({ ...s.elements.chart.props.data[0] }),
			(s: any) => (s.elements.chart.props.palette = 'unknown'),
			(s: any) => (s.elements.chart.props.data[0].color = '#fff;')
		]) {
			const bad = structuredClone(spec);
			mutate(bad);
			expect(parseIntelligentUI(JSON.stringify(bad), true).error).not.toBeNull();
		}
	});
});
