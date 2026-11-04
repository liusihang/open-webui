/** Exercise the production component's upload handlers without the workspace chrome. */
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { receiveUploadBatch } from '../../src/lib/utils/upload-batch';

function handlers(
	uploadFile: (...args: any[]) => any,
	validateKnowledgeUpload = vi.fn().mockResolvedValue({})
) {
	const component = readFileSync(
		'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte',
		'utf8'
	);
	const script = component.split('<script lang="ts">')[1].split('</script>')[0];
	const parsed = ts.createSourceFile('upload.ts', script, ts.ScriptTarget.Latest, true);
	const names = new Set([
		'validateUpload',
		'uploadEntries',
		'uploadFilesHandler',
		'uploadFileHandler'
	]);
	const source = parsed.statements
		.filter(
			(node) =>
				ts.isVariableStatement(node) &&
				node.declarationList.declarations.some((decl) => names.has(decl.name.getText(parsed)))
		)
		.map((node) => node.getText(parsed))
		.join('\n');
	const code = ts.transpileModule(source, {
		compilerOptions: { target: ts.ScriptTarget.ES2022 }
	}).outputText;
	return new Function(
		'uploadFile',
		'receiveUploadBatch',
		'validateKnowledgeUpload',
		`
		let knowledge = {id: 'original-kb'}, currentDirectoryId = 'original-dir', destroyed = false;
		let fileItems = [], uploadBatches = [], sequence = 0;
		const $i18n = {t: text => text}, $config = {file: {max_size: 10}}, $settings = {};
		const localStorage = {token: 'synthetic-token'};
		const toast = {error: () => {}, success: () => {}};
		const uuidv4 = () => String(++sequence), refreshUploadsSoon = () => {}, init = async () => {};
		${code}
		return {uploadFilesHandler, uploadFileHandler,
			changeTarget: () => {knowledge.id = 'another-kb'; currentDirectoryId = 'another-dir';},
			snapshot: () => ({fileItems, uploadBatches})};
	`
	)(uploadFile, receiveUploadBatch, validateKnowledgeUpload);
}

describe('knowledge upload handlers', () => {
	it('submits all 150 receipts without opening an indexing stream and keeps the original target', async () => {
		let index = 0;
		let active = 0;
		let peak = 0;
		const upload = vi.fn(async () => {
			peak = Math.max(peak, ++active);
			await Promise.resolve();
			controller.changeTarget();
			--active;
			return { id: String(++index), ingestion_job_id: `job-${index}`, data: { status: 'pending' } };
		});
		const controller = handlers(upload);
		await controller.uploadFilesHandler(
			Array.from({ length: 150 }, (_, i) => new File([`body ${i}`], `paper-${i}.txt`))
		);
		expect(upload).toHaveBeenCalledTimes(150);
		expect(peak).toBe(3);
		for (const call of upload.mock.calls as unknown as unknown[][]) {
			expect(call[2]).toMatchObject({ knowledge_id: 'original-kb', directory_id: 'original-dir' });
			expect(call[4]).toBe(false);
		}
		expect(controller.snapshot().uploadBatches[0]).toEqual({
			total: 150,
			received: 150,
			failed: 0,
			remaining: 0
		});
	});

	it('rejects a batch before transferring files or counting receipts', async () => {
		const upload = vi.fn().mockResolvedValue({ id: 'unexpected' });
		const validate = vi.fn().mockRejectedValue('Batch limit exceeded');
		const controller = handlers(upload, validate);
		await controller.uploadFilesHandler([new File(['body'], 'paper.txt')]);
		expect(validate).toHaveBeenCalledWith('synthetic-token', 'original-kb', 1, [], 4);
		expect(upload).not.toHaveBeenCalled();
		expect(controller.snapshot()).toEqual({ fileItems: [], uploadBatches: [] });
	});

	it('captures the target before asynchronous quota validation', async () => {
		const upload = vi.fn().mockResolvedValue({ id: 'receipt' });
		const validate = vi.fn(async () => {
			controller.changeTarget();
		});
		const controller = handlers(upload, validate);
		await controller.uploadFilesHandler([new File(['body'], 'paper.txt')]);
		expect((validate.mock.calls as unknown as any[][])[0][1]).toBe('original-kb');
		expect((upload.mock.calls as unknown as any[][])[0][2]).toMatchObject({
			knowledge_id: 'original-kb',
			directory_id: 'original-dir'
		});
	});

	it('retains a specific transfer error and continues uploading the rest', async () => {
		const upload = vi
			.fn()
			.mockRejectedValueOnce('目标知识库已删除')
			.mockResolvedValue({ id: 'received' });
		const controller = handlers(upload);
		await controller.uploadFilesHandler([new File(['a'], 'a.txt'), new File(['b'], 'b.txt')]);
		expect(upload).toHaveBeenCalledTimes(2);
		expect(controller.snapshot().uploadBatches[0]).toEqual({
			total: 2,
			received: 1,
			failed: 1,
			remaining: 0
		});
		expect(controller.snapshot().fileItems[0]).toMatchObject({
			status: 'failed',
			error: '目标知识库已删除'
		});
	});
});

