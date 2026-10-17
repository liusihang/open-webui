/** Bounded data-only presentation and form components. No model-authored executable code. */
export type Primitive = string | number | boolean;
export type Action =
	| { type: 'set'; stateKey: string; value: Primitive }
	| { type: 'increment'; stateKey: string; amount: number }
	| { type: 'toggle'; stateKey: string }
	| { type: 'copy-state'; stateKey: string; from: string }
	| { type: 'copy'; keys: string[] };
export type ImageProps = { src: string; alt: string; caption?: string; source?: string };
export type ChoiceProps = {
	title: string;
	stateKey: string;
	value: string;
	options: { label: string; value: string }[];
};
export type ExtendedProps = {
	Card: { title?: string; description?: string };
	Carousel: { title?: string };
	Flow: { title?: string; steps: { title: string; description?: string }[] };
	Popover: { label: string; title?: string };
	SVG: { svg: string; label: string };
	Icon: { name: 'info' | 'check' | 'warning' | 'star' | 'arrow-right' | 'heart'; label: string };
	Math: { expression: string; display: boolean };
	Image: ImageProps;
	ImageGallery: { title?: string; images: ImageProps[] };
	Input: {
		title: string;
		stateKey: string;
		value: string;
		placeholder?: string;
		maxLength: number;
	};
	Textarea: {
		title: string;
		stateKey: string;
		value: string;
		placeholder?: string;
		maxLength: number;
	};
	Checkbox: { title: string; stateKey: string; value: boolean };
	RadioGroup: ChoiceProps;
	SegmentedControl: ChoiceProps;
	DatePicker: { title: string; stateKey: string; value: string; min?: string; max?: string };
	Button: { label: string; actions: Action[] };
	Pressable: { label: string; actions: Action[] };
	Text: { text: string; bindings: { token: string; stateKey: string }[] };
	Conditional: { stateKey: string; equals: Primitive };
};
export type ExtendedType = keyof ExtendedProps;
export type ExtendedElement = {
	[K in ExtendedType]: { type: K; props: ExtendedProps[K]; children: string[] };
}[ExtendedType];
export const EXTENDED_TYPES = [
	'Card',
	'Carousel',
	'Flow',
	'Popover',
	'SVG',
	'Icon',
	'Math',
	'Image',
	'ImageGallery',
	'Input',
	'Textarea',
	'Checkbox',
	'RadioGroup',
	'DatePicker',
	'SegmentedControl',
	'Button',
	'Pressable',
	'Text',
	'Conditional'
] as const;
const fail = (): never => {
	throw new Error('Invalid extended UI component');
};
const obj = (v: unknown): Record<string, any> =>
	v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, any>) : fail();
const fields = (p: Record<string, any>, keys: string[]) => {
	if (Object.keys(p).some((k) => !keys.includes(k))) fail();
};
const str = (v: unknown, n = 512): string => (typeof v === 'string' && v.length <= n ? v : fail());
const required = (v: unknown, n = 512): string => {
	const s = str(v, n);
	return s.trim() ? s : fail();
};
const key = (v: unknown): string => {
	const s = required(v, 64);
	return /^[A-Za-z][A-Za-z0-9_-]*$/.test(s) &&
		!['constructor', 'prototype', '__proto__'].includes(s.toLowerCase())
		? s
		: fail();
};
const primitive = (v: unknown): Primitive =>
	typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e12)
		? v
		: str(v, 256);
const array = (v: unknown, max: number): any[] =>
	Array.isArray(v) && v.length > 0 && v.length <= max ? v : fail();
const optional = (p: Record<string, any>, k: string, max = 512) =>
	p[k] === undefined ? {} : { [k]: str(p[k], max) };
