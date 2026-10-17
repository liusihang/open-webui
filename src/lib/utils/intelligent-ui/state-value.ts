/** The client and server use the same narrow persisted-value limits. */
export function validInteractionValue(
	key: string,
	value: unknown
): value is string | number | boolean {
	if (typeof value === 'boolean') return true;
	if (typeof value === 'number') return Number.isFinite(value) && Math.abs(value) <= 1e12;
	if (typeof value !== 'string') return false;
	const multiline = key.startsWith('writing:') || key.startsWith('shared:');
	const max = key.startsWith('writing:') ? 16384 : 256;
	return (
		value.length <= max &&
		!(multiline ? /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/ : /[\u0000-\u001f\u007f]/).test(
			value
		)
	);
}

/** Mirror server budgets before optimistic metadata mutation; never silently drop a draft. */
export function mergeInteractionState(
	blocks: Record<string, Record<string, string | number | boolean>>,
	block: string,
	values: Record<string, string | number | boolean>
): { version: 1; blocks: Record<string, Record<string, string | number | boolean>> } {
	const validKey = (key: string) =>
		key.length > 0 && key.length <= 512 && !['__proto__', 'prototype', 'constructor'].includes(key);
	if (!validKey(block) || Object.keys(values).length > 64)
		throw new Error('Interactive state limit exceeded');
	for (const [key, value] of Object.entries(values)) {
		if (!validKey(key) || !validInteractionValue(key, value))
			throw new Error('Invalid interactive state');
	}
	const result = { version: 1 as const, blocks: { ...blocks, [block]: { ...values } } };
	if (
		Object.keys(result.blocks).length > 64 ||
		new TextEncoder().encode(JSON.stringify(result)).length > 131072
	)
		throw new Error('Interactive state limit exceeded');
	return result;
}
