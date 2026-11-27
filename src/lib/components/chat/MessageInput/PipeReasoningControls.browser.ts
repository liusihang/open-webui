import { expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get, writable } from 'svelte/store';
import { models } from '$lib/stores';
import PipeReasoningControls from './PipeReasoningControls.svelte';

it('mounts before the model list arrives and shows reasoning controls when the selected model loads', async () => {
	const previous = get(models);
	models.set(null);
	const target = document.createElement('div');
	document.body.append(target);
	let control: ReturnType<typeof mount> | undefined;
	try {
		control = mount(PipeReasoningControls, {
			target,
			props: { selectedModels: ['bifrostapi.startup-test'] },
			context: new Map([['i18n', writable({ t: (key: string) => key, language: 'en' })]])
		});
		flushSync();
		const button = () => target.querySelector('button[aria-label="Reasoning effort"]');
		expect(button()).toBeNull();

		models.set([
			{ id: 'bifrostapi.startup-test', name: 'Startup test', owned_by: 'openai', external: false }
		]);
		flushSync();
		expect(button()).not.toBeNull();
	} finally {
		if (control) await unmount(control);
		target.remove();
		models.set(previous);
	}
});
