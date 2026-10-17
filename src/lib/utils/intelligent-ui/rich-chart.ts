/** Trusted chart geometry. All callers are bounded again at this renderer boundary. */
export type RichPoint = { label: string; value: number };
export type RichSeries = { name: string; data: RichPoint[] };
export type RichSeriesField = { name: string; yField: string };
export const MAX_CHART_SERIES = 8;
export const MAX_CHART_POINTS = 128;
const finite = (value: unknown): value is number =>
	typeof value === 'number' && Number.isFinite(value);

export function normalizeRichSeries(
	data: RichPoint[],
	series?: RichSeries[],
	title = ''
): RichSeries[] {
	let remaining = MAX_CHART_POINTS;
	return (series?.length ? series : [{ name: title, data }])
		.slice(0, MAX_CHART_SERIES)
		.map((item) => {
			const points = (Array.isArray(item.data) ? item.data : [])
				.slice(0, MAX_CHART_POINTS)
				.filter((point) => point && typeof point.label === 'string' && finite(point.value))
				.slice(0, remaining)
				.map(({ label, value }) => ({ label, value }));
			remaining -= points.length;
			return { name: String(item.name).slice(0, 120), data: points };
		})
		.filter((series) => series.data.length > 0);
}

/** Pass already-filtered dataset rows; never resolves arbitrary fields through prototypes. */
export function resolveRichSeries(
	rows: Record<string, unknown>[],
	xField: string,
	fields: RichSeriesField[]
): RichSeries[] {
	const own = (row: object, field: string) => Object.prototype.hasOwnProperty.call(row, field);
	return normalizeRichSeries(
		[],
		fields.slice(0, MAX_CHART_SERIES).map(({ name, yField }) => ({
			name,
			data: rows.slice(0, 256).flatMap((row) => {
				const label = own(row, xField) ? row[xField] : undefined;
				const value = own(row, yField) ? row[yField] : undefined;
				return label !== undefined && label !== null && finite(value)
					? [{ label: String(label), value }]
					: [];
			})
		}))
	);
}

/** Normalizing before summing prevents overflow for finite values near Number.MAX_VALUE. */
export function pieSlices(data: RichPoint[]) {
	const points = data.slice(0, MAX_CHART_POINTS);
	if (!points.length || points.some((point) => !finite(point.value) || point.value < 0)) return [];
	const maximum = Math.max(...points.map((point) => point.value));
	if (maximum === 0) return [];
	const total = points.reduce((sum, point) => sum + point.value / maximum, 0);
	let angle = -Math.PI / 2;
	return points.map((point, index) => {
		const fraction = point.value / maximum / total;
		const start = angle;
		angle += fraction * Math.PI * 2;
		// Two arcs support the full-circle single-slice case as well as ordinary slices.
		const middle = start + (angle - start) / 2;
		const xy = (a: number) => `${300 + 92 * Math.cos(a)} ${115 + 92 * Math.sin(a)}`;
		return {
			...point,
			index,
			fraction,
			path: `M 300 115 L ${xy(start)} A 92 92 0 0 1 ${xy(middle)} A 92 92 0 0 1 ${xy(angle)} Z`
		};
	});
}

export function chartScale(series: RichSeries[]) {
	const values = series.flatMap((item) => item.data.map((point) => point.value));
	const magnitude = Math.max(1, ...values.map(Math.abs));
	const min = Math.min(0, ...values.map((value) => value / magnitude));
	const max = Math.max(0, ...values.map((value) => value / magnitude));
	return {
		min: min * magnitude,
		max: max * magnitude,
		y: (value: number) => 205 - ((value / magnitude - min) / (max - min || 1)) * 180
	};
}

/** Protocol extension guard. Reject unknown fields and ambiguous category alignment. */
export function validateRichChart(props: Record<string, unknown>): void {
	const fail = () => {
		throw new Error('Invalid chart series or pie options');
	};
	if (props.donut !== undefined && (props.kind !== 'pie' || typeof props.donut !== 'boolean'))
		fail();
	if (
		props.kind === 'pie' &&
		Array.isArray(props.data) &&
		props.data.some((point) => point?.value < 0)
	)
		fail();
	if (props.series === undefined) return;
	if (props.kind !== 'bar' && props.kind !== 'line') fail();
	if (
		!Array.isArray(props.series) ||
		props.series.length < 1 ||
		props.series.length > MAX_CHART_SERIES
	)
		fail();
	const names = new Set<string>();
	let total = 0;
	for (const item of props.series as unknown[]) {
		if (!item || typeof item !== 'object' || Array.isArray(item)) fail();
		const series = item as Record<string, unknown>;
		if (
			typeof series.name !== 'string' ||
			!series.name.trim() ||
			series.name.length > 120 ||
			names.has(series.name)
		)
			fail();
		names.add(series.name as string);
		if (props.dataSource !== undefined) {
			if (
				Object.keys(series).some((key) => !['name', 'yField'].includes(key)) ||
				typeof series.yField !== 'string' ||
				!series.yField ||
				series.yField.length > 120
			)
				fail();
		} else {
			if (
				Object.keys(series).some((key) => !['name', 'data'].includes(key)) ||
				!Array.isArray(series.data) ||
				series.data.length === 0
			)
				fail();
			const labels = new Set<string>();
			for (const point of series.data as unknown[]) {
				if (!point || typeof point !== 'object' || Array.isArray(point)) fail();
				const value = point as Record<string, unknown>;
				if (
					Object.keys(value).some((key) => !['label', 'value'].includes(key)) ||
					typeof value.label !== 'string' ||
					value.label.length > 120 ||
					labels.has(value.label) ||
					!finite(value.value)
				)
					fail();
				labels.add(value.label as string);
				if (++total > MAX_CHART_POINTS) fail();
			}
		}
	}
}
