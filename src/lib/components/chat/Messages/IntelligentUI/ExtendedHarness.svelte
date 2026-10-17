<script lang="ts">
	import { setContext, untrack } from 'svelte';
	import { createTestI18n } from './test-i18n';
	setContext('i18n', createTestI18n('en-US'));
	import ExtendedElement from './ExtendedElement.svelte';
	import {
		INTELLIGENT_UI_BLOCK,
		type IntelligentUIBlockContext,
		type InteractionState
	} from './context';
	let {
		elements,
		initial = {}
	}: { elements: { type: string; props: any; children?: string[] }[]; initial?: InteractionState } =
		$props();
	let state = $state<InteractionState>(untrack(() => ({ ...initial })));
	setContext<IntelligentUIBlockContext>(INTELLIGENT_UI_BLOCK, {
		spec: () => null,
		isDone: () => true,
		shared: (key) => state[key],
		writeShared: (key, value) => {
			state[key] = value;
		},
		read: (key, fallback) => state[key] ?? fallback,
		write: (key, value) => {
			state[key] = value;
		},
		canFollowUp: () => false,
		followUp: async () => {}
	});
</script>

{#each elements as element}<ExtendedElement {element}
		>{#snippet children()}<p>Panel contents</p>{/snippet}</ExtendedElement
	>{/each}
<output>{JSON.stringify(state)}</output>
