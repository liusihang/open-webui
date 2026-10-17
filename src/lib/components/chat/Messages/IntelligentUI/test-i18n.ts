import { createInstance } from 'i18next';
import { writable } from 'svelte/store';
import english from '$lib/i18n/locales/en-US/translation.json';
import chinese from '$lib/i18n/locales/zh-CN/translation.json';
import traditionalChinese from '$lib/i18n/locales/zh-TW/translation.json';

/** Use bundled dictionaries and the application's interpolation/fallback rules. */
export function createTestI18n(language = 'en-US') {
	const instance = createInstance();
	void instance.init({
		lng: language,
		fallbackLng: 'en-US',
		initImmediate: false,
		keySeparator: false,
		nsSeparator: false,
		returnEmptyString: false,
		interpolation: { escapeValue: false },
		resources: {
			'en-US': { translation: english },
			'zh-CN': { translation: chinese },
			'zh-TW': { translation: traditionalChinese }
		}
	});
	return writable(instance);
}
