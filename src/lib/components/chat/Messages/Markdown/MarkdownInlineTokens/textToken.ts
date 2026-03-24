export const getTextTokenSegments = (raw: string): string[] => {
	return raw.match(/\S+\s*|\s+/g) ?? [];
};

export const getTextTokenShouldPreserveStreamingMarkup = (
	current: boolean,
	done: boolean
): boolean => {
	return current || !done;
};
