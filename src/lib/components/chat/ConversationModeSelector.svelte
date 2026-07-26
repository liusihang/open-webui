<script lang="ts">
	import { getContext } from 'svelte';

	import type { ConversationMode } from './agentModeRequest';

	const i18n = getContext('i18n');

	export let mode: ConversationMode = 'chat';
	export let locked = false;
	export let agentAvailable = false;
	export let readOnly = false;
	export let onSelect: (mode: ConversationMode) => void = () => {};
	export let onCreateNew: (mode: ConversationMode) => void = () => {};

	const options: { value: ConversationMode; label: string }[] = [
		{ value: 'chat', label: 'Chat' },
		{ value: 'agent', label: 'Agent' }
	];

	const selectMode = (nextMode: ConversationMode) => {
		if (nextMode === mode || readOnly || (nextMode === 'agent' && !agentAvailable)) {
			return;
		}

		if (locked) {
			onCreateNew(nextMode);
			return;
		}

		onSelect(nextMode);
	};
</script>

<div
	role="radiogroup"
	aria-label={$i18n.t('Conversation mode')}
	class="flex h-9 items-center rounded-full border border-gray-200/80 bg-white/90 p-1 shadow-sm backdrop-blur dark:border-gray-700/80 dark:bg-gray-900/90"
>
	{#each options as option (option.value)}
		{@const unavailable = option.value === 'agent' && !agentAvailable}
		<button
			type="button"
			role="radio"
			aria-checked={mode === option.value}
			aria-disabled={readOnly || unavailable}
			disabled={readOnly || unavailable}
			title={unavailable ? $i18n.t('Agent Mode is not available') : undefined}
			class="min-w-20 rounded-full px-4 py-1 text-sm font-medium transition {mode === option.value
				? 'bg-gray-100 text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white'
				: 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100'} disabled:cursor-not-allowed disabled:opacity-50"
			on:click={() => selectMode(option.value)}
		>
			{$i18n.t(option.label)}
		</button>
	{/each}
</div>