describe('processing status recovery', () => {
	it('does not turn a network error into an empty successful status list', async () => {
		const source = readFileSync('src/lib/apis/knowledge/index.ts', 'utf8');
		const tree = ts.createSourceFile('knowledge.ts', source, ts.ScriptTarget.Latest, true);
		const node = tree.statements.find(
			(node) =>
				ts.isVariableStatement(node) &&
				node.declarationList.declarations.some(
					(decl) => decl.name.getText(tree) === 'getPendingKnowledgeFiles'
				)
		)!;
		const code = ts.transpileModule(node.getText(tree).replace(/^export /, ''), {
			compilerOptions: { target: ts.ScriptTarget.ES2022 }
		}).outputText;
		const request = new Function(
			'fetch',
			'WEBUI_API_BASE_URL',
			`${code}; return getPendingKnowledgeFiles;`
		)(vi.fn().mockRejectedValue(new Error('network interrupted')), '/fixture');
		const log = vi.spyOn(console, 'error').mockImplementation(() => {});
		try {
			await expect(request('token', 'kb')).rejects.toBe('network interrupted');
		} finally {
			log.mockRestore();
		}
	});

	it('keeps received rows and resumes polling after a temporary disconnect', async () => {
		const component = readFileSync(
			'src/lib/components/workspace/Knowledge/KnowledgeBase/Files.svelte',
			'utf8'
		);
		const tree = ts.createSourceFile(
			'files.ts',
			component.split('<script lang="ts">')[1].split('</script>')[0],
			ts.ScriptTarget.Latest,
			true
		);
		const node = tree.statements.find(
			(node) =>
				ts.isVariableStatement(node) &&
				node.declarationList.declarations.some((decl) => decl.name.getText(tree) === 'poll')
		)!;
		const code = ts.transpileModule(node.getText(tree), {
			compilerOptions: { target: ts.ScriptTarget.ES2022 }
		}).outputText;
		const status = vi
			.fn()
			.mockResolvedValueOnce([
				{ id: 'received', data: { status: 'pending', processing_status: 'waiting' } }
			])
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValueOnce([]);
		const controller = new Function(
			'getPendingKnowledgeFiles',
			`
			let timer, pending = [], mounted = true;
			const knowledge = {id: 'kb'}, localStorage = {token: 'fixture'}, reload = async () => {};
			${code}; return {poll, rows: () => pending};
		`
		)(status);
		vi.useFakeTimers();
		const log = vi.spyOn(console, 'warn').mockImplementation(() => {});
		try {
			await controller.poll();
			expect(controller.rows()[0].status).toBe('waiting');
			await vi.advanceTimersByTimeAsync(5000);
			expect(controller.rows()[0].id).toBe('received');
			await vi.advanceTimersByTimeAsync(5000);
			expect(status).toHaveBeenCalledTimes(3);
			expect(controller.rows()).toEqual([]);
			expect(vi.getTimerCount()).toBe(0);
		} finally {
			vi.useRealTimers();
			log.mockRestore();
		}
	});
});

