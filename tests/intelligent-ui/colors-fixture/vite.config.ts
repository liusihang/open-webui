import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('./', import.meta.url));
const repository = fileURLToPath(new URL('../../../', import.meta.url));
export default defineConfig({
	root,
	plugins: [svelte()],
	worker: { format: 'es' },
	resolve: { alias: { $lib: `${repository}src/lib` }, conditions: ['browser'] },
	define: {
		APP_VERSION: JSON.stringify('color-fixture'),
		APP_BUILD_HASH: JSON.stringify('fixture'),
		APP_BUILD_CHANNEL: JSON.stringify('test')
	},
	server: { host: '127.0.0.1', port: 4174, strictPort: true, fs: { allow: [repository] } },
	build: { outDir: `${repository}artifacts/intelligent-ui-colors/build`, emptyOutDir: true },
	css: { postcss: `${repository}postcss.config.js` },
	optimizeDeps: { exclude: ['@json-render/svelte'] }
});
