<script lang="ts">
	import { setContext, untrack } from 'svelte';
	import IntelligentUIBlock from './IntelligentUIBlock.svelte';
	import { INTELLIGENT_UI_HOST, type IntelligentUIHost, type InteractionState } from './context';
	import { createTestI18n } from './test-i18n';
	let {
		initialSource,
		secondSource = '{"op":"add","path":"/root","value":"explanation"}\n{"op":"add","path":"/elements/explanation","value":{"type":"Explanation","props":{"title":"Second view","content":"Second explanation"},"children":[]}}',
		initialDone = true,
		writable = true,
		locale = 'en-US',
		saved = {},
		onWrite = () => {},
		onFollowUp = async () => {}
	}: {
		initialSource: string;
		secondSource?: string;
		initialDone?: boolean;
		writable?: boolean;
		locale?: string;
		saved?: Record<string, InteractionState>;
		onWrite?: (block: string, state: InteractionState) => void;
		onFollowUp?: (prompt: string) => Promise<void>;
	} = $props();
	let source = $state(untrack(() => initialSource));
	let done = $state(untrack(() => initialDone));
	setContext('i18n', createTestI18n(untrack(() => locale)));
	export function updateSource(next: string, finished = false) {
		source = next;
		done = finished;
	}
	setContext<IntelligentUIHost>(INTELLIGENT_UI_HOST, {
		canWrite: () => writable && done,
		canFollowUp: () => writable && done,
		read: (block) => saved[block] ?? {},
		write: (block, state) => onWrite(block, state),
		followUp: (prompt) => onFollowUp(prompt)
	});
</script>

<p>Text before the first view.</p>
<IntelligentUIBlock {source} {done} blockId="first" />
<p>Text between the two views.</p>
<IntelligentUIBlock source={secondSource} done={true} blockId="second" />
<p>Text after the second view.</p>
