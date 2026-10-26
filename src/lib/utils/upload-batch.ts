/** Transfer a batch without waiting for indexing; never replay ambiguous uploads. */
export async function receiveUploadBatch<T>(
	items: readonly T[],
	receive: (item: T, index: number) => Promise<void>,
	concurrency = 3
) {
	const batch = [...items];
	let next = 0;
	const failures: { index: number; error: unknown }[] = [];
	await Promise.all(
		Array.from({ length: Math.min(batch.length, Math.max(1, concurrency)) }, async () => {
			while (next < batch.length) {
				const index = next++;
				try {
					await receive(batch[index], index);
				} catch (error) {
					failures.push({ index, error });
				}
			}
		})
	);
	return failures;
}
