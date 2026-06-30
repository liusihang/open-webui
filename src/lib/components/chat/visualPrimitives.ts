export type ChatVisualTone = 'muted' | 'running' | 'success' | 'warning' | 'danger';

export const chatThinBorderClass = 'border border-gray-100/80 dark:border-gray-800/80';

export const chatMutedSurfaceClass = [
	'rounded-lg',
	chatThinBorderClass,
	'bg-gray-50/70 text-gray-700 dark:bg-gray-900/30 dark:text-gray-200'
].join(' ');

export const chatActionButtonClass = [
	'inline-flex h-7 w-7 items-center justify-center rounded-md',
	'text-gray-500 transition',
	'hover:bg-gray-100 hover:text-gray-900',
	'dark:text-gray-400 dark:hover:bg-gray-850 dark:hover:text-gray-100',
	'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300/70',
	'dark:focus-visible:ring-gray-700/70',
	'disabled:pointer-events-none disabled:opacity-40'
].join(' ');

export const chatCollapsibleHeaderClass = [
	'flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left',
	'text-[13px] text-gray-600 transition',
	'hover:bg-gray-50 hover:text-gray-900',
	'dark:text-gray-300 dark:hover:bg-gray-850/70 dark:hover:text-gray-100',
	'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300/70',
	'dark:focus-visible:ring-gray-700/70'
].join(' ');

export const chatMonoBlockClass = [
	'max-h-40 overflow-auto whitespace-pre-wrap rounded-md',
	chatThinBorderClass,
	'bg-gray-50/70 p-2 font-mono text-xs text-gray-700',
	'dark:bg-gray-900/40 dark:text-gray-200'
].join(' ');

export const chatTerminalClass = [
	'max-h-72 overflow-auto rounded-lg border border-gray-800',
	'bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100',
	'shadow-sm dark:border-gray-800 dark:bg-gray-950'
].join(' ');

const toneAliases: Record<string, ChatVisualTone> = {
	'approval-requested': 'warning',
	'approval-responded': 'success',
	approved: 'success',
	cancelled: 'danger',
	completed: 'success',
	danger: 'danger',
	denied: 'danger',
	done: 'success',
	error: 'danger',
	failed: 'danger',
	'input-available': 'running',
	'input-streaming': 'running',
	muted: 'muted',
	'output-available': 'success',
	'output-denied': 'danger',
	'output-error': 'danger',
	pending: 'warning',
	rejected: 'danger',
	running: 'running',
	success: 'success',
	warning: 'warning',
	'waiting-approval': 'warning',
	waiting_approval: 'warning'
};

const badgeToneClass: Record<ChatVisualTone, string> = {
	muted:
		'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-800 dark:bg-gray-900/40 dark:text-gray-300',
	running:
		'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900/60 dark:bg-blue-950/20 dark:text-blue-200',
	success:
		'border-green-200 bg-green-50 text-green-700 dark:border-green-900/60 dark:bg-green-950/20 dark:text-green-200',
	warning:
		'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-100',
	danger:
		'border-red-200 bg-red-50 text-red-700 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-200'
};

const dotToneClass: Record<ChatVisualTone, string> = {
	muted: 'bg-gray-300 dark:bg-gray-600',
	running: 'bg-blue-500 dark:bg-blue-400 animate-pulse',
	success: 'bg-green-500 dark:bg-green-400',
	warning: 'bg-amber-500 dark:bg-amber-400 animate-pulse',
	danger: 'bg-red-500 dark:bg-red-400'
};

export const resolveChatVisualTone = (state: string | null | undefined): ChatVisualTone => {
	if (!state) return 'muted';
	return toneAliases[state.toLowerCase()] ?? 'muted';
};

export const chatBadgeClass = (state?: string | null) =>
	[
		'inline-flex h-5 max-w-full items-center rounded-md border px-1.5',
		'text-[11px] font-medium leading-none',
		badgeToneClass[resolveChatVisualTone(state)]
	].join(' ');

export const chatStatusDotClass = (state?: string | null) =>
	['inline-block size-1.5 shrink-0 rounded-full', dotToneClass[resolveChatVisualTone(state)]].join(
		' '
	);
