<script lang="ts">
	import { fade } from 'svelte/transition';
	import {
		getTextTokenSegments,
		getTextTokenShouldPreserveStreamingMarkup
	} from './textToken';

	export let token;
	export let done = true;

	let preserveStreamingMarkup = false;
	let texts = [];

	$: preserveStreamingMarkup = getTextTokenShouldPreserveStreamingMarkup(
		preserveStreamingMarkup,
		done
	);
	$: texts = preserveStreamingMarkup ? getTextTokenSegments(token?.raw ?? '') : [];
</script>

{#if !preserveStreamingMarkup}
	{token?.raw}
{:else}
	{#each texts as text, idx (idx)}
		<span class="" in:fade={{ duration: done ? 0 : 100 }}>
			{text}
		</span>
	{/each}
{/if}