export function safeImageUrl(input: unknown): string {
	const raw = required(input, 2048);
	let u: URL;
	try {
		u = new URL(raw);
	} catch {
		return fail();
	}
	if (u.protocol !== 'https:' || u.username || u.password || (u.port && u.port !== '443'))
		return fail();
	const host = u.hostname.toLowerCase();
	if (
		host === 'localhost' ||
		!host.includes('.') ||
		host.endsWith('.localhost') ||
		host.endsWith('.local') ||
		host.endsWith('.internal') ||
		/^\d+\.\d+\.\d+\.\d+$/.test(host) ||
		host.includes(':')
	)
		return fail();
	return u.href;
}
const image = (v: unknown): ImageProps => {
	const p = obj(v);
	fields(p, ['src', 'alt', 'caption', 'source']);
	return {
		src: safeImageUrl(p.src),
		alt: str(p.alt, 512),
		...optional(p, 'caption'),
		...(p.source === undefined ? {} : { source: safeImageUrl(p.source) })
	};
};
const date = (v: unknown): string => {
	const s = str(v, 10);
	if (s === '') return s;
	if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || new Date(s + 'T00:00:00Z').toISOString().slice(0, 10) !== s)
		fail();
	return s;
};
/** Strict SVG subset; reject rather than trying to repair active or ambiguous markup. */
export function sanitizeSvg(input: unknown): string {
	const source = required(input, 16000);
	if (/<!|<\?|&|[\u0000-\u0008]/.test(source)) fail();
	const tags = new Set([
		'svg',
		'g',
		'path',
		'rect',
		'circle',
		'ellipse',
		'line',
		'polyline',
		'polygon',
		'text',
		'title',
		'desc'
	]);
	const attrs = new Set([
		'xmlns',
		'viewBox',
		'width',
		'height',
		'x',
		'y',
		'x1',
		'x2',
		'y1',
		'y2',
		'cx',
		'cy',
		'r',
		'rx',
		'ry',
		'd',
		'points',
		'fill',
		'stroke',
		'stroke-width',
		'stroke-linecap',
		'stroke-linejoin',
		'opacity',
		'fill-opacity',
		'stroke-opacity',
		'transform',
		'text-anchor',
		'font-size'
	]);
	const stack: string[] = [];
	let count = 0;
	let root = false;
	let end = 0;
	const tokens = source.matchAll(/<[^>]*>/g);
	for (const match of tokens) {
		const between = source.slice(end, match.index);
		if (
			between.includes('<') ||
			between.includes('>') ||
			(between.trim() && !['text', 'title', 'desc'].includes(stack.at(-1) ?? ''))
		)
			fail();
		const token = match[0];
		end = match.index! + token.length;
		const m = /^<(\/?)\s*([A-Za-z]+)([\s\S]*?)(\/?)>$/.exec(token);
		if (!m) fail();
		const [, closing, tag, rest, self] = m!;
		if (!tags.has(tag) || ++count > 256) fail();
		if (closing) {
			if (rest.trim() || self || stack.pop() !== tag) fail();
			continue;
		}
		if (!stack.length) {
			if (root || tag !== 'svg') fail();
			root = true;
		}
		if (stack.length > 12 || (tag === 'svg' && stack.length)) fail();
		let pos = 0;
		const seen = new Set<string>();
		const re = /\s+([A-Za-z][A-Za-z-]*)\s*=\s*"([^"<>]*)"/g;
		let a: RegExpExecArray | null;
		while ((a = re.exec(rest))) {
			if (rest.slice(pos, a.index).trim()) fail();
			pos = re.lastIndex;
			const [, name, value] = a;
			if (!attrs.has(name) || seen.has(name)) fail();
			seen.add(name);
			if (name === 'xmlns') {
				if (value !== 'http://www.w3.org/2000/svg') fail();
			} else if (name === 'fill' || name === 'stroke') {
				if (!/^(none|currentColor|transparent|#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,24})$/.test(value))
					fail();
			} else if (name === 'stroke-linecap') {
				if (!/^(butt|round|square)$/.test(value)) fail();
			} else if (name === 'stroke-linejoin') {
				if (!/^(miter|round|bevel)$/.test(value)) fail();
			} else if (name === 'text-anchor') {
				if (!/^(start|middle|end)$/.test(value)) fail();
			} else if (name === 'transform') {
				if (!/^(?:(?:translate|scale|rotate|matrix)\([0-9eE.,+\-\s]+\)\s*)+$/.test(value)) fail();
			} else if (!/^[0-9eE.,+\-\sMmZzLlHhVvCcSsQqTtAa]*$/.test(value) || value.length > 8000)
				fail();
		}
		if (rest.slice(pos).trim()) fail();
		if (!self) stack.push(tag);
	}
	if (!root || stack.length || source.slice(end).trim()) fail();
	return source;
}
export function validateExtendedElement(input: unknown): ExtendedElement {
	const e = obj(input);
	fields(e, ['type', 'props', 'children']);
	if (!EXTENDED_TYPES.includes(e.type)) fail();
	const p = obj(e.props ?? {});
	const children =
		e.children === undefined
			? []
			: Array.isArray(e.children) && e.children.length <= 32
				? e.children.map(key)
				: fail();
	if (new Set(children).size !== children.length) fail();
	let result: Record<string, any> = {};
	switch (e.type as ExtendedType) {
		case 'Card':
			fields(p, ['title', 'description']);
			result = { ...optional(p, 'title'), ...optional(p, 'description', 2000) };
			break;
		case 'Carousel':
			fields(p, ['title']);
			if (!children.length) fail();
			result = optional(p, 'title');
			break;
		case 'Flow':
			fields(p, ['title', 'steps']);
			result = {
				...optional(p, 'title'),
				steps: array(p.steps, 16).map((v) => {
					const s = obj(v);
					fields(s, ['title', 'description']);
					return { title: required(s.title), ...optional(s, 'description', 2000) };
				})
			};
			break;
		case 'Popover':
			fields(p, ['label', 'title']);
			result = { label: required(p.label), ...optional(p, 'title') };
			break;
		case 'SVG':
			fields(p, ['svg', 'label']);
			result = { svg: sanitizeSvg(p.svg), label: required(p.label) };
			break;
		case 'Icon':
			fields(p, ['name', 'label']);
			if (!['info', 'check', 'warning', 'star', 'arrow-right', 'heart'].includes(p.name)) fail();
			result = { name: p.name, label: required(p.label) };
			break;
		case 'Math':
			fields(p, ['expression', 'display']);
			if (p.display !== undefined && typeof p.display !== 'boolean') fail();
			result = { expression: required(p.expression, 4000), display: p.display ?? true };
			break;
		case 'Image':
			result = image(p);
			break;
		case 'ImageGallery':
			fields(p, ['title', 'images']);
			result = { ...optional(p, 'title'), images: array(p.images, 12).map(image) };
			break;
		case 'Input':
		case 'Textarea': {
			fields(p, ['title', 'stateKey', 'value', 'placeholder', 'maxLength']);
			const max = p.maxLength ?? 256;
			if (!Number.isInteger(max) || max < 1 || max > 256) fail();
			result = {
				title: required(p.title),
				stateKey: key(p.stateKey),
				value:
					e.type === 'Input' && /[\r\n\t]/.test(p.value ?? '') ? fail() : str(p.value ?? '', max),
				maxLength: max,
				...optional(p, 'placeholder')
			};
			break;
		}
		case 'Checkbox':
			fields(p, ['title', 'stateKey', 'value']);
			if (typeof p.value !== 'boolean') fail();
			result = { title: required(p.title), stateKey: key(p.stateKey), value: p.value };
			break;
		case 'RadioGroup':
		case 'SegmentedControl': {
			fields(p, ['title', 'stateKey', 'value', 'options']);
			const options = array(p.options, 32).map((v) => {
				const o = obj(v);
				fields(o, ['label', 'value']);
				return { label: required(o.label), value: str(o.value, 256) };
			});
			if (
				new Set(options.map((o) => o.value)).size !== options.length ||
				!options.some((o) => o.value === p.value)
			)
				fail();
			result = { title: required(p.title), stateKey: key(p.stateKey), value: p.value, options };
			break;
		}
		case 'DatePicker': {
			fields(p, ['title', 'stateKey', 'value', 'min', 'max']);
			const value = date(p.value ?? '');
			const min = p.min === undefined ? undefined : date(p.min);
			const max = p.max === undefined ? undefined : date(p.max);
			if ((min && max && min > max) || (value && ((min && value < min) || (max && value > max))))
				fail();
			result = {
				title: required(p.title),
				stateKey: key(p.stateKey),
				value,
				...(min ? { min } : {}),
				...(max ? { max } : {})
			};
			break;
		}
		case 'Button':
		case 'Pressable':
			fields(p, ['label', 'actions']);
			result = {
				label: required(p.label),
				actions: array(p.actions, 8).map((v) => {
					const a = obj(v);
					if (a.type === 'copy') {
						fields(a, ['type', 'keys']);
						return { type: 'copy', keys: array(a.keys, 16).map(key) };
					}
					const stateKey = key(a.stateKey);
					switch (a.type) {
						case 'set':
							fields(a, ['type', 'stateKey', 'value']);
							return { type: a.type, stateKey, value: primitive(a.value) };
						case 'increment':
							fields(a, ['type', 'stateKey', 'amount']);
							if (
								typeof a.amount !== 'number' ||
								!Number.isFinite(a.amount) ||
								Math.abs(a.amount) > 1e6
							)
								fail();
							return { type: a.type, stateKey, amount: a.amount };
						case 'toggle':
							fields(a, ['type', 'stateKey']);
							return { type: a.type, stateKey };
						case 'copy-state':
							fields(a, ['type', 'stateKey', 'from']);
							return { type: a.type, stateKey, from: key(a.from) };
						default:
							return fail();
					}
				})
			};
			break;
		case 'Text':
			fields(p, ['text', 'bindings']);
			result = {
				text: str(p.text, 8000),
				bindings:
					p.bindings === undefined
						? []
						: Array.isArray(p.bindings) && p.bindings.length <= 16
							? p.bindings.map((v: unknown) => {
									const b = obj(v);
									fields(b, ['token', 'stateKey']);
									return { token: key(b.token), stateKey: key(b.stateKey) };
								})
							: fail()
			};
			break;
		case 'Conditional':
			fields(p, ['stateKey', 'equals']);
			if (children.length < 1 || children.length > 2) fail();
			result = { stateKey: key(p.stateKey), equals: primitive(p.equals) };
			break;
	}
	if (
		e.type === 'Text' &&
		new Set(result.bindings.map((b: { token: string }) => b.token)).size !== result.bindings.length
	)
		fail();
	if (
		!['Card', 'Carousel', 'Popover', 'Conditional', 'Pressable'].includes(e.type) &&
		children.length
	)
		fail();
	return { type: e.type, props: result, children } as ExtendedElement;
}
export function extendedSharedValue(
	element: { type: string; props: any },
	stateKey: string,
	read: (key: string, fallback: Primitive) => Primitive
): Primitive | undefined {
	const p = element.props;
	if (!EXTENDED_CONTROL_TYPES.includes(element.type) || p.stateKey !== stateKey) return undefined;
	const saved = read(`shared:${stateKey}`, p.value);
	switch (element.type) {
		case 'Input':
		case 'Textarea':
			return typeof saved === 'string' &&
				!/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(saved) &&
				!(element.type === 'Input' && /[\r\n\t]/.test(saved))
				? saved.slice(0, p.maxLength)
				: p.value;
		case 'Checkbox':
			return typeof saved === 'boolean' ? saved : p.value;
		case 'RadioGroup':
		case 'SegmentedControl':
			return p.options.some((o: { value: string }) => o.value === saved) ? saved : p.value;
		case 'DatePicker':
			try {
				const d = date(saved);
				return d && ((p.min && d < p.min) || (p.max && d > p.max)) ? p.value : d;
			} catch {
				return p.value;
			}
		default:
			return undefined;
	}
}
export function runActions(
	actions: Action[],
	read: (key: string) => Primitive | undefined,
	write: (key: string, value: Primitive) => void
): void {
	for (const action of actions.slice(0, 8)) {
		if (action.type === 'copy') continue;
		const current = read(action.stateKey);
		if (current === undefined) continue;
		if (action.type === 'set') write(action.stateKey, action.value);
		else if (action.type === 'toggle' && typeof current === 'boolean')
			write(action.stateKey, !current);
		else if (action.type === 'increment' && typeof current === 'number') {
			const next = current + action.amount;
			if (Number.isFinite(next) && Math.abs(next) <= 1e12) write(action.stateKey, next);
		} else if (action.type === 'copy-state') {
			const value = read(action.from);
			if (value !== undefined) write(action.stateKey, value);
		}
	}
}
export function boundText(
	text: string,
	bindings: { token: string; stateKey: string }[],
	read: (key: string) => Primitive | undefined
): string {
	const values = new Map(bindings.map((b) => [b.token, read(b.stateKey)]));
	return text.replace(/\{\{([A-Za-z][A-Za-z0-9_-]*)\}\}/g, (match, token) =>
		values.has(token) ? String(values.get(token) ?? '') : match
	);
}

