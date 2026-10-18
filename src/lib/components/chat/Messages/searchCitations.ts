import { Lexer, Marked } from 'marked';
import markedExtension from '$lib/utils/marked/extension';
import colonFenceExtension from '$lib/utils/marked/colon-fence-extension';
import citationExtension from '$lib/utils/marked/citation-extension';

/** Read-only compatibility for Alpha tokens saved by the search_web tool.
 * Full refs are local to this message's matched tool outputs, never source indices.
 */
type Source = { url: string; title: string };
const refPattern = /^turn\d+[A-Za-z]+\d+$/;
const privateTokenPattern = /[^\n]*(?:|(?=\n|$))|【turn\d+[A-Za-z]+\d+】/g;
// Isolated parser: never register compatibility behavior on marked's global instance.
// These are the message renderer's Markdown containers; their nested code is parsed too.
const codeLexer = new Marked(markedExtension(), colonFenceExtension(), citationExtension());
const markdownOccurrences = (text: string, pattern: RegExp, preserveDefinitions = false) => {
	const matches = [...text.matchAll(pattern)];
	// Occurrence markers survive marked's list/blockquote indentation normalization.
	// Keep the original Markdown, including all delimiters, when identifying code.
	let prefix = '\uE100';
	while (text.includes(prefix)) prefix += '\uE100';
	const markerPattern = new RegExp(`${prefix}(\\d+)\uE101`, 'g');
	let index = 0;
	const annotated = text.replace(pattern, (raw) => `${prefix}${index++}\uE101${raw}`);
	// A marker before a numeric reference definition would hide its block syntax.
	// Seed the original definition table before the annotated inline parse.
	const definitions = preserveDefinitions ? codeLexer.lexer(text).links : undefined;
	const lexer = new Lexer(codeLexer.defaults);
	if (definitions) lexer.tokens.links = definitions;
	return { matches, markerPattern, tokens: lexer.lex(annotated) };
};
const outsideCode = (text: string, change: (text: string) => string) => {
	if (!/[【]/.test(text)) return change(text);
	const { matches, markerPattern, tokens } = markdownOccurrences(text, privateTokenPattern);
	const protectedIndices = new Set<number>();
	let inRawBlock = false;
	codeLexer.walkTokens(tokens, (token) => {
		if (token.type === 'html' && 'inRawBlock' in token) inRawBlock = token.inRawBlock;
		if (['code', 'codespan', 'html'].includes(token.type) || inRawBlock) {
			for (const marker of token.raw.matchAll(markerPattern)) protectedIndices.add(+marker[1]);
		}
	});
	// Change only original slices. Never serialize parsed code back into the message.
	let result = '';
	let start = 0;
	for (const [i, match] of matches.entries()) {
		if (!protectedIndices.has(i)) continue;
		result += change(text.slice(start, match.index)) + match[0];
		start = match.index! + match[0].length;
	}
	return result + change(text.slice(start));
};

