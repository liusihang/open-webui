/** Real Chromium security smoke. Usage: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/intelligent-ui/app-sandbox.chromium.mjs */
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { pathToFileURL } from 'node:url';
const moduleName = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(
	moduleName.startsWith('/') ? pathToFileURL(moduleName).href : moduleName
);
const bundle = await build({
	entryPoints: ['src/lib/utils/intelligent-ui/app-sandbox.ts'],
	bundle: true,
	write: false,
	format: 'iife',
	globalName: 'AppSandbox',
	platform: 'browser'
});
const browser = await chromium.launch({
	executablePath: process.env.CHROMIUM_PATH || chromium.executablePath(),
	headless: true,
	args: ['--no-sandbox']
});
try {
	const page = await browser.newPage();
	const external = [];
	await page.route('https://blocked.example/**', (route) => {
		external.push(route.request().url());
		return route.abort();
	});
	await page.setContent('<!doctype html><title>App boundary smoke</title>');
	await page.addScriptTag({ content: bundle.outputFiles[0].text });
	const token = '0123456789abcdef';
	await page.evaluate(
		({ token }) => {
			window.writes = [];
			window.ready = false;
			const iframe = document.createElement('iframe');
			iframe.sandbox = 'allow-scripts';
			iframe.id = 'app';
			iframe.srcdoc = AppSandbox.buildAppDocument(
				{
					title: 'Threshold demo',
					stateKeys: ['threshold'],
					html: '<div id="bar">Bar</div><p id="label">Waiting</p><canvas id="plot" width="120" height="60"></canvas><button id="add">Increase</button><p id="network"></p><p id="access"></p><img src="https://blocked.example/image"><iframe src="https://blocked.example/frame"></iframe><a href="https://blocked.example/link">Link</a>',
					css: '#bar{height:12px;background:#336699}body{background-image:url(https://blocked.example/css)}',
					js: `
ui.onState(state => { const n=Number(state.threshold)||0; ui.setText('label','Threshold '+n);ui.setStyle('bar','width',n+'%');ui.draw('plot',[['clear'],['rect',0,0,n,30,'#336699']]); });
ui.on('add','click',()=>ui.setState('threshold',(Number(ui.getState('threshold'))||0)+10));
fetch('https://blocked.example/fetch').then(()=>ui.setText('network','BAD')).catch(()=>ui.setText('network','blocked'));
try { importScripts('https://blocked.example/import.js'); } catch {}
try { new WebSocket('wss://blocked.example/socket'); } catch {}
ui.setText('access',typeof window+':'+typeof document+':'+typeof parent);
try { self.location='https://blocked.example/navigation'; } catch {}
ui.setState('undeclared','rejected');
`
				},
				token
			);
			window.addEventListener('message', (e) => {
				if (e.data?.type === 'ready' && e.data?.token === token) {
					window.ready = true;
					iframe.contentWindow.postMessage(
						{ channel: 'intelligent-ui-app', token, type: 'state', state: { threshold: 40 } },
						'*'
					);
				}
				const write = AppSandbox.acceptAppMessage(e, iframe.contentWindow, token, ['threshold']);
				if (write) window.writes.push(write);
			});
			document.body.append(iframe);
		},
		{ token }
	);
	await page.waitForFunction(() => window.ready === true);
	const app = page.frameLocator('#app');
	await app.locator('#label').filter({ hasText: 'Threshold 40' }).waitFor();
	assert.equal(await app.locator('#bar').evaluate((n) => n.style.width), '40%');
	assert.deepEqual(
		await app
			.locator('#plot')
			.evaluate((n) => Array.from(n.getContext('2d').getImageData(10, 10, 1, 1).data)),
		[51, 102, 153, 255]
	);
	assert.equal(await app.locator('#network').textContent(), 'blocked');
	assert.equal(await app.locator('#access').textContent(), 'undefined:undefined:undefined');
	assert.equal(await app.locator('img,iframe,a').count(), 0);
	await app.locator('#add').click();
	await page.waitForFunction(() =>
		window.writes.some((w) => w.key === 'threshold' && w.value === 50)
	);
	assert.deepEqual(await page.evaluate(() => window.writes), [{ key: 'threshold', value: 50 }]);
	await page.evaluate(
		({ token }) =>
			document
				.querySelector('#app')
				.contentWindow.postMessage(
					{ channel: 'intelligent-ui-app', token, type: 'state', state: { threshold: 70 } },
					'*'
				),
		{ token }
	);
	await app.locator('#label').filter({ hasText: 'Threshold 70' }).waitFor();
	assert.equal(await app.locator('#bar').evaluate((n) => n.style.width), '70%');
	await page.waitForTimeout(500);
	assert.deepEqual(external, [], 'No HTTP request may escape HTML, CSS, or worker code');
	assert.ok(page.frames()[1].url() === 'about:srcdoc', 'App did not navigate');
	console.log(
		'PASS Chromium: opaque iframe; worker-only JS; linked label, bar, canvas, write bridge; HTML/CSS/fetch/import/navigation denied.'
	);
} finally {
	await browser.close();
}
