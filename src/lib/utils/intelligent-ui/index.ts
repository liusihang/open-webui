/** The deliberately small, data-only subset accepted from model-generated UI fences. */
export type StackProps = { title?: string };
export type ChartProps = {
	title: string;
	description?: string;
	kind: 'bar' | 'line';
	data: { label: string; value: number }[];
	unit?: string;
};
export type ComparisonCardsProps = {
	title: string;
	items: {
		title: string;
		description: string;
		attributes?: { label: string; value: string }[];
	}[];
};
export type ExplanationProps = { title: string; content: string };
export type ParameterSliderProps = {
	title: string;
	min: number;
	max: number;
	step: number;
	value: number;
	unit?: string;
	followUp?: string;
};

type ElementOf<T extends string, P> = { type: T; props: P; children: string[] };
export type IntelligentUIElement =
	| ElementOf<'Stack', StackProps>
	| ElementOf<'Chart', ChartProps>
	| ElementOf<'ComparisonCards', ComparisonCardsProps>
	| ElementOf<'Explanation', ExplanationProps>
	| ElementOf<'ParameterSlider', ParameterSliderProps>;
export type Spec = { root: string; elements: Record<string, IntelligentUIElement> };
export type IntelligentUISpec = Spec;
export type ParseResult = {
	spec: Spec | null;
	error: string | null;
	pending: boolean;
	/** Plain text only. Render as text, never HTML or executable Markdown. */
	fallback: string;
};

export const INTELLIGENT_UI_LIMITS = Object.freeze({
	bytes: 128 * 1024,
	nodes: 64,
	depth: 8,
	operations: 512,
	chartPoints: 128,
	cards: 12,
	attributes: 16,
	title: 160,
	description: 2048,
	content: 16 * 1024,
	number: 1e12
});

const unsafeKeys = new Set(['__proto__', 'prototype', 'constructor']);
const own = (object: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(object, key);
const unavailable = 'This interactive view could not be displayed. Please use the text response.';

class InvalidUI extends Error {}

function requireValid(condition: unknown, message: string): asserts condition {
	if (!condition) throw new InvalidUI(message);
}

function isObject(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function fields(
	value: unknown,
	required: string[],
	optional: string[] = []
): Record<string, unknown> {
	requireValid(isObject(value), 'Expected a data object.');
	const allowed = new Set([...required, ...optional]);
	requireValid(
		Object.keys(value).every((key) => allowed.has(key)) && required.every((key) => own(value, key)),
		'Unsupported or missing UI fields.'
	);
	return value;
}

function safeId(value: unknown): asserts value is string {
	requireValid(
		typeof value === 'string' &&
			/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value) &&
			!unsafeKeys.has(value.toLowerCase()),
		'Invalid element identifier.'
	);
}

function string(
	value: unknown,
	max: number = INTELLIGENT_UI_LIMITS.title
): asserts value is string {
	requireValid(
		typeof value === 'string' &&
			value.length <= max &&
			!/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value),
		'Invalid or oversized text.'
	);
}

function optionalString(object: Record<string, unknown>, key: string, max?: number) {
	if (own(object, key)) string(object[key], max);
}

function number(value: unknown): asserts value is number {
	requireValid(
		typeof value === 'number' &&
			Number.isFinite(value) &&
			Math.abs(value) <= INTELLIGENT_UI_LIMITS.number,
		'Invalid or out-of-range number.'
	);
}

function array(value: unknown, max: number): asserts value is unknown[] {
	requireValid(Array.isArray(value) && value.length <= max, 'Invalid or oversized list.');
}

/** No prototype keys, executable directives, or dynamic bindings are part of this format. */
function inspectKeys(value: unknown, depth = 0) {
	requireValid(depth <= 16, 'UI data is nested too deeply.');
	if (Array.isArray(value)) {
		for (const item of value) inspectKeys(item, depth + 1);
	} else if (isObject(value)) {
		for (const [key, item] of Object.entries(value)) {
			requireValid(!unsafeKeys.has(key.toLowerCase()) && !key.startsWith('$'), 'Unsafe UI field.');
			inspectKeys(item, depth + 1);
		}
	}
}