export const EXTENDED_CONTROL_TYPES: readonly string[] = [
	'Input',
	'Textarea',
	'Checkbox',
	'RadioGroup',
	'SegmentedControl',
	'DatePicker'
];
/** References must resolve to a declared control during whole-spec validation. */
export function extendedStateReferences(element: { type: string; props: any }): string[] {
	const p = element.props;
	if (element.type === 'Text') return p.bindings.map((b: { stateKey: string }) => b.stateKey);
	if (element.type === 'Conditional') return [p.stateKey];
	if (element.type === 'Button' || element.type === 'Pressable')
		return p.actions.flatMap((a: Action) =>
			a.type === 'copy' ? a.keys : a.type === 'copy-state' ? [a.stateKey, a.from] : [a.stateKey]
		);
	return [];
}

/** Prevent nested interactive content inside the accessible Pressable button surface. */
export function validatePressableChildren(
	elements: Record<string, { type: string; props: any; children?: string[] }>
): void {
	const staticTypes = new Set([
		'Stack',
		'Grid',
		'Card',
		'Flow',
		'SVG',
		'Icon',
		'Math',
		'Text',
		'Explanation',
		'ComparisonCards',
		'MetricCard',
		'Image',
		'ImageGallery',
		'Conditional'
	]);
	for (const root of Object.values(elements)) {
		if (root.type !== 'Pressable') continue;
		const visited = new Set<string>();
		const inspect = (id: string, depth: number) => {
			if (depth > 8 || visited.size >= 64 || visited.has(id)) fail();
			visited.add(id);
			const child = elements[id];
			if (!child || !staticTypes.has(child.type)) fail();
			if (child.type === 'Image' && child.props.source) fail();
			if (
				child.type === 'ImageGallery' &&
				child.props.images.some((image: ImageProps) => image.source)
			)
				fail();
			for (const nested of child.children ?? []) inspect(nested, depth + 1);
		};
		for (const child of root.children ?? []) inspect(child, 1);
	}
}
