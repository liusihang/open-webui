import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Run the production extractor, without importing unrelated DOM/Markdown modules.
const source = readFileSync(new URL('./index.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('index.ts', source, ts.ScriptTarget.Latest, true);
const statement = parsed.statements.find(
	(node) =>
		ts.isVariableStatement(node) &&
		node.declarationList.declarations.some(
			(declaration) => declaration.name.getText(parsed) === 'extractContentFromFile'
		)
) as ts.VariableStatement;
const initializer = statement.declarationList.declarations[0].initializer!.getText(parsed);
const javascript = ts.transpileModule(`const extract = ${initializer};`, {
	compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
}).outputText;

const file = (name = 'paper.pdf', type = 'application/pdf') => ({
	name,
	type,
	arrayBuffer: async () => new TextEncoder().encode('%PDF-1.7\n').buffer,
	slice: () => ({ text: async () => '%PDF-1.7\n' })
});

const extractor = (pages: number, failure?: Error) => {
	const getTextContent = vi.fn(async () => ({ items: [{ str: 'page text' }] }));
	const getPage = vi.fn(async () => ({ getTextContent }));
	const destroy = vi.fn(async () => {});
	const getDocument = vi.fn(() => ({
		promise: failure
			? Promise.reject(failure)
			: Promise.resolve({ numPages: pages, getPage, destroy })
	}));
	const readAsText = vi.fn();
	const FileReader = function () {
		readAsText();
	};
	const extract = new Function('ensurePDFjsLoaded', 'FileReader', `${javascript}; return extract;`)(
		async () => ({ getDocument }),
		FileReader
	);
	return { extract, getDocument, getPage, readAsText, destroy };
};

describe('temporary chat PDF upload admission', () => {
	it.each([19, 20])('allows %i pages and reads the selected PDF', async (pages) => {
		const f = extractor(pages);
		await expect(f.extract(file(), { uploadContext: 'chat' })).resolves.toContain('page text');
		expect(f.getPage).toHaveBeenCalledTimes(pages);
	});

	it('rejects 21 pages before reading any page or falling back to text', async () => {
		const f = extractor(21);
		await expect(f.extract(file(), { uploadContext: 'chat' })).rejects.toThrow(
			/21.*20.*知识库.*引用/
		);
		expect(f.getPage).not.toHaveBeenCalled();
		expect(f.readAsText).not.toHaveBeenCalled();
		expect(f.destroy).toHaveBeenCalledOnce();
	});

	it('does not trust a PDF disguised as plain text', async () => {
		const f = extractor(21);
		await expect(
			f.extract(file('notes.txt', 'text/plain'), { uploadContext: 'chat' })
		).rejects.toThrow('知识库');
		expect(f.readAsText).not.toHaveBeenCalled();
	});

	it.each([0, NaN])('does not accept an unreadable page count %s', async (pages) => {
		const f = extractor(pages);
		await expect(f.extract(file(), { uploadContext: 'chat' })).rejects.toThrow('页数');
		expect(f.getPage).not.toHaveBeenCalled();
	});

	it('does not apply a chat limit to the generic extraction API', async () => {
		const f = extractor(21);
		await expect(f.extract(file())).resolves.toContain('page text');
		expect(f.getPage).toHaveBeenCalledTimes(21);
	});

	it('propagates PDF load errors instead of falling back to raw text', async () => {
		const f = extractor(1, new Error('PDF requires a password'));
		await expect(f.extract(file(), { uploadContext: 'chat' })).rejects.toThrow('password');
		expect(f.readAsText).not.toHaveBeenCalled();
	});

	it('enables chat admission in the temporary attachment caller and preserves API error detail', () => {
		const input = readFileSync(
			new URL('../components/chat/MessageInput.svelte', import.meta.url),
			'utf8'
		);
		expect(input).toContain("extractContentFromFile(file, { uploadContext: 'chat' })");
		const chat = readFileSync(new URL('../components/chat/Chat.svelte', import.meta.url), 'utf8');
		expect(chat).toContain('error: fileItem.error');
	});
});
