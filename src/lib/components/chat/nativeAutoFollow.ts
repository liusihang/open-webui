type NativeScrollContainer = {
	scrollTop: number;
	readonly scrollHeight: number;
};

export const armNativeScrollAnchor = ({
	shouldFollow,
	getContainer,
	requestFrame,
	cancelFrame,
	frameCount = 3
}: {
	shouldFollow: () => boolean;
	getContainer: () => NativeScrollContainer | null | undefined;
	requestFrame: (callback: FrameRequestCallback) => number;
	cancelFrame: (frameId: number) => void;
	frameCount?: number;
}) => {
	let cancelled = false;
	let frameId: number | null = null;
	let remainingFrames = Math.max(1, frameCount);

	const runFrame = () => {
		frameId = null;
		if (cancelled || !shouldFollow()) return;

		const container = getContainer();
		if (!container) return;
		container.scrollTop = container.scrollHeight;
		remainingFrames -= 1;
		if (remainingFrames > 0) {
			frameId = requestFrame(runFrame);
		}
	};

	frameId = requestFrame(runFrame);

	return () => {
		cancelled = true;
		if (frameId !== null) {
			cancelFrame(frameId);
			frameId = null;
		}
	};
};

export const nativeScrollAnchor = (node: HTMLElement, following: boolean) => {
	let shouldFollow = following;
	const cancel = armNativeScrollAnchor({
		shouldFollow: () => shouldFollow,
		getContainer: () => node.closest('#messages-container') as HTMLElement | null,
		requestFrame: (callback) => requestAnimationFrame(callback),
		cancelFrame: (frameId) => cancelAnimationFrame(frameId),
		frameCount: 3
	});

	return {
		update(nextFollowing: boolean) {
			shouldFollow = nextFollowing;
		},
		destroy: cancel
	};
};
