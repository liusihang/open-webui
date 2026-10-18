import { Lexer, Tokenizer, type TokenizerThis } from 'marked';

export function citationExtension() {
	return {
		name: 'citation',
		level: 'inline' as const,

		start(src: string) {
			// Trigger on any [number] or [number#suffix]
			// We check for a digit immediately after [ to avoid matching arbitrary links
			return src.search(/\[\d/);
		},

		tokenizer(this: TokenizerThis, src: string) {
			// Link labels are text owned by that link, never native source buttons.
			if (this.lexer.state.inLink) return;
			if (!/^\[\d/.test(src)) return;

			// Use marked's own link grammar (including reference definitions), not a
			// lookahead for "(". Its public tokenizer shares the current inline lexer.
			const markdown = new Tokenizer();
			new Lexer({ ...this.lexer.options, tokenizer: markdown });
			markdown.lexer = this.lexer;
			const linkAt = (text: string) => {
				const link = markdown.link(text) ?? markdown.reflink(text, this.lexer.tokens.links);
				return link?.type === 'link' ? link : undefined;
			};
			const link = linkAt(src);
			if (link) return link;

			const rule = /^\[(?:\d+(?:#[^,\]\s]+)?(?:,\s*\d+(?:#[^,\]\s]+)?)*)\]/;
			let raw = '';
			let remaining = src;
			let match: RegExpExecArray | null;
			while ((match = rule.exec(remaining))) {
				if (raw && linkAt(remaining)) break;
				raw += match[0];
				remaining = remaining.slice(match[0].length);
			}
			if (!raw) return;

			// Extract ALL bracket groups inside the big match
			const groupRegex = /\[([^\]]+)\]/g;
			const ids: number[] = [];
			const citationIdentifiers: string[] = [];
			let m: RegExpExecArray | null;

			while ((m = groupRegex.exec(raw))) {
				// m[1] is the content inside brackets, e.g. "1, 2#foo"
				const parts = m[1].split(',').map((p) => p.trim());

				parts.forEach((part) => {
					// Check if it starts with digit
					const match = /^(\d+)(?:#(.+))?$/.exec(part);
					if (match) {
						const index = parseInt(match[1], 10);
						if (!isNaN(index)) {
							ids.push(index);
							// Store the full identifier ("1#foo" or "1")
							citationIdentifiers.push(part);
						}
					}
				});
			}

			if (ids.length === 0) return;

			return {
				type: 'citation',
				raw,
				ids, // merged list of integers for legacy title lookup
				citationIdentifiers // merged list of full identifiers for granular targeting
			};
		},

		renderer(token: any) {
			// fallback text
			return token.raw;
		}
	};
}

export default function () {
	return {
		extensions: [citationExtension()]
	};
}