function element(value: unknown): IntelligentUIElement {
	const node = fields(value, ['type', 'props', 'children']);
	array(node.children, INTELLIGENT_UI_LIMITS.nodes);
	node.children.forEach(safeId);
	requireValid(
		new Set(node.children).size === node.children.length,
		'Duplicate child identifiers.'
	);
	requireValid(
		node.type === 'Stack' || node.children.length === 0,
		'Only Stack may have children.'
	);
	let props: Record<string, unknown>;
	switch (node.type) {
		case 'Stack':
			props = fields(node.props, [], ['title']);
			optionalString(props, 'title');
			break;
		case 'Chart':
			props = fields(node.props, ['title', 'kind', 'data'], ['description', 'unit']);
			string(props.title);
			optionalString(props, 'description', INTELLIGENT_UI_LIMITS.description);
			optionalString(props, 'unit', 64);
			requireValid(props.kind === 'bar' || props.kind === 'line', 'Unsupported chart kind.');
			array(props.data, INTELLIGENT_UI_LIMITS.chartPoints);
			for (const entry of props.data) {
				const point = fields(entry, ['label', 'value']);
				string(point.label);
				number(point.value);
			}
			break;
		case 'ComparisonCards':
			props = fields(node.props, ['title', 'items']);
			string(props.title);
			array(props.items, INTELLIGENT_UI_LIMITS.cards);
			for (const entry of props.items) {
				const card = fields(entry, ['title', 'description'], ['attributes']);
				string(card.title);
				string(card.description, INTELLIGENT_UI_LIMITS.description);
				if (own(card, 'attributes')) {
					array(card.attributes, INTELLIGENT_UI_LIMITS.attributes);
					for (const entry of card.attributes) {
						const attribute = fields(entry, ['label', 'value']);
						string(attribute.label);
						string(attribute.value, 1024);
					}
				}
			}
			break;
		case 'Explanation':
			props = fields(node.props, ['title', 'content']);
			string(props.title);
			string(props.content, INTELLIGENT_UI_LIMITS.content);
			break;
		case 'ParameterSlider':
			props = fields(node.props, ['title', 'min', 'max', 'step', 'value'], ['unit', 'followUp']);
			string(props.title);
			for (const key of ['min', 'max', 'step', 'value']) number(props[key]);
			const { min, max, step, value: current } = props as ParameterSliderProps;
			requireValid(
				min < max &&
					step > 0 &&
					step <= max - min &&
					Number.isFinite((max - min) / step) &&
					current >= min &&
					current <= max,
				'Invalid slider range, step, or value.'
			);
			optionalString(props, 'unit', 64);
			optionalString(props, 'followUp', INTELLIGENT_UI_LIMITS.description);
			break;
		default:
			throw new InvalidUI('Unsupported UI component.');
	}
	// All nested fields have been checked, including their exact key sets.
	return node as IntelligentUIElement;
}

type DraftSpec = { root: string | null; elements: Record<string, IntelligentUIElement> };

function validateGraph(draft: DraftSpec, done: boolean) {
	const ids = Object.keys(draft.elements);
	requireValid(ids.length <= INTELLIGENT_UI_LIMITS.nodes, 'Too many UI elements.');
	const heights = new Map<string, number>();
	const visiting = new Set<string>();
	function height(id: string): number {
		if (!own(draft.elements, id)) {
			requireValid(!done, 'The finished UI references a missing element.');
			return 0;
		}
		requireValid(!visiting.has(id), 'UI elements cannot contain cycles.');
		if (heights.has(id)) return heights.get(id)!;
		visiting.add(id);
		const result = 1 + Math.max(0, ...draft.elements[id].children.map(height));
		visiting.delete(id);
		requireValid(result <= INTELLIGENT_UI_LIMITS.depth, 'The UI tree is too deep.');
		heights.set(id, result);
		return result;
	}
	for (const id of ids) height(id);
	if (done) {
		requireValid(draft.root !== null, 'The finished UI has no root.');
		requireValid(own(draft.elements, draft.root), 'The finished UI references a missing root.');
	}
}

/** A renderer never receives dangling child references or unrelated orphan elements. */
function reachableSpec(draft: DraftSpec): Spec | null {
	if (draft.root === null || !own(draft.elements, draft.root)) return null;
	const elements: Spec['elements'] = {};
	let renderedNodes = 0;
	function visit(id: string) {
		requireValid(
			++renderedNodes <= INTELLIGENT_UI_LIMITS.nodes,
			'The UI expands to too many elements.'
		);
		const node = draft.elements[id];
		const children = node.children.filter((child) => own(draft.elements, child));
		elements[id] = { ...node, children };
		// Count repeated subtrees too: a small acyclic graph can otherwise expand exponentially.
		children.forEach(visit);
	}
	visit(draft.root);
	return { root: draft.root, elements };
}

