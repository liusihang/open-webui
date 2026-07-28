import { existsSync, readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

const componentUrl = (name: string) => new URL(`./${name}`, import.meta.url);
const readSource = (name: string) => readFileSync(componentUrl(name), 'utf8');
const readLocale = (locale: string) =>
	JSON.parse(
		readFileSync(new URL(`../../i18n/locales/${locale}/translation.json`, import.meta.url), 'utf8')
	);

describe('composer model and reasoning presentation contract', () => {
	it.each(['ComposerModelSettings.svelte', 'ReasoningEffortSlider.svelte'])(
		'adds and compiles %s',
		(component) => {
			const filename = componentUrl(component);
			expect(existsSync(filename)).toBe(true);
			if (!existsSync(filename)) {
				return;
			}

			expect(() => compile(readFileSync(filename, 'utf8'), { filename: filename.pathname })).not.toThrow();
		}
	);

	it('uses one composer pill instead of the native reasoning select', () => {
		const input = readSource('MessageInput.svelte');

		expect(input).toContain("import ComposerModelSettings from './ComposerModelSettings.svelte'");
		expect(input).toContain('<ComposerModelSettings');
		expect(input).not.toContain('aria-label="思考深度"');
		expect(input).not.toContain('发散性思考 (12400)');
	});

	it('keeps the existing model selector single-select only', () => {
		const selector = readSource('ModelSelector.svelte');
		const selectorPrimitive = readSource('ModelSelector/Selector.svelte');

		expect(selector).not.toContain('aria-label="Add Model"');
		expect(selector).not.toContain('aria-label="Remove Model"');
		expect(selector).not.toContain('$user?.permissions?.chat?.multiple_models');
		expect(selector).toContain('selectedLabel={$mobile ? compactSelectedModelLabel : null}');
		expect(selectorPrimitive).toContain('export let selectedLabel: string | null = null');
		expect(selectorPrimitive).toContain('{selectedLabel ?? selectedModel.label}');
	});

	it('normalizes effective composer capabilities to one model', () => {
		const input = readSource('MessageInput.svelte');

		expect(input).toContain('resolveConversationModeRequestModels');
		expect(input).toMatch(
			/selectedModelIds\s*=\s*[\s\S]*atSelectedModel[\s\S]*resolveConversationModeRequestModels\([\s\S]*selectedModels,[\s\S]*'chat'[\s\S]*\)/
		);
		expect(input).not.toContain('selectedModels.length > 1');
	});

	it('provides an accessible four-stop reasoning slider', () => {
		const filename = componentUrl('ReasoningEffortSlider.svelte');
		expect(existsSync(filename)).toBe(true);
		if (!existsSync(filename)) {
			return;
		}

		const slider = readFileSync(filename, 'utf8');
		expect(slider).toContain('role="slider"');
		expect(slider).toContain('aria-valuemin={0}');
		expect(slider).toContain('aria-valuemax={options.length - 1}');
		expect(slider).toContain("event.key === 'ArrowLeft'");
		expect(slider).toContain("event.key === 'ArrowRight'");
		expect(slider).toContain('@media (prefers-reduced-motion: reduce)');
	});

	it('combines model and effort inside one compact menu', () => {
		const filename = componentUrl('ComposerModelSettings.svelte');
		expect(existsSync(filename)).toBe(true);
		if (!existsSync(filename)) {
			return;
		}

		const settings = readFileSync(filename, 'utf8');
		expect(settings).toContain("import Selector from './ModelSelector/Selector.svelte'");
		expect(settings).toContain("import ReasoningEffortSlider from './ReasoningEffortSlider.svelte'");
		expect(settings).toContain('aria-haspopup="menu"');
		expect(settings).toContain('bind:value={reasoningEffort}');
		expect(settings).toContain('{$i18n.t(\'Model\')}');
		expect(settings).toContain('{$i18n.t(\'Reasoning Effort\')}');
	});

	it('provides the approved reasoning labels in English and Simplified Chinese', () => {
		const english = readLocale('en-US');
		const chinese = readLocale('zh-CN');

		expect(chinese['Reasoning Effort']).toBe('思考强度');
		expect(chinese['Model and reasoning settings']).toBe('模型与思考设置');
		expect(chinese['Light reasoning']).toBe('轻度');
		expect(chinese['Standard reasoning']).toBe('标准');
		expect(chinese['Deep reasoning']).toBe('深度');
		expect(chinese['Extra deep reasoning']).toBe('极深');
		expect(chinese['This model does not support adjustable reasoning effort']).toBe(
			'该模型不支持调整思考强度'
		);

		for (const key of [
			'Model and reasoning settings',
			'Light reasoning',
			'Standard reasoning',
			'Deep reasoning',
			'Extra deep reasoning',
			'This model does not support adjustable reasoning effort'
		]) {
			expect(english).toHaveProperty(key);
		}
	});
});
