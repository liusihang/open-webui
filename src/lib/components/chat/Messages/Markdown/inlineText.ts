import type { Token } from 'marked';

/** Plain inline text for table export; native references retain their exact marker.
 * A citation number alone is not enough evidence to invent a source URL.
 */
export function inlineText(tokens: Token[] = []): string {
	return tokens
		.map((token) => {
			if (token.type === 'citation') return token.raw;
			if (token.type === 'br') return '\n';
			if ('tokens' in token && Array.isArray(token.tokens)) return inlineText(token.tokens);
			return ('text' in token ? token.text : undefined) ?? token.raw ?? '';
		})
		.join('');
}