function apply(value: unknown, draft: DraftSpec) {
	inspectKeys(value);
	const patch = fields(value, ['op', 'path', 'value']);
	requireValid(patch.op === 'add' || patch.op === 'replace', 'Unsupported UI patch operation.');
	if (patch.path === '/root') {
		safeId(patch.value);
		draft.root = patch.value;
	} else {
		requireValid(typeof patch.path === 'string', 'Invalid UI patch path.');
		const match = /^\/elements\/([a-zA-Z][a-zA-Z0-9_-]{0,63})$/.exec(patch.path);
		requireValid(match !== null, 'Only whole root or element updates are supported.');
		const id = match[1];
		safeId(id);
		draft.elements[id] = element(patch.value);
	}
	validateGraph(draft, false);
}

function fullSpec(value: unknown): DraftSpec {
	inspectKeys(value);
	const spec = fields(value, ['root', 'elements']);
	safeId(spec.root);
	requireValid(isObject(spec.elements), 'Expected an element map.');
	requireValid(
		Object.keys(spec.elements).length <= INTELLIGENT_UI_LIMITS.nodes,
		'Too many UI elements.'
	);
	const elements: Spec['elements'] = {};
	for (const [id, value] of Object.entries(spec.elements)) {
		safeId(id);
		elements[id] = element(value);
	}
	return { root: spec.root, elements };
}

const incomplete = Symbol('incomplete');
const invalid = Symbol('invalid');
type JsonState = 'complete' | 'incomplete' | 'invalid';

