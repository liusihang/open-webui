import type { Spec } from '$lib/utils/intelligent-ui';
/** Host callbacks are supplied only by an assistant message, never by model data. */
export const INTELLIGENT_UI_HOST = Symbol('intelligent-ui-host');
export const INTELLIGENT_UI_BLOCK = Symbol('intelligent-ui-block');
export type InteractionValue = string | number | boolean;
export type InteractionState = Record<string, InteractionValue>;
export interface IntelligentUIHost {
	canWrite: () => boolean;
	canFollowUp: () => boolean;
	read: (block: string) => InteractionState;
	write: (block: string, state: InteractionState) => void;
	followUp: (prompt: string) => Promise<void>;
}
export interface IntelligentUIBlockContext {
	isDone: () => boolean;
	spec: () => Spec | null;
	shared: (stateKey: string) => InteractionValue | undefined;
	writeShared: (stateKey: string, value: InteractionValue) => void;
	read: (key: string, fallback: InteractionValue) => InteractionValue;
	write: (key: string, value: InteractionValue) => void;
	canFollowUp: () => boolean;
	followUp: (prompt: string) => Promise<void>;
}

/** The owner endpoint also labels writable chats "continue"; route readOnly is the share guard. */
export function canPersistIntelligentUI(
	message: { done?: boolean; user_id?: string },
	readOnly: boolean,
	currentUserId?: string
): boolean {
	return (
		!readOnly &&
		message.done === true &&
		Boolean(currentUserId) &&
		(!message.user_id || message.user_id === currentUserId)
	);
}
