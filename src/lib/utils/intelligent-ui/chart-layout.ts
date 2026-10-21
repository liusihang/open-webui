/** Chart coordinates stay in CSS pixels, so wider containers do not enlarge text. */
export const AXIS_FONT_SIZE = 12;
export const AXIS_LINE_HEIGHT = 16;

// Conservative system-font widths, including CJK and wide Latin capitals.
// Layout is deterministic during SSR and does not depend on canvas/font loading.
export function chartTextWidth(text: string): number {
	return [...text].reduce(
		(width, char) =>
			width +
			(/\p{Mark}/u.test(char) || char === '\u200d'
				? 0
				: /\s/u.test(char)
					? 4
					: /[^\u0000-\u00ff]/u.test(char)
						? 13
						: /[MW@%]/.test(char)
							? 12
							: /[A-Z0-9]/.test(char)
								? 9
								: 7),
		0
	);
}

/** Preserve every word/character; split long identifiers rather than adding ellipses. */
export function wrapChartLabel(text: string, width: number): string[] {
	const lines: string[] = [];
	let line = '';
	for (const word of text.trim().split(/\s+/u)) {
		if (line && chartTextWidth(`${line} ${word}`) <= width) {
			line += ` ${word}`;
			continue;
		}
		if (line) lines.push(line);
		line = '';
		for (const char of word) {
			if (line && chartTextWidth(line + char) > width) {
				lines.push(line);
				line = '';
			}
			line += char;
		}
	}
	if (line || !lines.length) lines.push(line);
	return lines;
}

export function categoricalChartLayout(labels: string[], viewportWidth = 600, rows: string[] = []) {
	const slot = 40;
	// End-anchored labels rotate -45 degrees. Reserve the left and bottom
	// projections too, so rotating complete text never moves it outside the SVG.
	const left = Math.ceil(
		Math.max(
			rows.length ? Math.min(180, Math.max(68, ...rows.map(chartTextWidth))) + 16 : 68,
			...labels.map(
				(label, index) => chartTextWidth(label) * Math.SQRT1_2 - (index + 0.5) * slot + 12
			)
		)
	);
	const width = Math.ceil(Math.max(480, viewportWidth || 600, left + labels.length * slot + 24));
	const plotWidth = width - left - 24;
	const columnWidth = plotWidth / Math.max(1, labels.length);
	const yLines = rows.map((label) => wrapChartLabel(label, left - 16));
	const rowHeight = Math.max(24, ...yLines.map((lines) => lines.length * AXIS_LINE_HEIGHT + 8));
	const plotHeight = Math.max(180, rows.length * rowHeight);
	const bottom = 25 + plotHeight;
	const height = Math.ceil(bottom + 36 + Math.max(0, ...labels.map(chartTextWidth)) * Math.SQRT1_2);
	return { width, height, left, bottom, plotWidth, plotHeight, columnWidth, yLines };
}
