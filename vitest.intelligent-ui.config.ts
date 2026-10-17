import { defineConfig, mergeConfig } from 'vitest/config';
import baseConfig from './vite.config';

// DOM tests must resolve Svelte's browser entry; ordinary SSR/unit tests retain the default entry.
export default mergeConfig(
	baseConfig,
	defineConfig({
		resolve: { conditions: ['browser'] },
		test: {
			environment: 'jsdom',
			include: ['src/lib/components/chat/Messages/IntelligentUI/*.browser.ts'],
			pool: 'forks',
			poolOptions: { forks: { singleFork: true } }
		}
	})
);
