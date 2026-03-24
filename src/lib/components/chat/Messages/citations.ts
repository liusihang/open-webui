type CitationSourceDocument = {
	source?: Record<string, any>;
	document?: string[];
	metadata?: Array<Record<string, any> | undefined>;
	distances?: Array<number | undefined>;
};

type CitationBucket = {
	id: string;
	source: Record<string, any>;
	document: string[];
	metadata: Array<Record<string, any> | undefined>;
	distances: number[];
};

type CitationSummaryItem = {
	id: string;
	source: Record<string, any>;
	hasUrl: boolean;
};

export const buildCitations = (sources: CitationSourceDocument[] = []): CitationBucket[] => {
	return sources.reduce<CitationBucket[]>((acc, source) => {
		if (!source || Object.keys(source).length === 0) {
			return acc;
		}

		source?.document?.forEach((document, index) => {
			const metadata = source?.metadata?.[index];
			const distance = source?.distances?.[index];

			const id = metadata?.source ?? source?.source?.id ?? 'N/A';
			let normalizedSource = source?.source ?? {};

			if (metadata?.name) {
				normalizedSource = { ...normalizedSource, name: metadata.name };
			}

			if (id.startsWith('http://') || id.startsWith('https://')) {
				normalizedSource = { ...normalizedSource, name: id, url: id };
			}

			const existingSource = acc.find((item) => item.id === id);

			if (existingSource) {
				existingSource.document.push(document);
				existingSource.metadata.push(metadata);
				if (distance !== undefined) existingSource.distances.push(distance);
			} else {
				acc.push({
					id,
					source: normalizedSource,
					document: [document],
					metadata: metadata ? [metadata] : [],
					distances: distance !== undefined ? [distance] : []
				});
			}
		});

		return acc;
	}, []);
};

export const summarizeCitations = (sources: CitationSourceDocument[] = []) => {
	const summary = new Map<string, CitationSummaryItem>();
	const distances: number[] = [];

	for (const source of sources) {
		if (!source || Object.keys(source).length === 0) {
			continue;
		}

		for (let index = 0; index < (source.document ?? []).length; index += 1) {
			const metadata = source?.metadata?.[index];
			const distance = source?.distances?.[index];
			const id = metadata?.source ?? source?.source?.id ?? 'N/A';
			let normalizedSource = source?.source ?? {};

			if (metadata?.name) {
				normalizedSource = { ...normalizedSource, name: metadata.name };
			}

			if (id.startsWith('http://') || id.startsWith('https://')) {
				normalizedSource = { ...normalizedSource, name: id, url: id };
			}

			if (!summary.has(id)) {
				const sourceName = normalizedSource?.name ?? '';
				summary.set(id, {
					id,
					source: normalizedSource,
					hasUrl: typeof sourceName === 'string' && sourceName.startsWith('http')
				});
			}

			if (distance !== undefined) {
				distances.push(distance);
			}
		}
	}

	const items = Array.from(summary.values());
	return {
		items,
		count: items.length,
		urlSources: items.filter((item) => item.hasUrl),
		distances
	};
};

export const calculateShowRelevance = (distances: Array<number | undefined>) => {
	const normalized = distances.filter((d): d is number => d !== undefined);
	const inRange = normalized.filter((d) => d >= -1 && d <= 1).length;
	const outOfRange = normalized.filter((d) => d < -1 || d > 1).length;

	if (normalized.length === 0) {
		return false;
	}

	if (
		(inRange === normalized.length - 1 && outOfRange === 1) ||
		(outOfRange === normalized.length - 1 && inRange === 1)
	) {
		return false;
	}

	return true;
};

export const shouldShowPercentage = (distances: Array<number | undefined>) => {
	const normalized = distances.filter((d): d is number => d !== undefined);
	return normalized.length > 0 && normalized.every((d) => d >= -1 && d <= 1);
};
