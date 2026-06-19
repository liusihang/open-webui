<script lang="ts">
	import { getContext } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');
</script>

<div
	class="agent-run-thinking-state my-2 flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400"
	aria-live="polite"
>
	<Spinner className="size-4" />
	<span>{$i18n.t('正在思考...')}</span>
</div>
