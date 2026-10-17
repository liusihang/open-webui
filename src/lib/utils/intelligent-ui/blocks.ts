/** Bounded contracts shared by the model-spec validator and isolated block renderers. */
export type AppBlockProps = {
	title: string;
	html: string;
	css?: string;
	js?: string;
	height?: number;
	stateKeys?: string[];
};
export type WritingBlockProps = { title: string; content: string; format?: 'text' | 'markdown' };
export type IsolatedBlockElement =
	| { type: 'AppBlock'; props: AppBlockProps; children: string[] }
	| { type: 'WritingBlock'; props: WritingBlockProps; children: string[] };
const keyPattern = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
const forbidden = new Set(['__proto__', 'constructor', 'prototype']);
export const isAppStateKey = (key: unknown): key is string =>
	typeof key === 'string' && keyPattern.test(key) && !forbidden.has(key);
export const isAppStateValue = (value: unknown): value is string | number | boolean =>
	typeof value === 'boolean' ||
	(typeof value === 'string' && value.length <= 256) ||
	(typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 1e12);

export function validateIsolatedBlock(
	type: 'AppBlock' | 'WritingBlock',
	input: unknown
): AppBlockProps | WritingBlockProps {
	if (!input || typeof input !== 'object' || Array.isArray(input))
		throw new Error('Invalid block props.');
	const p = input as Record<string, unknown>;
	const allowed =
		type === 'AppBlock'
			? ['title', 'html', 'css', 'js', 'height', 'stateKeys']
			: ['title', 'content', 'format'];
	if (Object.keys(p).some((key) => !allowed.includes(key))) throw new Error('Unknown block prop.');
	const text = (key: string, limit: number, required = true) => {
		if (!required && p[key] === undefined) return;
		if (typeof p[key] !== 'string' || (p[key] as string).length > limit)
			throw new Error(`Invalid ${key}.`);
	};
	text('title', 160);
	if (type === 'WritingBlock') {
		text('content', 16 * 1024);
		if (p.format !== undefined && p.format !== 'text' && p.format !== 'markdown')
			throw new Error('Invalid writing format.');
	} else {
		text('html', 24 * 1024);
		text('css', 8 * 1024, false);
		text('js', 16 * 1024, false);
		if (
			p.height !== undefined &&
			(typeof p.height !== 'number' ||
				!Number.isInteger(p.height) ||
				p.height < 160 ||
				p.height > 900)
		)
			throw new Error('Invalid app height.');
		if (
			p.stateKeys !== undefined &&
			(!Array.isArray(p.stateKeys) ||
				p.stateKeys.length > 16 ||
				!p.stateKeys.every(isAppStateKey) ||
				new Set(p.stateKeys).size !== p.stateKeys.length)
		)
			throw new Error('Invalid app state keys.');
	}
	return p as AppBlockProps | WritingBlockProps;
}

/** Append-only streams merge into local edits; revisions never silently overwrite a user's draft. */
export function mergeWritingStream(
	previous: string,
	incoming: string,
	draft: string
): { text: string; conflict: boolean } {
	if (draft === previous) return { text: incoming, conflict: false };
	if (incoming.startsWith(previous)) {
		const merged = draft + incoming.slice(previous.length);
		return merged.length <= 16384
			? { text: merged, conflict: false }
			: { text: draft, conflict: true };
	}
	return { text: draft, conflict: incoming !== previous };
}
