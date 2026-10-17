<script lang="ts">
	import { setContext, untrack } from 'svelte';
	import { INTELLIGENT_UI_BLOCK, type IntelligentUIBlockContext } from './context';
	import { createTestI18n } from './test-i18n';
	import AppBlock from './AppBlock.svelte';
	import WritingBlock from './WritingBlock.svelte';
	let {
		kind = 'WritingBlock',
		initialView,
		initialDone = true,
		onWrite = () => {}
	}: {
		kind?: string;
		initialView: any;
		initialDone?: boolean;
		onWrite?: (key: string, value: any) => void;
	} = $props();
	let view = $state(untrack(() => initialView));
	let done = $state(untrack(() => initialDone));
	let values = $state<Record<string, any>>({});
	export function update(next: any, finished = true) {
		view = next;
		done = finished;
	}
	setContext('i18n', createTestI18n('en-US'));
	setContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK, {
		spec: () => null,
		isDone: () => done,
		shared: (key) => values[key],
		writeShared: (key, value) => {
			values[key] = value;
			onWrite(key, value);
		},
		read: (key, fallback) => values[key] ?? fallback,
		write: (key, value) => {
			values[key] = value;
			onWrite(key, value);
		},
		canFollowUp: () => false,
		followUp: async () => {}
	});
</script>

{#if kind === 'AppBlock'}<AppBlock {view} />{:else}<WritingBlock {view} />{/if}
