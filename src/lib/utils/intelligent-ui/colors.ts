/** Data-only color protocol. Never interpolate model-authored CSS, functions or URLs. */
export type ColorValue = string | { light: string; dark: string };
export type Palette = 'default' | 'ocean' | 'forest' | 'sunset';
export type Appearance = {
	foreground?: ColorValue;
	background?: ColorValue;
	border?: ColorValue;
	accent?: ColorValue;
	palette?: Palette;
};
export type RowStyle = { id: string; appearance: Appearance };
export type CellStyle = { rowId: string; column: string; appearance: Appearance };
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const object = (value: unknown): value is Record<string, unknown> =>
	value !== null && typeof value === 'object' && !Array.isArray(value);
const hex = (value: unknown): value is string =>
	typeof value === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value);
export const PALETTES: Record<Palette, string[]> = {
	default: ['#6366f1', '#059669', '#db2777', '#d97706', '#0284c7', '#9333ea', '#dc2626', '#0d9488'],
	ocean: ['#0284c7', '#0d9488', '#6366f1', '#0891b2', '#475569', '#7c3aed'],
	forest: ['#15803d', '#0f766e', '#65a30d', '#a16207', '#0891b2', '#7c3aed'],
	sunset: ['#c2410c', '#db2777', '#d97706', '#9333ea', '#b91c1c', '#0d9488']
};
export function isColorValue(value: unknown): value is ColorValue {
	return (
		hex(value) ||
		(object(value) &&
			Object.keys(value).length === 2 &&
			own(value, 'light') &&
			own(value, 'dark') &&
			hex(value.light) &&
			hex(value.dark))
	);
}
export function validateColor(value: unknown): ColorValue {
	if (!isColorValue(value)) throw new Error('Expected a hex color or a light/dark hex pair.');
	return typeof value === 'string' ? value : { light: value.light, dark: value.dark };
}
export function validatePalette(value: unknown): Palette {
	if (typeof value !== 'string' || !own(PALETTES, value)) throw new Error('Unknown UI palette.');
	return value as Palette;
}
export function validateAppearance(value: unknown): Appearance {
	if (
		!object(value) ||
		Object.keys(value).some(
			(key) => !['foreground', 'background', 'border', 'accent', 'palette'].includes(key)
		)
	)
		throw new Error('Invalid UI appearance.');
	const result: Appearance = {};
	for (const key of ['foreground', 'background', 'border', 'accent'] as const)
		if (own(value, key)) result[key] = validateColor(value[key]);
	if (own(value, 'palette')) result.palette = validatePalette(value.palette);
	return result;
}
function normalized(value: string): string {
	return value.length === 4
		? '#' + [...value.slice(1).toLowerCase()].map((part) => part + part).join('')
		: value.toLowerCase();
}
export function colorPair(value: ColorValue): { light: string; dark: string } {
	const safe = validateColor(value);
	return typeof safe === 'string'
		? { light: normalized(safe), dark: normalized(safe) }
		: { light: normalized(safe.light), dark: normalized(safe.dark) };
}
function channels(value: string): number[] {
	const text = normalized(value).slice(1);
	return [0, 2, 4].map((offset) => parseInt(text.slice(offset, offset + 2), 16));
}
function mix(first: string, second: string, fraction: number): string {
	const a = channels(first),
		b = channels(second);
	return (
		'#' +
		a
			.map((value, index) =>
				Math.round(value * (1 - fraction) + b[index] * fraction)
					.toString(16)
					.padStart(2, '0')
			)
			.join('')
	);
}
function luminance(color: string): number {
	const [r, g, b] = channels(color).map((channel) => {
		const value = channel / 255;
		return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
	});
	return r * 0.2126 + g * 0.7152 + b * 0.0722;
}
export function contrastRatio(first: string, second: string): number {
	const a = luminance(first),
		b = luminance(second);
	return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
/** Keep the requested hue when possible; move toward black/white on an opaque known surface. */
export function readableColor(foreground: string, background: string, minimum = 4.5): string {
	if (contrastRatio(foreground, background) >= minimum) return normalized(foreground);
	const target =
		contrastRatio('#000000', background) > contrastRatio('#ffffff', background)
			? '#000000'
			: '#ffffff';
	for (let step = 1; step <= 100; step++) {
		const candidate = mix(foreground, target, step / 100);
		if (contrastRatio(candidate, background) >= minimum) return candidate;
	}
	return target;
}
const themeSurface = { light: '#ffffff', dark: '#17191f' };
const themeInk = { light: '#1f2937', dark: '#e5e7eb' };
function safeAppearance(value?: Appearance): Appearance {
	try {
		return validateAppearance(value ?? {});
	} catch {
		return {};
	}
}
export function colorVariables(value: ColorValue, prefix: string): string {
	// Prefixes are developer-selected, but remain guarded at this boundary too.
	if (!/^[a-z][a-z0-9-]{0,40}$/.test(prefix) || !isColorValue(value)) return '';
	const pair = colorPair(value);
	return `--${prefix}-light:${pair.light};--${prefix}-dark:${pair.dark};`;
}
/** Locally scoped variables; no global theme mutation and no raw model-authored declaration. */
export function resolvedAppearance(
	value?: Appearance,
	options: { surface?: boolean; strong?: boolean } = {}
): Appearance & {
	foreground: ColorValue;
	background: ColorValue;
	border: ColorValue;
	accent: ColorValue;
} {
	const appearance = safeAppearance(value);
	const accent = colorPair(appearance.accent ?? PALETTES[appearance.palette ?? 'default'][0]);
	const background = appearance.background
		? colorPair(appearance.background)
		: options.strong
			? accent
			: options.surface
				? {
						light: mix(themeSurface.light, accent.light, 0.025),
						dark: mix(themeSurface.dark, accent.dark, 0.055)
					}
				: themeSurface;
	const requested = appearance.foreground ? colorPair(appearance.foreground) : themeInk;
	const foreground = {
		light: readableColor(requested.light, background.light),
		dark: readableColor(requested.dark, background.dark)
	};
	const border = appearance.border
		? colorPair(appearance.border)
		: {
				light: mix(background.light, accent.light, 0.2),
				dark: mix(background.dark, accent.dark, 0.28)
			};
	const readableAccent = {
		light: readableColor(accent.light, background.light),
		dark: readableColor(accent.dark, background.dark)
	};
	return {
		foreground,
		background,
		border,
		accent: readableAccent,
		...(appearance.palette ? { palette: appearance.palette } : {})
	};
}
export function appearanceStyle(
	value?: Appearance,
	options: { surface?: boolean; strong?: boolean } = {}
): string {
	const appearance = resolvedAppearance(value, options);
	return (
		colorVariables(appearance.foreground, 'iui-fg') +
		colorVariables(appearance.background, 'iui-bg') +
		colorVariables(appearance.border, 'iui-border') +
		colorVariables(appearance.accent, 'iui-accent')
	);
}

export function chartColor(
	value: unknown,
	identity: string,
	palette: Palette = 'default',
	background?: ColorValue
): ColorValue {
	let hash = 2166136261;
	for (const character of identity)
		hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
	const colors = own(PALETTES, palette) ? PALETTES[palette] : PALETTES.default;
	const pair = colorPair(isColorValue(value) ? value : colors[hash % colors.length]);
	const surface = isColorValue(background) ? colorPair(background) : themeSurface;
	return {
		light: readableColor(pair.light, surface.light, 3),
		dark: readableColor(pair.dark, surface.dark, 3)
	};
}
/** Channel-wise precedence: table < column < matching row < matching cell. IDs never use view indexes. */
export function tableCellAppearance(
	view: {
		appearance?: Appearance;
		rowKey?: string;
		rowStyles?: RowStyle[];
		cellStyles?: CellStyle[];
	},
	row: Record<string, unknown>,
	column: { key: string; appearance?: Appearance }
): Appearance {
	const id =
		view.rowKey && own(row, view.rowKey) && row[view.rowKey] != null
			? String(row[view.rowKey])
			: undefined;
	return {
		...safeAppearance(view.appearance),
		...safeAppearance(column.appearance),
		...safeAppearance(
			id === undefined ? undefined : view.rowStyles?.find((rule) => rule.id === id)?.appearance
		),
		...safeAppearance(
			id === undefined
				? undefined
				: view.cellStyles?.find((rule) => rule.rowId === id && rule.column === column.key)
						?.appearance
		)
	};
}
