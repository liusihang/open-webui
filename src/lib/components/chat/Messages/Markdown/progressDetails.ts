import type { Token } from 'marked';

type DetailsAttributes = {
	type?: string;
	done?: string;
	hidden?: string;
};

export type ProgressDetailsToken = Token & {
	type: 'details';
	attributes?: DetailsAttributes;
};

const PROGRESS_TYPES = new Set(['reasoning', 'tool_calls']);
const TRUE_VALUES = new Set(['true', '1', 'yes']);

const isTruthyAttribute = (value: unknown): boolean => {
	if (typeof value === 'boolean') {
		return value;
	}

	if (typeof value === 'string') {
		return TRUE_VALUES.has(value.trim().toLowerCase());
	}

	return false;
};

export const isProgressDetailsToken = (token: Token): token is ProgressDetailsToken => {
	const detailsToken = token as ProgressDetailsToken;
	return (
		detailsToken?.type === 'details' && PROGRESS_TYPES.has(detailsToken?.attributes?.type ?? '')
	);
};

export const isProgressDetailsHidden = (token: ProgressDetailsToken): boolean => {
	return isTruthyAttribute(token?.attributes?.hidden);
};

export const isProgressDetailsDone = (token: ProgressDetailsToken): boolean => {
	return isTruthyAttribute(token?.attributes?.done);
};

export type ProgressDetailsState = {
	visibleTokens: ProgressDetailsToken[];
	currentToken: ProgressDetailsToken;
	historyTokens: ProgressDetailsToken[];
};

export const selectProgressDetailsState = (
	tokens: ProgressDetailsToken[]
): ProgressDetailsState | null => {
	const visibleTokens = tokens.filter((token) => !isProgressDetailsHidden(token));
	if (visibleTokens.length === 0) {
		return null;
	}

	const runningToken = [...visibleTokens].reverse().find((token) => !isProgressDetailsDone(token));
	const currentToken = runningToken ?? visibleTokens[visibleTokens.length - 1];
	const historyTokens = visibleTokens.filter((token) => token !== currentToken);

	return {
		visibleTokens,
		currentToken,
		historyTokens
	};
};
