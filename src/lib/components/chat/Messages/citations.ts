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

type CitationMapEntry =
	| string
	| {
			evidence_ref?: unknown;
			[key: string]: unknown;
	  };

export type CitationGroup = {
	id: string;
	source: Record<string, unknown> | undefined;
	document: unknown[];
	metadata: Array<Record<string, unknown> | undefined>;
	distances: Array<number | undefined>;
};

export type CitationPreview = {
	type: 'text' | 'image';
	title?: string;
	text?: string;
	caption?: string;
	ocr_text?: string;
	source_name?: string;
	page_index?: number;
	thumbnail_url?: string;
	content_url?: string;
	mime_type?: string;
	asset_ref?: string;
};

export type CitationTarget = {
	id: string;
	number: number;
	title: string;
	citation: CitationGroup;
	preview?: CitationPreview;
};

type CitationTargetOptions = {
	content?: string | null;
	metadata?: Record<string, unknown> | null;
};

const getMetadataString = (
	metadata: Record<string, unknown> | undefined,
	key: string
): string | null => {
	const value = metadata?.[key];
	return typeof value === 'string' && value.length > 0 ? value : null;
};

const getGroupId = (source: CitationSource, metadata?: Record<string, unknown>) => {
	const evidenceRef = getMetadataString(metadata, 'evidence_ref');
	const metadataSource = getMetadataString(metadata, 'source');
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

	return evidenceRef ?? metadataSource ?? sourceId ?? sourceUrl ?? sourceName ?? 'N/A';
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
		getMetadataString(metadata, 'source'),
		getMetadataString(metadata, 'evidence_ref'),
		getMetadataString(metadata, 'name'),
		metadata?.page ?? null,
		distance ?? null
	]);
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
};

const getCitationMap = (metadata?: Record<string, unknown> | null) => {
	const citationMap = metadata?.citation_map;
	return isPlainObject(citationMap) ? citationMap : undefined;
};

const getCitationMapEvidenceRef = (
	citationMap: Record<string, unknown> | undefined,
	citationNumber: number
): string | null => {
	const entry = citationMap?.[String(citationNumber)] as CitationMapEntry | undefined;
	if (typeof entry === 'string' && entry.length > 0) {
		return entry;
	}
	if (isPlainObject(entry)) {
		const evidenceRef = entry.evidence_ref;
		return typeof evidenceRef === 'string' && evidenceRef.length > 0 ? evidenceRef : null;
	}
	return null;
};

const getCitedNumbers = (content?: string | null): number[] => {
	if (!content) {
		return [];
	}

	const numbers: number[] = [];
	const seen = new Set<number>();
	const citationPattern = /\[((?:\d+(?:#[^,\]\s]+)?)(?:\s*,\s*\d+(?:#[^,\]\s]+)?)*)\]/g;
	for (const match of content.matchAll(citationPattern)) {
		for (const part of match[1].split(',')) {
			const value = parseInt(part.trim().split('#')[0], 10);
			if (Number.isInteger(value) && value > 0 && !seen.has(value)) {
				seen.add(value);
				numbers.push(value);
			}
		}
	}

	return numbers;
};

const getPreviewUrl = (
	metadata: Record<string, unknown> | undefined,
	key: string
): string | undefined => {
	const value = metadata?.[key];
	return typeof value === 'string' && value.length > 0 ? value : undefined;
};

const buildCitationPreview = (
	citation: CitationGroup,
	title: string
): CitationPreview | undefined => {
	const metadata = citation.metadata?.find((item) => item && isPlainObject(item));
	const preview = metadata?.preview;
	const previewObject = isPlainObject(preview) ? preview : undefined;
	const previewType =
		previewObject?.type === 'image' || metadata?.modality === 'image' ? 'image' : 'text';
	const text =
		getMetadataString(previewObject, 'text') ??
		getMetadataString(metadata, 'preview_text') ??
		(typeof citation.document?.[0] === 'string' ? citation.document[0] : undefined);

	const result: CitationPreview = {
		type: previewType,
		title,
		text,
		caption:
			getMetadataString(previewObject, 'caption') ??
			getMetadataString(metadata, 'caption') ??
			undefined,
		ocr_text:
			getMetadataString(previewObject, 'ocr_text') ??
			getMetadataString(metadata, 'ocr_text') ??
			undefined,
		source_name:
			getMetadataString(previewObject, 'source_name') ??
			getMetadataString(metadata, 'source_name') ??
			getMetadataString(metadata, 'source') ??
			undefined,
		page_index:
			typeof previewObject?.page_index === 'number'
				? previewObject.page_index
				: typeof metadata?.page_index === 'number'
					? metadata.page_index
					: typeof metadata?.page === 'number'
						? metadata.page
						: undefined,
		thumbnail_url:
			getPreviewUrl(metadata, 'thumbnail_url') ?? getPreviewUrl(previewObject, 'thumbnail_url'),
		content_url:
			getPreviewUrl(metadata, 'content_url') ?? getPreviewUrl(previewObject, 'content_url'),
		mime_type:
			getMetadataString(previewObject, 'mime_type') ??
			getMetadataString(metadata, 'mime_type') ??
			undefined,
		asset_ref:
			getMetadataString(previewObject, 'asset_ref') ??
			getMetadataString(metadata, 'asset_ref') ??
			undefined
	};

	if (
		!result.text &&
		!result.caption &&
		!result.ocr_text &&
		!result.thumbnail_url &&
		!result.content_url
	) {
		return undefined;
	}

	return result;
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

const getCitationTitle = (citation: CitationGroup) => {
	const metadata = citation.metadata?.find((item) => item && isPlainObject(item));
	const name = getMetadataString(metadata, 'name');
	const sourceName =
		typeof citation.source?.name === 'string' && citation.source.name.length > 0
			? citation.source.name
			: undefined;
	const source = getMetadataString(metadata, 'source');
	return name ?? sourceName ?? source ?? citation.id ?? 'N/A';
};

const targetFromCitation = (citation: CitationGroup, number: number): CitationTarget => {
	const title = getCitationTitle(citation);
	return {
		id: citation.id,
		number,
		title,
		citation,
		preview: buildCitationPreview(citation, title)
	};
};

export const buildCitationTargets = (
	sources: CitationSource[] = [],
	options: CitationTargetOptions = {}
): CitationTarget[] => {
	const citations = buildCitations(sources);
	const citationMap = getCitationMap(options.metadata);
	const citedNumbers = getCitedNumbers(options.content);
	const hasCitationMap = Boolean(citationMap);

	if (!hasCitationMap) {
		return citations.map((citation, index) => targetFromCitation(citation, index + 1));
	}

	const citationsById = new Map(citations.map((citation) => [citation.id, citation]));
	const numbers =
		citedNumbers.length > 0
			? citedNumbers
			: Object.keys(citationMap ?? {})
					.map((key) => parseInt(key, 10))
					.filter((value) => Number.isInteger(value) && value > 0)
					.sort((a, b) => a - b);
	const targets: CitationTarget[] = [];
	const seenIds = new Set<string>();

	for (const number of numbers) {
		const directCitation = citations[number - 1];
		const evidenceRef = getCitationMapEvidenceRef(citationMap, number);
		const mappedCitation = evidenceRef ? citationsById.get(evidenceRef) : undefined;
		const citation = mappedCitation ?? directCitation;

		if (!citation || seenIds.has(citation.id)) {
			continue;
		}
		seenIds.add(citation.id);
		targets.push(targetFromCitation(citation, number));
	}

	return targets;
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
