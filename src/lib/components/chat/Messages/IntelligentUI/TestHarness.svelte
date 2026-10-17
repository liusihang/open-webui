<script lang="ts">
	import { setContext, untrack } from 'svelte';
	import IntelligentUIBlock from './IntelligentUIBlock.svelte';
	import { INTELLIGENT_UI_HOST, type IntelligentUIHost, type InteractionState } from './context';
	let {
		initialSource,
		initialDone = true,
		writable = true,
		saved = {},
		onWrite = () => {},
		onFollowUp = async () => {}
	}: {
		initialSource: string;
		initialDone?: boolean;
		writable?: boolean;
		saved?: Record<string, InteractionState>;
		onWrite?: (block: string, state: InteractionState) => void;
		onFollowUp?: (prompt: string) => Promise<void>;
	} = $props();
	let source = $state(untrack(() => initialSource));
	let done = $state(untrack(() => initialDone));
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
<IntelligentUIBlock
	source={'{"op":"add","path":"/root","value":"explanation"}\n{"op":"add","path":"/elements/explanation","value":{"type":"Explanation","props":{"title":"Second view","content":"Second explanation"},"children":[]}}'}
	done={true}
	blockId="second"
/>
<p>Text after the second view.</p>