function productionHandler(name: string) {
	const source = readFileSync(
		'src/lib/components/workspace/Knowledge/KnowledgeBase.svelte',
		'utf8'
	);
	const tree = ts.createSourceFile(
		'knowledge.ts',
		source.split('<script lang="ts">')[1].split('</script>')[0],
		ts.ScriptTarget.Latest,
		true
	);
	const node = tree.statements.find(
		(node) =>
			ts.isVariableStatement(node) &&
			node.declarationList.declarations.some((decl) => decl.name.getText(tree) === name)
	);
	if (!node) throw new Error(`Missing production handler: ${name}`);
	return ts.transpileModule(node.getText(tree), {
		compilerOptions: { target: ts.ScriptTarget.ES2022 }
	}).outputText;
}

describe('knowledge batch quota boundaries', () => {
	it('validates a mixed drop once before directory writes and submits a single receipt batch', async () => {
		const order: string[] = [];
		const validate = vi.fn(async () => {
			order.push('validate');
			return true;
		});
		const mkdir = vi.fn(async () => {
			order.push('mkdir');
			return { folder: 'directory-id' };
		});
		const upload = vi.fn(async () => {
			order.push('upload');
			return 0;
		});
		const directoryUpload = new Function(
			'validateUpload',
			'createMissingDirectories',
			'uploadEntries',
			`
			const knowledge = {id: 'original-kb'}, currentDirectoryId = 'root', breadcrumbs = [];
			let syncing;
			const localStorage = {token: 'fixture'}, $i18n = {t: text => text};
			const toast = {error: () => {}, success: () => {}}, init = () => {};
			const buildDirectoryManifest = async entries => entries.map(entry => ({...entry, checksum: 'checksum', size: entry.file.size}));
			const syncKnowledgeDiff = async () => ({mkdir: ['folder'], directory_map: {}});
			${productionHandler('uploadDirectoryEntries')}
			return uploadDirectoryEntries;
		`
		)(validate, mkdir, upload);
		await directoryUpload(
			[{ path: 'folder', filename: 'nested.txt', file: new File(['nested'], 'nested.txt') }],
			[new File(['loose'], 'loose.txt')]
		);
		expect(validate).toHaveBeenCalledTimes(1);
		expect(validate).toHaveBeenCalledWith(2, [], 6, 'original-kb');
		expect(order).toEqual(['validate', 'mkdir', 'upload']);
		expect(upload).toHaveBeenCalledOnce();
		const [entries, target] = upload.mock.calls[0] as unknown as [any[], string];
		expect(target).toBe('original-kb');
		expect(entries.map(({ file, directory_id }) => [file.name, directory_id])).toEqual([
			['loose.txt', 'root'],
			['nested.txt', 'directory-id']
		]);
	});

	it('rejects replacement count/size before sync cleanup can remove existing files', async () => {
		const validate = vi.fn().mockResolvedValue(false);
		const cleanup = vi.fn();
		const mkdir = vi.fn();
		const upload = vi.fn();
		const sync = new Function(
			'validateUpload',
			'syncKnowledgeCleanup',
			'createMissingDirectories',
			'uploadManifestEntries',
			`
			const knowledge = {id: 'kb'}, localStorage = {token: 'fixture'}, $i18n = {t: text => text};
			const toast = {error: () => {}, success: () => {}}, browser = {clearRemovedSelection: () => {}}, init = () => {};
			let syncing, pendingSyncFiles = [
				{path: '', filename: 'modified.txt', file: new File(['replacement'], 'modified.txt')},
				{path: '', filename: 'unchanged.txt', file: new File(['x'.repeat(100)], 'unchanged.txt')}
			];
			const buildDirectoryManifest = async entries => entries.map(entry => ({...entry, checksum: 'checksum', size: entry.file.size}));
			const syncKnowledgeDiff = async () => ({added: [], modified: [{path: '', filename: 'modified.txt', stale_file_id: 'old-modified'}], deleted: [{file_id: 'deleted'}], rmdir: []});
			${productionHandler('syncDirectoryHandler')}
			return syncDirectoryHandler;
		`
		)(validate, cleanup, mkdir, upload);
		await sync();
		expect(validate).toHaveBeenCalledTimes(1);
		expect(validate).toHaveBeenCalledWith(1, ['deleted', 'old-modified'], 11, 'kb');
		expect(cleanup).not.toHaveBeenCalled();
		expect(mkdir).not.toHaveBeenCalled();
		expect(upload).not.toHaveBeenCalled();
	});
});
