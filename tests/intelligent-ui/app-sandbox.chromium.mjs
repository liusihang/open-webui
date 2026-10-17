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
async function runSmoke(parentMode) {
	const page = await browser.newPage();
	try {
		const external = [];
		// Capture navigation from before app creation; frame-list order is not an identity contract.
		const navigations = [];
		const frameIds = new Map();
		const frameId = (frame) => {
			if (!frameIds.has(frame)) frameIds.set(frame, frameIds.size + 1);
			return frameIds.get(frame);
		};
		page.on('framenavigated', (frame) =>
			navigations.push({ frame, id: frameId(frame), url: frame.url() })
		);
		await page.route('https://blocked.example/**', (route) => {
			external.push(route.request().url());
			return route.abort();
		});
		if (parentMode === 'about:blank') {
			await page.setContent('<!doctype html><title>App boundary smoke</title>');
			assert.equal(await page.evaluate(() => location.origin), 'null');
		} else {
			// A realistic non-opaque parent origin, fulfilled locally without a network request.
			await page.route('https://ui.example.test/', (route) =>
				route.fulfill({
					status: 200,
					contentType: 'text/html',
					body: '<!doctype html><title>App boundary smoke</title>'
				})
			);
			await page.goto('https://ui.example.test/');
			assert.equal(await page.evaluate(() => location.origin), 'https://ui.example.test');
		}
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
		const appElement = await page.locator('#app').elementHandle();
		const appFrame = await appElement.contentFrame();
		assert.ok(appFrame, 'The #app element must own an attached frame');
		const documentIdentity = await appFrame.evaluate(() => {
			window.__smokeDocumentId = 'intelligent-ui-document-' + Math.random().toString(36).slice(2);
			return { href: location.href, marker: window.__smokeDocumentId };
		});
		const diagnostics = () =>
			JSON.stringify({
				parentMode,
				appFrameId: frameId(appFrame),
				appFrameIndex: page.frames().indexOf(appFrame),
				appFrameURL: appFrame.url(),
				allFrames: page
					.frames()
					.map((frame, index) => ({ id: frameId(frame), index, url: frame.url() })),
				appNavigations: navigations
					.filter((entry) => entry.frame === appFrame)
					.map((entry) => entry.url),
				documentIdentity
			});
		console.log('Initial app frame diagnostics:', diagnostics());
		assert.equal(
			documentIdentity.href,
			'about:srcdoc',
			`Initial app document must be srcdoc: ${diagnostics()}`
		);
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
		assert.equal(appFrame.isDetached(), false, `App frame detached: ${diagnostics()}`);
		assert.equal(
			await appElement.contentFrame(),
			appFrame,
			`App frame identity changed: ${diagnostics()}`
		);
		const finalIdentity = await appFrame.evaluate(() => ({
			href: location.href,
			marker: window.__smokeDocumentId
		}));
		assert.deepEqual(
			finalIdentity,
			documentIdentity,
			`App navigated or replaced its document: ${diagnostics()}`
		);
		const appNavigations = navigations
			.filter((entry) => entry.frame === appFrame)
			.map((entry) => entry.url);
		assert.ok(
			appNavigations.every((url) => ['', 'about:blank', 'about:srcdoc'].includes(url)),
			`Unexpected app navigation: ${diagnostics()}`
		);
		console.log('App frame navigation diagnostics:', diagnostics());
		console.log(
			'PASS Chromium: opaque iframe; worker-only JS; linked label, bar, canvas, write bridge; HTML/CSS/fetch/import/navigation denied.'
		);
	} finally {
		await page.close();
	}
}
const failures = [];
try {
	for (const parentMode of ['about:blank', 'https']) {
		try {
			await runSmoke(parentMode);
		} catch (error) {
			console.error(`FAIL parent mode ${parentMode}:`, error);
			failures.push(error);
		}
	}
	if (failures.length) throw new AggregateError(failures, 'App sandbox smoke failed');
} finally {
	await browser.close();
}
