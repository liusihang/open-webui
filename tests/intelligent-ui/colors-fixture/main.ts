import { mount } from 'svelte';
import '../../../src/tailwind.css';
import './fixture.css';
import Fixture from './Fixture.svelte';
import { parseIntelligentUI } from '../../../src/lib/utils/intelligent-ui';
import { fixtureSource, contrastSource } from './spec';
const source =
	new URLSearchParams(location.search).get('fixture') === 'contrast'
		? contrastSource
		: fixtureSource;
const parsed = parseIntelligentUI(source, true);
if (parsed.error) console.error('Fixture parse error:', parsed.error);
mount(Fixture, { target: document.getElementById('app')! });
(window as any).__colorFixture = { parsed, source };
