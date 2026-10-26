import { describe, expect, it } from 'vitest';
import { receiveUploadBatch } from './upload-batch';

describe('bulk upload receipt scheduling', () => {
	it('receives all 150 files with three transfers without waiting for indexing', async () => {
		let active = 0;
		let peak = 0;
		const received: number[] = [];
		const files = Array.from({ length: 150 }, (_, id) => id);
		const failures = await receiveUploadBatch(files, async (file) => {
			peak = Math.max(peak, ++active);
			await Promise.resolve();
			received.push(file);
			--active;
		});
		expect(peak).toBe(3);
		expect(received).toHaveLength(150);
		expect(new Set(received).size).toBe(150);
		expect(failures).toEqual([]);
	});

	it('continues past a failure, without retrying a possibly already stored upload', async () => {
		const attempts: number[] = [];
		const failures = await receiveUploadBatch([0, 1, 2, 3], async (file) => {
			attempts.push(file);
			if (file === 1) throw new Error('connection lost');
		});
		expect(attempts.sort()).toEqual([0, 1, 2, 3]);
		expect(failures.map(({ index }) => index)).toEqual([1]);
	});

	it('uses a snapshot even when the selected file list changes during a transfer', async () => {
		const files = [0, 1, 2, 3];
		const received: number[] = [];
		await receiveUploadBatch(
			files,
			async (file) => {
				files.splice(0, files.length);
				received.push(file);
			},
			1
		);
		expect(received).toEqual([0, 1, 2, 3]);
	});
});
