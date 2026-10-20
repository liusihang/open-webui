<script lang="ts">
	import { setContext } from 'svelte';
	import IntelligentUIBlock from '$lib/components/chat/Messages/IntelligentUI/IntelligentUIBlock.svelte';
	import {
		INTELLIGENT_UI_HOST,
		type IntelligentUIHost,
		type InteractionState
	} from '$lib/components/chat/Messages/IntelligentUI/context';
	import { createTestI18n } from '$lib/components/chat/Messages/IntelligentUI/test-i18n';
	import { fixtureSource, contrastSource } from './spec';
	const source =
		new URLSearchParams(location.search).get('fixture') === 'contrast'
			? contrastSource
			: fixtureSource;
	setContext('i18n', createTestI18n('en-US'));
	let saved = $state<Record<string, InteractionState>>({});
	setContext<IntelligentUIHost>(INTELLIGENT_UI_HOST, {
		canWrite: () => true,
		canFollowUp: () => false,
		read: (block) => saved[block] ?? {},
		write: (block, state) => {
			saved[block] = state;
		},
		followUp: async () => {}
	});
	let dark = $state(document.documentElement.classList.contains('dark'));
	function toggleTheme() {
		dark = !dark;
		document.documentElement.classList.toggle('dark', dark);
	}
</script>

<div class="fixture-shell">
	<header class="fixture-nav">
		<div class="fixture-brand">
			<span class="fixture-mark" aria-hidden="true">O</span>Open WebUI<span class="fixture-divider"
			></span><span class="fixture-caption">Intelligent UI</span>
		</div>
		<button class="fixture-theme" onclick={toggleTheme} aria-label="Toggle color theme"
			>{dark ? 'Light mode' : 'Dark mode'}</button
		>
	</header>
	<main>
		<div class="fixture-kicker">NATIVE COMPONENTS · FLEXIBLE COLORS</div>
		<h1>Energy operations</h1>
		<p class="fixture-intro">
			A connected view of clean energy, site performance, and the next steps.
		</p>
		<div class="fixture-meta">
			<span class="fixture-status"></span>Illustrative data<span>•</span>Weekly review<span>•</span
			>October 2026
		</div>
		<IntelligentUIBlock {source} done={true} blockId="color-fixture" />
		<footer>
			Rendered with the production Intelligent UI component catalog. All controls stay local.
		</footer>
	</main>
	<output data-fixture-state hidden>{JSON.stringify(saved)}</output>
</div>
