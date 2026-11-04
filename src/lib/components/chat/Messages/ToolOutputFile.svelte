<script lang="ts">
	// Keep keys in nested template expressions visible to i18next-parser.
	// $i18n.t("Terminal unavailable");
	// $i18n.t("No preview available");

	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		config,
		settings,
		selectedTerminalId,
		showTabs,
		showFileNavPath,
		terminalServers
	} from '$lib/stores';
	import { downloadFileBlob, downloadFilePreview, readFile } from '$lib/apis/terminal';
	import { getFileContentById } from '$lib/apis/files';
	import FilePreview from '$lib/components/chat/FileNav/FilePreview.svelte';
	import Icon from '$lib/components/chat/FileNav/Icon.svelte';
	import { fileIconName } from '$lib/components/chat/FileNav/fileIcon';
	import { normalizeDocumentTargetPage } from '$lib/utils/documentPreview';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	export let item: any;
	export let chatId = '';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let expanded = true;
	let loading = false;
	let loadedKey = '';
	let error = '';
	let objectUrls: string[] = [];

	let fileImageUrl: string | null = null;
	let fileVideoUrl: string | null = null;
	let fileAudioUrl: string | null = null;
	let filePdfData: ArrayBuffer | null = null;
	let fileSqliteData: ArrayBuffer | null = null;
	let fileModelData: ArrayBuffer | null = null;
	let fileDocxData: ArrayBuffer | null = null;
	let fileContent: string | null = null;
	let fileOfficeHtml: string | null = null;
	let fileOfficeSlides: string[] | null = null;
	let currentSlide = 0;
	let excelSheetNames: string[] = [];
	let selectedExcelSheet = '';
	let excelWorkbook: any = null;

	const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'avif']);
	const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'ogv', 'avi', 'mkv']);
	const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'oga', 'flac', 'm4a', 'aac', 'wma', 'opus']);
	const SQLITE_EXTS = new Set(['db', 'sqlite', 'sqlite3', 'db3']);
	const OFFICE_EXTS = new Set(['docx', 'xlsx', 'xls', 'pptx']);
	const DOWNLOAD_ONLY_EXTS = new Set(['zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz', 'bin']);

	$: fileId =
		item?.source === 'open_webui'
			? (String(item.path ?? '').match(/^\/api\/v1\/files\/([^/]+)\/content$/)?.[1] ?? '')
			: '';
	// Content URLs have no extension; use the filename to select the preview.
	$: path = String(fileId ? item.name : item?.full_path || item?.path || '');
	$: name = String(item?.name || path.split('/').filter(Boolean).at(-1) || 'file');
	$: targetPage = normalizeDocumentTargetPage(item?.page);
	$: selector = item?.terminal_selector;
	$: sessionId = String(item?.session_id || chatId || '');
	$: terminal = $config?.features?.enable_tool_servers
		? resolveTerminal(
				selector,
				item?.terminal_id,
				$terminalServers,
				($settings as any)?.terminalServers ?? []
			)
		: null;
	$: unavailable = !terminal && !fileId;
	$: previewKey =
		fileId || (terminal ? `${terminal.url}|${terminal.key}|${path}|${sessionId}` : '');
	$: isPerChatTerminal =
		($terminalServers ?? []).find((server: any) => server.id === selector)?.contexts?.chat
			?.context_id === 'chat_id';
	$: previewBaseUrl =
		terminal && isPerChatTerminal && sessionId
			? `${terminal.url}/chats/${encodeURIComponent(sessionId)}`
			: (terminal?.url ?? '');
	$: previewClass = isImage(path)
		? 'h-[22rem]'
		: isPdf(path) ||
			  isOffice(path) ||
			  ['glb', 'gltf', 'stl', 'obj', 'ply'].includes(getExt(path)) ||
			  getExt(path) === 'html' ||
			  getExt(path) === 'htm' ||
			  getExt(path) === 'svg'
			? 'h-96'
			: 'h-72';
	$: if (
		expanded &&
		!unavailable &&
		path &&
		!isDownloadOnly(path) &&
		previewKey !== loadedKey &&
		!loading
	) {
		void loadPreview(previewKey);
	}

	const getExt = (value: string) => value.split('.').pop()?.toLowerCase() ?? '';
	const isImage = (value: string) => IMAGE_EXTS.has(getExt(value));
	const isVideo = (value: string) => VIDEO_EXTS.has(getExt(value));
	const isAudio = (value: string) => AUDIO_EXTS.has(getExt(value));
	const isSqlite = (value: string) => SQLITE_EXTS.has(getExt(value));
	const isPdf = (value: string) => getExt(value) === 'pdf';
	const isOffice = (value: string) => OFFICE_EXTS.has(getExt(value));
	const isDownloadOnly = (value: string) => DOWNLOAD_ONLY_EXTS.has(getExt(value));

	const t = (key: string, vars?: Record<string, unknown>) => $i18n?.t?.(key, vars) ?? key;

	function resolveTerminal(
		selector: string,
		terminalId: string | undefined,
		servers: any[] | null,
		directServers: any[]
	): { url: string; key: string } | null {
		if (!selector) return null;

		const systemTerminal = servers?.find((server: any) => server.id === selector);
		if (systemTerminal?.url) {
			return { url: systemTerminal.url, key: localStorage.token };
		}
		// Saved file references already identify their system terminal. Its same-origin
		// proxy enforces access; downloads need not wait for unrelated tool discovery.
		if (terminalId && terminalId === selector) {
			return {
				url: `${WEBUI_API_BASE_URL}/terminals/${encodeURIComponent(terminalId)}`,
				key: localStorage.token
			};
		}

		const directTerminal = directServers.find(
			(server: any) => server.url === selector && server.enabled
		);
		if (directTerminal?.url) {
			return { url: directTerminal.url, key: directTerminal.key ?? '' };
		}

		return null;
	}

	function clearPreview() {
		for (const url of objectUrls) URL.revokeObjectURL(url);
		objectUrls = [];
		fileImageUrl = null;
		fileVideoUrl = null;
		fileAudioUrl = null;
		filePdfData = null;
		fileSqliteData = null;
		fileModelData = null;
		fileDocxData = null;
		fileContent = null;
		fileOfficeHtml = null;
		fileOfficeSlides = null;
		currentSlide = 0;
		excelSheetNames = [];
		selectedExcelSheet = '';
		excelWorkbook = null;
	}

	async function blobForPreview() {
		if (fileId) {
			const data = await getFileContentById(fileId).catch(() => null);
			return data
				? { blob: new Blob([data], { type: item?.content_type ?? '' }), filename: name }
				: null;
		}
		if (!terminal) return null;
		return downloadFileBlob(terminal.url, terminal.key, path, sessionId || undefined);
	}

	async function loadExcelSheet(sheet: string) {
		if (!excelWorkbook) return;
		selectedExcelSheet = sheet;
		const { excelToTable } = await import('$lib/utils/excelToTable');
		const result = await excelToTable(excelWorkbook.Sheets[selectedExcelSheet]);
		const DOMPurify = (await import('dompurify')).default;
		fileOfficeHtml = DOMPurify.sanitize(result.html);
	}

	async function loadPreview(key: string) {
		loadedKey = key;
		loading = true;
		error = '';
		clearPreview();

		try {
			if (isImage(path) || isVideo(path) || isAudio(path)) {
				const result = await blobForPreview();
				if (!result) throw new Error(t('Preview failed'));
				const url = URL.createObjectURL(result.blob);
				objectUrls = [...objectUrls, url];
				if (isImage(path)) fileImageUrl = url;
				else if (isVideo(path)) fileVideoUrl = url;
				else fileAudioUrl = url;
			} else if (['glb', 'gltf', 'stl', 'obj', 'ply'].includes(getExt(path))) {
				const result = await blobForPreview();
				if (!result) throw new Error(t('Preview failed'));
				fileModelData = await result.blob.arrayBuffer();
			} else if (isPdf(path) || isSqlite(path) || isOffice(path)) {
				const ext = getExt(path);

				if (isPdf(path)) {
					const result = await blobForPreview();
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await result.blob.arrayBuffer();
					filePdfData = arrayBuffer;
				} else if (isSqlite(path)) {
					const result = await blobForPreview();
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await result.blob.arrayBuffer();
					fileSqliteData = arrayBuffer;
				} else if (ext === 'docx') {
					const preview = terminal
						? await downloadFilePreview(terminal.url, terminal.key, path, sessionId || undefined)
						: null;
					if (preview) {
						filePdfData = await preview.blob.arrayBuffer();
					} else {
						const result = await blobForPreview();
						if (!result) throw new Error(t('Preview failed'));
						fileDocxData = await result.blob.arrayBuffer();
					}
				} else if (ext === 'xlsx' || ext === 'xls') {
					const result = await blobForPreview();
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await result.blob.arrayBuffer();
					const XLSX = await import('xlsx');
					excelWorkbook = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
					excelSheetNames = excelWorkbook.SheetNames;
					if (excelSheetNames.length > 0) {
						await loadExcelSheet(excelSheetNames[0]);
					}
				} else if (ext === 'pptx') {
					const preview = terminal
						? await downloadFilePreview(terminal.url, terminal.key, path, sessionId || undefined)
						: null;
					if (preview) {
						filePdfData = await preview.blob.arrayBuffer();
					} else {
						const result = await blobForPreview();
						if (!result) throw new Error(t('Preview failed'));
						const arrayBuffer = await result.blob.arrayBuffer();
						const { pptxToImages } = await import('$lib/utils/pptxToHtml');
						const resultImages = await pptxToImages(arrayBuffer);
						fileOfficeSlides = resultImages.images;
					}
				}
			} else if (fileId && !isDownloadOnly(path)) {
				const result = await blobForPreview();
				if (!result) throw new Error(t('Preview failed'));
				fileContent = await result.blob.text();
			} else if (terminal && !isDownloadOnly(path)) {
				fileContent = await readFile(terminal.url, terminal.key, path, sessionId || undefined);
			}
		} catch (e) {
			error = e instanceof Error ? e.message : t('Preview failed');
		} finally {
			loading = false;
		}
	}

	function openInFiles() {
		if (unavailable || !path) return;
		selectedTerminalId.set(selector);
		showTabs.set(true);
		showFileNavPath.set(targetPage ? { path, page: targetPage } : path);
	}

	async function downloadFile() {
		if (unavailable || !path) return;
		const result = await blobForPreview();
		if (!result) {
			toast.error(t('Download failed'));
			return;
		}

		const url = URL.createObjectURL(result.blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = result.filename;
		document.body.appendChild(a);
		a.click();
		a.remove();
		// Keep the object URL alive until the browser starts the download, including on mobile.
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}

	onDestroy(clearPreview);
</script>

<div
	data-terminal-file={path}
	class="my-2 w-full min-w-0 max-w-full overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-white/8 dark:bg-gray-950/20"
>
	<div
		class="flex min-h-11 items-center sm:min-h-8 {expanded
			? 'border-b border-gray-100 dark:border-white/8'
			: ''}"
	>
		<button
			type="button"
			class="flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2.5 text-left sm:min-h-8"
			on:click={() => (expanded = !expanded)}
			aria-expanded={expanded}
		>
			<div
				class="flex size-5 shrink-0 items-center justify-center text-gray-500 dark:text-gray-400"
			>
				<Icon name={fileIconName(name || path || '', 'file')} size={14} />
			</div>
			<div
				title={name}
				class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800 dark:text-gray-100"
			>
				{name}
			</div>
		</button>

		<button
			type="button"
			class="mr-1 flex size-9 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:text-gray-700 disabled:opacity-40 disabled:hover:text-gray-400 dark:text-gray-500 dark:hover:text-gray-200 dark:disabled:hover:text-gray-500 sm:size-6"
			disabled={unavailable}
			on:click|stopPropagation={downloadFile}
			aria-label={t('Download')}
		>
			<Icon name="download" size={13} />
		</button>
		{#if !fileId}
			<button
				type="button"
				class="mr-1 flex size-9 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:text-gray-700 disabled:opacity-40 disabled:hover:text-gray-400 dark:text-gray-500 dark:hover:text-gray-200 dark:disabled:hover:text-gray-500 sm:size-6"
				disabled={unavailable}
				on:click|stopPropagation={openInFiles}
				aria-label={t('Open')}
			>
				<Icon name="external-link" size={13} />
			</button>
		{/if}
	</div>

	{#if expanded}
		{#if unavailable}
			<div class="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
				{t('Terminal unavailable')}
			</div>
		{:else if error}
			<div class="px-4 py-3 text-xs text-red-500 dark:text-red-400">{error}</div>
		{:else if isDownloadOnly(path)}
			<div class="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
				{t('No preview available')}
			</div>
		{:else}
			<div
				class="{previewClass} max-h-[75vh] min-h-24 resize-y overflow-hidden bg-gray-50 dark:bg-gray-950"
			>
				<FilePreview
					selectedFile={path}
					fileLoading={loading}
					{fileImageUrl}
					{fileVideoUrl}
					{fileAudioUrl}
					{filePdfData}
					{fileSqliteData}
					{fileModelData}
					{fileDocxData}
					{fileContent}
					baseUrl={fileId ? '' : previewBaseUrl}
					chatId={sessionId}
					apiKey={terminal?.key ?? ''}
					{fileOfficeHtml}
					{fileOfficeSlides}
					{currentSlide}
					{targetPage}
					{excelSheetNames}
					{selectedExcelSheet}
					onSheetChange={loadExcelSheet}
					readOnly={true}
				/>
				{#if !loading && fileImageUrl === null && fileVideoUrl === null && fileAudioUrl === null && filePdfData === null && fileSqliteData === null && fileModelData === null && fileDocxData === null && fileContent === null && fileOfficeHtml === null && fileOfficeSlides === null}
					<div
						class="flex h-full items-center justify-center px-3 text-xs text-gray-500 dark:text-gray-400"
					>
						{t('No preview available')}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
