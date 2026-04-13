import { WEBUI_API_BASE_URL } from '$lib/constants';

export type OnlyOfficeSessionMode = 'view' | 'edit';

export type OnlyOfficeSessionResponse = {
	document_server_url: string;
	config: Record<string, unknown>;
};

type OnlyOfficeFileSessionRequest = {
	source_type?: 'file';
	file_id: string;
	mode?: OnlyOfficeSessionMode;
};

type OnlyOfficeTerminalSessionRequest = {
	source_type: 'terminal';
	terminal_server_id: string;
	terminal_file_path: string;
	mode?: OnlyOfficeSessionMode;
};

type OnlyOfficeSessionRequest = OnlyOfficeFileSessionRequest | OnlyOfficeTerminalSessionRequest;

const ONLYOFFICE_SESSION_TIMEOUT_MS = 12000;

const extractErrorDetail = (value: unknown): string | null => {
	if (typeof value === 'string' && value.trim()) {
		return value.trim();
	}

	if (Array.isArray(value)) {
		const details = value
			.map((item) => extractErrorDetail(item))
			.filter((item): item is string => Boolean(item));
		return details.length > 0 ? details.join('; ') : null;
	}

	if (value && typeof value === 'object') {
		const record = value as Record<string, unknown>;
		const detail =
			extractErrorDetail(record.detail) ??
			extractErrorDetail(record.message) ??
			extractErrorDetail(record.error) ??
			extractErrorDetail(record.msg);
		if (detail) {
			return detail;
		}

		if ('loc' in record && 'msg' in record) {
			const location = Array.isArray(record.loc) ? record.loc.join('.') : '';
			const message = extractErrorDetail(record.msg);
			if (message) {
				return location ? `${location}: ${message}` : message;
			}
		}
	}

	return null;
};

const parseOnlyOfficeError = async (response: Response): Promise<string> => {
	let detail: string | null = null;

	try {
		detail = extractErrorDetail(await response.clone().json());
	} catch {
		// No JSON body; fall back to text payload.
	}

	if (!detail) {
		try {
			detail = extractErrorDetail(await response.clone().text());
		} catch {
			// Ignore text parsing errors and use default message.
		}
	}

	return detail ?? `Failed to create OnlyOffice session (HTTP ${response.status}).`;
};

export const createOnlyOfficeSession = async (token: string, payload: OnlyOfficeSessionRequest) => {
	const controller = new AbortController();
	const timeoutId = setTimeout(() => controller.abort(), ONLYOFFICE_SESSION_TIMEOUT_MS);

	try {
		const response = await fetch(`${WEBUI_API_BASE_URL}/onlyoffice/session`, {
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			},
			body: JSON.stringify(payload),
			signal: controller.signal
		});

		if (!response.ok) {
			throw new Error(await parseOnlyOfficeError(response));
		}

		return (await response.json()) as OnlyOfficeSessionResponse;
	} catch (error) {
		if (error instanceof Error && error.name === 'AbortError') {
			throw new Error(
				`OnlyOffice session request timed out after ${Math.round(
					ONLYOFFICE_SESSION_TIMEOUT_MS / 1000
				)} seconds. Please retry.`
			);
		}

		if (error instanceof Error) {
			throw error;
		}

		throw new Error('Failed to create OnlyOffice session.');
	} finally {
		clearTimeout(timeoutId);
	}
};