/** Classify a JSON prefix without mistaking malformed final lines for unfinished streams. */
function jsonState(source: string): JsonState {
	let offset = 0;
	const space = () => {
		while (/[\t\n\r ]/.test(source[offset] ?? '') && offset < source.length) offset++;
	};
	const expected = (character: string) => {
		if (offset === source.length) throw incomplete;
		if (source[offset++] !== character) throw invalid;
	};
	function quoted() {
		const start = offset;
		expected('"');
		while (offset < source.length) {
			const char = source[offset++];
			if (char === '"') return JSON.parse(source.slice(start, offset)) as string;
			if (char.charCodeAt(0) < 32) throw invalid;
			if (char === '\\') {
				if (offset === source.length) throw incomplete;
				const escape = source[offset++];
				if (escape === 'u') {
					for (let digit = 0; digit < 4; digit++) {
						if (offset === source.length) throw incomplete;
						if (!/[0-9a-fA-F]/.test(source[offset++])) throw invalid;
					}
				} else if (!'"\\/bfnrt'.includes(escape)) throw invalid;
			}
		}
		throw incomplete;
	}
	function value(depth: number) {
		if (depth > 16) throw invalid;
		space();
		if (offset === source.length) throw incomplete;
		const char = source[offset];
		if (char === '"') return quoted();
		if (char === '{' || char === '[') {
			const object = char === '{';
			const close = object ? '}' : ']';
			const keys = new Set<string>();
			offset++;
			space();
			if (source[offset] === close) {
				offset++;
				return;
			}
			while (true) {
				if (object) {
					const key = quoted();
					if (keys.has(key) || unsafeKeys.has(key.toLowerCase()) || key.startsWith('$'))
						throw invalid;
					keys.add(key);
					space();
					expected(':');
				}
				value(depth + 1);
				space();
				if (source[offset] === close) {
					offset++;
					return;
				}
				expected(',');
				space();
			}
		}
		for (const literal of ['true', 'false', 'null']) {
			if (char === literal[0]) {
				for (const character of literal) expected(character);
				return;
			}
		}
		if (char === '-' || /[0-9]/.test(char)) {
			if (char === '-') {
				offset++;
				if (offset === source.length) throw incomplete;
			}
			if (source[offset] === '0') offset++;
			else {
				if (!/[1-9]/.test(source[offset] ?? '')) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			if (source[offset] === '.') {
				offset++;
				if (offset === source.length) throw incomplete;
				if (!/[0-9]/.test(source[offset])) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			if (source[offset] === 'e' || source[offset] === 'E') {
				offset++;
				if (source[offset] === '+' || source[offset] === '-') offset++;
				if (offset === source.length) throw incomplete;
				if (!/[0-9]/.test(source[offset])) throw invalid;
				while (/[0-9]/.test(source[offset] ?? '')) offset++;
			}
			return;
		}
		throw invalid;
	}
	try {
		value(0);
		space();
		return offset === source.length ? 'complete' : 'invalid';
	} catch (error) {
		return error === incomplete ? 'incomplete' : 'invalid';
	}
}

function fallbackText(spec: Spec | null): string {
	if (!spec) return '';
	const lines: string[] = [];
	function visit(id: string) {
		const node = spec!.elements[id];
		if (node.props.title) lines.push(node.props.title);
		switch (node.type) {
			case 'Chart':
				if (node.props.description) lines.push(node.props.description);
				for (const point of node.props.data) {
					lines.push(
						`${point.label}: ${point.value}${node.props.unit ? ` ${node.props.unit}` : ''}`
					);
				}
				break;
			case 'ComparisonCards':
				for (const card of node.props.items) {
					lines.push(card.title, card.description);
					for (const attribute of card.attributes ?? [])
						lines.push(`${attribute.label}: ${attribute.value}`);
				}
				break;
			case 'Explanation':
				lines.push(node.props.content);
				break;
			case 'ParameterSlider':
				lines.push(
					`${node.props.value}${node.props.unit ? ` ${node.props.unit}` : ''} (${node.props.min}–${node.props.max})`
				);
				break;
		}
		node.children.forEach(visit);
	}
	visit(spec.root);
	return lines.join('\n');
}

/**
 * Parse a fence body, or a single surrounding ```intelligent-ui fence.
 * This is a bounded patch dialect, not a general JSON Patch interpreter. Both add
 * and replace assign a whole root/element; no expressions or actions are evaluated.
 * Reparse the accumulated body on every update. Only an unfinished final JSONL
 * record may be buffered; malformed/unsupported data always fails closed.
 */
export function parseIntelligentUI(source: string, done: boolean): ParseResult {
	try {
		requireValid(
			source.length <= INTELLIGENT_UI_LIMITS.bytes &&
				new TextEncoder().encode(source).byteLength <= INTELLIGENT_UI_LIMITS.bytes,
			'The UI payload exceeds 128 KB.'
		);
		let body = source;
		const start = body.trimStart();
		if (
			!done &&
			start.startsWith('`') &&
			('```intelligent-ui'.startsWith(start) || /^```intelligent-ui[\t ]*\r?$/.test(start))
		) {
			return { spec: null, error: null, pending: true, fallback: '' };
		}
		if (/^\s*```/.test(body)) {
			const opening = /^\s*```intelligent-ui[\t ]*\r?\n/.exec(body);
			requireValid(opening !== null, 'Expected an intelligent-ui fence.');
			body = body.slice(opening[0].length);
			const closing = /(?:^|\r?\n)```[\t ]*(?:\r?\n)?[\t\r\n ]*$/.exec(body);
			if (closing) body = body.slice(0, closing.index);
			else requireValid(!done, 'The UI fence is not closed.');
		}
		// Marked includes one/two arriving closing backticks in an open code token.
		// Buffer that framing only while streaming; a finished stray fence still fails.
		if (!done) body = body.replace(/(?:^|\r?\n)`{1,2}$/, '');
		if (!body.trim()) {
			requireValid(!done, 'The UI payload is empty.');
			return { spec: null, error: null, pending: true, fallback: '' };
		}

		let draft: DraftSpec = { root: null, elements: {} };
		const state = jsonState(body);
		if (state === 'complete') {
			const value: unknown = JSON.parse(body);
			if (isObject(value) && own(value, 'op')) apply(value, draft);
			else draft = fullSpec(value);
		} else if (/^\s*\{(?:[\t ]*\r?\n|\s*"(?:root|elements)"\s*:|\s*$)/.test(body)) {
			// A pretty-printed full-spec fallback must arrive as one complete object.
			requireValid(state === 'incomplete' && !done, 'Invalid or incomplete UI JSON.');
			return { spec: null, error: null, pending: true, fallback: '' };
		} else {
			const lines = body.split('\n');
			let operations = 0;
			for (let index = 0; index < lines.length; index++) {
				const line = lines[index].trim();
				if (!line) continue;
				const state = jsonState(line);
				if (state === 'incomplete' && index === lines.length - 1 && !done) break;
				requireValid(state === 'complete', 'Invalid or incomplete UI JSONL record.');
				requireValid(++operations <= INTELLIGENT_UI_LIMITS.operations, 'Too many UI updates.');
				apply(JSON.parse(line), draft);
			}
		}
		validateGraph(draft, done);
		const spec = reachableSpec(draft);
		return { spec, error: null, pending: !done, fallback: fallbackText(spec) };
	} catch (error) {
		return {
			spec: null,
			error: error instanceof InvalidUI ? error.message : 'Invalid UI data.',
			pending: false,
			fallback: unavailable
		};
	}
}

/** Stable non-cryptographic key for view-local state; never use as a security token. */
export function intelligentUIHash(source: string): string {
	let hash = 2166136261;
	for (let index = 0; index < source.length; index++) {
		hash ^= source.charCodeAt(index);
		hash = Math.imul(hash, 16777619);
	}
	return (hash >>> 0).toString(36);
}
