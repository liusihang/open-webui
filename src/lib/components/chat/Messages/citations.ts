type CitationSource = {
	source?: {
		id?: string;
		name?: string;
		url?: string;
		embed_url?: string;
	};
	document?: unknown[];
	metadata?: Array<Record<string, unknown> | undefined>;
	distances?: Array<number | undefined>;
	[key: string]: unknown;
};

export type CitationGroup = {
	id: string;
	source: Record<string, unknown> | undefined;
	document: unknown[];
	metadata: Array<Record<string, unknown> | undefined>;
	distances: Array<number | undefined>;
};

const getGroupId = (source: CitationSource, metadata?: Record<string, unknown>) => {
	const metadataSource =
		typeof metadata?.source === 'string' && metadata.source.length > 0 ? metadata.source : null;
	const sourceUrl =
		typeof source?.source?.url === 'string' && source.source.url.length > 0
			? source.source.url
			: null;
	const sourceId =
		typeof source?.source?.id === 'string' && source.source.id.length > 0 ? source.source.id : null;
	const sourceName =
		typeof source?.source?.name === 'string' && source.source.name.length > 0
			? source.source.name
			: null;

	return metadataSource ?? sourceId ?? sourceUrl ?? sourceName ?? 'N/A';
};

const getDisplaySource = (
	source: CitationSource,
	groupId: string,
	metadata?: Record<string, unknown>
) => {
	let displaySource = source?.source ? { ...source.source } : undefined;

	if (typeof metadata?.name === 'string' && metadata.name.length > 0) {
		displaySource = { ...displaySource, name: metadata.name };
	}

	if (groupId.startsWith('http://') || groupId.startsWith('https://')) {
		displaySource = { ...displaySource, name: groupId, url: groupId };
	}

	return displaySource;
};

const getDocumentKey = (
	groupId: string,
	document: unknown,
	metadata?: Record<string, unknown>,
	distance?: number
) => {
	return JSON.stringify([
		groupId,
		document,
		metadata?.source ?? null,
		metadata?.name ?? null,
		metadata?.page ?? null,
		distance ?? null
	]);
};

export const buildCitations = (sources: CitationSource[] = []): CitationGroup[] => {
	const groups = new Map<string, CitationGroup>();
	const documentKeys = new Set<string>();

	for (const source of sources ?? []) {
		if (!source || typeof source !== 'object' || Object.keys(source).length === 0) {
			continue;
		}

		const documents = Array.isArray(source.document) ? source.document : [];
		for (const [index, document] of documents.entries()) {
			const metadata = Array.isArray(source.metadata)
				? (source.metadata[index] as Record<string, unknown> | undefined)
				: undefined;
			const distance = Array.isArray(source.distances) ? source.distances[index] : undefined;
			const groupId = String(getGroupId(source, metadata));
			const displaySource = getDisplaySource(source, groupId, metadata);
			const documentKey = getDocumentKey(groupId, document, metadata, distance);

			if (documentKeys.has(documentKey)) {
				continue;
			}

			documentKeys.add(documentKey);

			if (!groups.has(groupId)) {
				groups.set(groupId, {
					id: groupId,
					source: displaySource,
					document: [],
					metadata: [],
					distances: []
				});
			}

			const group = groups.get(groupId)!;
			group.document.push(document);
			group.metadata.push(metadata);
			if (distance !== undefined) {
				group.distances.push(distance);
			}
		}
	}

	return Array.from(groups.values());
};

export const calculateShowRelevance = (sources: CitationGroup[]) => {
	const distances = sources.flatMap((citation) => citation.distances ?? []);
	const inRange = distances.filter((d) => d !== undefined && d >= -1 && d <= 1).length;
	const outOfRange = distances.filter((d) => d !== undefined && (d < -1 || d > 1)).length;

	if (distances.length === 0) {
		return false;
	}

	if (
		(inRange === distances.length - 1 && outOfRange === 1) ||
		(outOfRange === distances.length - 1 && inRange === 1)
	) {
		return false;
	}

	return true;
};

export const shouldShowPercentage = (sources: CitationGroup[]) => {
	const distances = sources.flatMap((citation) => citation.distances ?? []);
	return distances.length > 0 && distances.every((d) => d !== undefined && d >= -1 && d <= 1);
};