/** Honor the citations-disabled capability without deleting numeric Markdown links or code. */
export function removeNativeCitations(text: string): string {
	const pattern = /(\[(?:\d+(?:#[^,\]\s]+)?(?:,\s*\d+(?:#[^,\]\s]+)?)*)\])+/g;
	if (!/\[\d/.test(text)) return text;
	const { matches, markerPattern, tokens } = markdownOccurrences(text, pattern, true);
	const removals: { start: number; length: number }[] = [];
	let precedingMarker: number | undefined;
	let inRawBlock = false;
	codeLexer.walkTokens(tokens, (token) => {
		if (token.type === 'html' && 'inRawBlock' in token) inRawBlock = token.inRawBlock;
		if (token.type === 'citation' && precedingMarker !== undefined && !inRawBlock) {
			removals.push({ start: matches[precedingMarker].index!, length: token.raw.length });
		}
		const markers = [...token.raw.matchAll(markerPattern)];
		const last = markers.at(-1);
		precedingMarker =
			token.type === 'text' && last && last.index! + last[0].length === token.raw.length
				? +last[1]
				: undefined;
	});
	for (const { start, length } of removals.sort((a, b) => b.start - a.start)) {
		text = text.slice(0, start) + text.slice(start + length);
	}
	return text;
}

export function createSearchCitationFormatter(output: unknown[]) {
	const references = new Map<string, Source>();
	const conflicts = new Set<string>();
	const calls = new Set(
		(output ?? [])
			.filter((item: any) => item?.type === 'function_call' && item.name === 'search_web')
			.map((item: any) => item.call_id)
			.filter(Boolean)
	);
	const add = (ref: unknown, url: unknown, title: unknown) => {
		if (typeof ref !== 'string' || !refPattern.test(ref) || typeof url !== 'string') return;
		let href: string;
		try {
			const parsed = new URL(url);
			if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password)
				return;
			href = parsed.href;
		} catch {
			return;
		}
		if (references.has(ref) && references.get(ref)?.url !== href) conflicts.add(ref);
		else
			references.set(ref, {
				url: href,
				title: typeof title === 'string' && title ? title.slice(0, 300) : url
			});
	};
	for (const item of output ?? []) {
		const result = item as any;
		if (result?.type !== 'function_call_output' || !calls.has(result.call_id)) continue;
		let value = result.output;
		if (Array.isArray(value))
			value = value
				.filter((b: any) => ['input_text', 'text'].includes(b?.type))
				.map((b: any) => b.text)
				.join('');
		if (typeof value === 'string') {
			try {
				value = JSON.parse(value);
			} catch {
				continue;
			}
		}
		if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
		for (const key of ['results', 'citation_sources', 'sources', 'citations']) {
			for (const row of Array.isArray(value[key]) ? value[key] : []) {
				if (!row || typeof row !== 'object') continue;
				const source = row.source && typeof row.source === 'object' ? row.source : row;
				add(row.ref_id ?? source.ref_id, source.url, source.title ?? source.name);
			}
		}
		if (typeof value.content === 'string')
			outsideCode(value.content, (segment) => {
				for (const match of segment.matchAll(
					/(?:^|\n)([^\n]*?) \((https?:\/\/[^\s]+)\)\n(?:cite|【)(turn\d+[A-Za-z]+\d+)(?:|】)/g
				))
					add(match[3], match[2], match[1]);
				return segment;
			});
	}
	return (text: string): string => {
		if (typeof text !== 'string' || !text.includes('')) return text;
		return outsideCode(text, (segment) =>
			segment.replace(/[^\n]*(?:|(?=\n|$))/g, (token) => {
				const parts = token.replace(/^|$/g, '').split('').slice(1);
				if (!parts.length) return '（上游显示内容不可用）';
				return [...new Set(parts)]
					.map((part) => {
						const [ref, ...label] = part.split('†');
						const source = references.get(ref);
						if (source && !conflicts.has(ref)) {
							// A source label is literal text, not nested Markdown. Newlines
							// and pipes must not create rows/columns when used in a table.
							const title = source.title
								.replace(/\s+/g, ' ')
								.replace(/[\\`*{}\[\]()#+.!_|>~<&]/g, '\\$&');
							const url = source.url.replace(/[\s|\\()<>"`\[\]]/g, (char) =>
								encodeURIComponent(char).replace(
									/[!'()*]/g,
									(c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`
								)
							);
							return `[${title}](${url})`;
						}
						if (/^\d+$/.test(ref) && label.length) return `${label.join('†')} (link id: ${ref})`;
						if (/^turn\d+(?:finance|forecast|sports|time)\d+$/.test(ref))
							return `（上游数据引用：${ref}；未提供来源链接）`;
						return `（来源未解析：${ref.slice(0, 100)}）`;
					})
					.join(' ');
			})
		);
	};
}
