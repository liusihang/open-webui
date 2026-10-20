/**
 * Real Chromium integration + screenshot runner, never a mock image.
 * Start the fixture with: npx vite --config tests/intelligent-ui/colors-fixture/vite.config.ts
 * Then: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs CHROMIUM_PATH=/usr/bin/chromium node tests/intelligent-ui/colors-fixture/capture.mjs
 */
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const moduleName = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(
	moduleName.startsWith('/') ? pathToFileURL(moduleName).href : moduleName
);
const destination = resolve(process.env.ARTIFACT_DIR || 'artifacts/intelligent-ui-colors');
const url = process.env.FIXTURE_URL || 'http://127.0.0.1:4174/';
await mkdir(destination, { recursive: true });
const browser = await chromium.launch({
	headless: true,
	executablePath: process.env.CHROMIUM_PATH || chromium.executablePath(),
	args: ['--no-sandbox']
});
const report = {
	browser: `Chromium ${browser.version()}`,
	fixture: url,
	checks: [],
	screenshots: [],
	contrast: []
};
async function inspectContrast(locator, description, minimum = 4.5) {
	const sample = await locator.evaluate((element) => {
		const parse = (value) => {
			const n = value.match(/[\d.]+/g)?.map(Number) || [];
			return [n[0] || 0, n[1] || 0, n[2] || 0, n.length > 3 ? n[3] : 1];
		};
		const merge = (foreground, background, alpha) =>
			foreground.slice(0, 3).map((v, i) => v * alpha + background[i] * (1 - alpha));
		const lineage = [];
		for (let el = element; el; el = el.parentElement) lineage.unshift(el);
		let background = [255, 255, 255];
		let opacity = 1;
		for (const el of lineage) {
			const style = getComputedStyle(el);
			const color = parse(style.backgroundColor);
			background = merge(color, background, color[3]);
			opacity *= Number(style.opacity);
		}
		const style = getComputedStyle(element);
		const text = parse(element instanceof SVGElement ? style.fill : style.color);
		const foreground = merge(text, background, text[3] * opacity);
		const luminance = (rgb) =>
			rgb
				.map((v) => v / 255)
				.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
				.reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
		const a = luminance(foreground),
			b = luminance(background);
		return {
			foreground,
			background,
			opacity,
			ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
		};
	});
	report.contrast.push({ description, minimum, ...sample });
	assert.ok(
		sample.ratio >= minimum - 0.01,
		`${description}: contrast ${sample.ratio.toFixed(2)} is below ${minimum}`
	);
}
try {
	for (const width of [1280, 390])
		for (const theme of ['light', 'dark']) {
			const page = await browser.newPage({
				viewport: { width, height: width === 1280 ? 1000 : 844 },
				deviceScaleFactor: 1,
				colorScheme: theme
			});
			const errors = [];
			page.on('pageerror', (error) => errors.push(error.message));
			page.on('console', (message) => {
				if (message.type() === 'error') errors.push(message.text());
			});
			await page.goto(`${url}?theme=${theme}`, { waitUntil: 'networkidle' });
			await page.locator('[data-chart="output"] [data-chart-point]').first().waitFor();
			assert.equal(await page.evaluate(() => window.__colorFixture.parsed.error), null);
			assert.equal(await page.locator('[data-chart="output"] [data-chart-point]').count(), 3);
			assert.equal(await page.locator('[data-chart="mix"] [data-chart-point]').count(), 3);
			const overflow = await page.evaluate(() => ({
				width: innerWidth,
				client: document.documentElement.clientWidth,
				scroll: document.documentElement.scrollWidth
			}));
			assert.ok(
				overflow.scroll <= overflow.client,
				`${theme}/${width}: document overflow ${JSON.stringify(overflow)}`
			);
			const chartViewport = await page
				.locator('[data-chart="output"] .chart-viewport')
				.evaluate((el) => ({
					client: el.clientWidth,
					scroll: el.scrollWidth,
					overflow: getComputedStyle(el).overflowX,
					tabindex: el.getAttribute('tabindex')
				}));
			assert.equal(chartViewport.overflow, 'auto');
			assert.equal(chartViewport.tabindex, '0');
			if (width === 1280)
				assert.equal(
					chartViewport.scroll,
					chartViewport.client,
					'Two-column desktop Cartesian plot remains fully visible'
				);
			if (width === 390)
				assert.ok(
					chartViewport.scroll > chartViewport.client,
					'Mobile Cartesian plot scrolls within its own viewport'
				);
			assert.equal(
				await page
					.locator('[data-chart="mix"] .chart-viewport')
					.evaluate((el) => el.scrollWidth <= el.clientWidth),
				true,
				'Pie remains fluid'
			);
			const points = page.locator('[data-chart="output"] [data-chart-point]');
			await points.first().focus();
			await page.keyboard.press('ArrowRight');
			assert.match(
				await page.locator('[data-chart="output"] [data-chart-tooltip]').textContent(),
				/Porto: 68/
			);
			await page.keyboard.press('Escape');
			await page.getByRole('button', { name: 'Set target to 85%', exact: true }).click();
			await page.getByText('85%', { exact: true }).first().waitFor();
			assert.match(await page.locator('[data-fixture-state]').textContent(), /"shared:target":85/);
			const slider = page.getByRole('slider');
			await slider.focus();
			await page.keyboard.press('ArrowLeft');
			assert.equal(await slider.inputValue(), '80');
			assert.match(await page.locator('[data-fixture-state]').textContent(), /"shared:target":80/);
			const checkAppearance = await page
				.getByRole('checkbox', { name: 'Include demand review' })
				.evaluate((el) => ({
					appearance: getComputedStyle(el).appearance,
					accent: getComputedStyle(el).accentColor
				}));
			assert.equal(checkAppearance.appearance, 'auto');
			assert.notEqual(checkAppearance.accent, 'auto');
			assert.notEqual(checkAppearance.accent, 'rgb(37, 99, 235)');
			const table = page.locator('[data-table="operations"]');
			const cell = table.locator('[data-row-id="porto"][data-column="status"]');
			const initialCell = await cell.evaluate((el) => ({
				background: getComputedStyle(el).backgroundColor,
				color: getComputedStyle(el).color
			}));
			await table.getByRole('button', { name: /Site/ }).first().click();
			assert.deepEqual(
				await cell.evaluate((el) => ({
					background: getComputedStyle(el).backgroundColor,
					color: getComputedStyle(el).color
				})),
				initialCell
			);
			const search = table.getByRole('searchbox');
			await search.fill('Porto');
			assert.equal(await table.locator('tbody tr').count(), 1);
			assert.deepEqual(
				await cell.evaluate((el) => ({
					background: getComputedStyle(el).backgroundColor,
					color: getComputedStyle(el).color
				})),
				initialCell
			);
			await search.fill('');
			await inspectContrast(cell, `${theme}/${width}: emphasized table cell`);
			// Restore the canonical review state before capturing each actual rendered page.
			await page.reload({ waitUntil: 'networkidle' });
			await page.locator('[data-chart="output"] [data-chart-point]').first().waitFor();
			await page.evaluate(() => document.fonts.ready);
			const path = resolve(destination, `native-colors-${theme}-${width}.png`);
			await page.screenshot({ path, fullPage: true, animations: 'disabled' });
			assert.deepEqual(errors, [], `${theme}/${width}: browser errors`);
			report.screenshots.push({ path, theme, width, ...overflow, chartViewport });
			report.checks.push(
				`${theme}/${width}: real parser and catalog; charts and pie; keyboard chart focus and tooltip; button and slider linked spans; table sort/search stable colors; no page overflow; no browser errors`
			);
			await page.close();
		}
	for (const width of [1280, 390])
		for (const theme of ['light', 'dark']) {
			const page = await browser.newPage({
				viewport: { width, height: 1000 },
				colorScheme: theme
			});
			await page.goto(`${url}?theme=${theme}&fixture=contrast`, { waitUntil: 'networkidle' });
			await page.getByText('Readable yellow span', { exact: true }).waitFor();
			assert.equal(await page.evaluate(() => window.__colorFixture.parsed.error), null);
			assert.equal(
				await page.evaluate(
					() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
				),
				true
			);
			for (const text of [
				'Inherited stack heading',
				'Readable yellow span',
				'Inherited input label',
				'Inherited checkbox label',
				'Description remains legible on the authored surface.',
				'Chart description remains readable.'
			]) {
				await inspectContrast(page.getByText(text, { exact: true }), `${theme}/${width}: ${text}`);
			}
			await inspectContrast(
				page.locator('[data-chart="chart"] [data-chart-point] rect'),
				`${theme}/${width}: chart fill matching requested background`,
				3
			);
			await page.screenshot({
				path: resolve(destination, `native-colors-contrast-${theme}-${width}.png`),
				fullPage: true,
				animations: 'disabled'
			});
			await page.close();
		}
	report.passed = true;
	console.log(
		`PASS: ${report.checks.length} viewport/theme runs, ${report.contrast.length} contrast checks.`
	);
} catch (error) {
	report.passed = false;
	report.error = String(error.stack || error);
	console.error(error);
	process.exitCode = 1;
} finally {
	await writeFile(resolve(destination, 'verification.json'), JSON.stringify(report, null, 2));
	await browser.close();
}
