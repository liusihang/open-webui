import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, tick } from 'svelte';
import ExtendedHarness from './ExtendedHarness.svelte';
import { getFileBlobById } from '$lib/apis/files';

vi.mock('$lib/apis/files', () => ({ getFileBlobById: vi.fn() }));

const id = '11111111-1111-4111-8111-111111111111';
const src = `/api/v1/files/${id}/content`;
let app: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
const loadFile = vi.mocked(getFileBlobById);

beforeEach(() => {
	loadFile.mockReset().mockResolvedValue(new Blob(['image'], { type: 'image/png' }));
	vi.stubGlobal(
		'URL',
		class extends URL {
			static createObjectURL = vi.fn(() => 'blob:authenticated-image');
			static revokeObjectURL = vi.fn();
		}
	);
	localStorage.token = 'local-fixture-token';
});
afterEach(async () => {
	if (app) await unmount(app);
	app = undefined;
	target?.remove();
	localStorage.removeItem('token');
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});
async function render(type = 'Image', props: any = { src, alt: 'Terminal plot' }) {
	target = document.createElement('div');
	document.body.append(target);
	app = mount(ExtendedHarness, { target, props: { elements: [{ type, props }] } });
	await tick();
}

describe('authenticated Intelligent UI images', () => {
	it('uses the existing authenticated file API and renders a local blob', async () => {
		await render();
		await vi.waitFor(() =>
			expect(target.querySelector('img')?.getAttribute('src')).toBe('blob:authenticated-image')
		);
		expect(loadFile).toHaveBeenCalledWith('local-fixture-token', id, {
			signal: expect.any(AbortSignal)
		});
		expect(target.innerHTML).not.toContain('local-fixture-token');
		expect(target.querySelector('img')?.alt).toBe('Terminal plot');
	});
	it('loads terminal images in a gallery with their captions', async () => {
		await render('ImageGallery', {
			title: 'Results',
			images: [
				{ src, alt: 'A', caption: 'First' },
				{ src, alt: 'B' }
			]
		});
		await vi.waitFor(() => expect(target.querySelectorAll('img')).toHaveLength(2));
		expect(loadFile).toHaveBeenCalledTimes(2);
		expect(target.textContent).toContain('First');
	});
	it('does not attach credentials or call the file API for external images', async () => {
		await render('Image', { src: 'https://example.org/plot.png', alt: 'External' });
		expect(target.querySelector('img')?.getAttribute('src')).toBe('https://example.org/plot.png');
		expect(loadFile).not.toHaveBeenCalled();
	});
	it.each([403, 404])('shows an explicit unavailable state on HTTP %s', async (status) => {
		loadFile.mockRejectedValue({ status });
		await render();
		await vi.waitFor(() =>
			expect(target.textContent).toContain('Image unavailable: Terminal plot')
		);
		expect(target.querySelector('img')).toBeNull();
		expect(URL.createObjectURL).not.toHaveBeenCalled();
	});
	it.each(['text/html', 'image/svg+xml'])(
		'rejects a non-raster file response: %s',
		async (type) => {
			loadFile.mockResolvedValue(new Blob(['not a raster image'], { type }));
			await render();
			await vi.waitFor(() => expect(target.textContent).toContain('Image unavailable'));
			expect(URL.createObjectURL).not.toHaveBeenCalled();
		}
	);
	it('revokes blob URLs and cancels file loading on unmount', async () => {
		await render();
		await vi.waitFor(() => expect(target.querySelector('img')).not.toBeNull());
		const options = loadFile.mock.calls[0][2]!;
		await unmount(app!);
		app = undefined;
		expect(options.signal?.aborted).toBe(true);
		expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:authenticated-image');
	});
	it('does not create a blob URL when a stale request finishes after unmount', async () => {
		let finish: (blob: Blob) => void = () => {};
		loadFile.mockImplementation(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				})
		);
		await render();
		await unmount(app!);
		app = undefined;
		finish(new Blob(['image'], { type: 'image/png' }));
		await tick();
		expect(URL.createObjectURL).not.toHaveBeenCalled();
	});
});
