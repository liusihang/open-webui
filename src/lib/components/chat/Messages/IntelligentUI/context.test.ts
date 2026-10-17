import { describe, expect, it } from 'vitest';
import { canPersistIntelligentUI } from './context';

describe('interactive message ownership', () => {
	it('allows a reloaded owner chat even when its API share_mode is continue', () => {
		const chat = { share_mode: 'continue', message: { done: true, user_id: 'owner' } };
		expect(canPersistIntelligentUI(chat.message, false, 'owner')).toBe(true);
	});
	it('denies shared/read-only routes, foreign messages, in-flight messages and anonymous users', () => {
		expect(canPersistIntelligentUI({ done: true, user_id: 'owner' }, true, 'owner')).toBe(false);
		expect(canPersistIntelligentUI({ done: true, user_id: 'other' }, false, 'owner')).toBe(false);
		expect(canPersistIntelligentUI({ done: false, user_id: 'owner' }, false, 'owner')).toBe(false);
		expect(canPersistIntelligentUI({ done: true }, false)).toBe(false);
	});
	it('allows legacy owner messages with no per-message author', () => {
		expect(canPersistIntelligentUI({ done: true }, false, 'owner')).toBe(true);
	});
});
