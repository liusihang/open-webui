type ResizeObserverLike = Pick<ResizeObserver, 'observe' | 'disconnect'>;

type ResizeObserverConstructor = new (callback: ResizeObserverCallback) => ResizeObserverLike;

export type AutoFollowResizeOptions = {
	shouldFollow: () => boolean;
	scheduleScroll: () => void;
	ResizeObserverClass?: ResizeObserverConstructor;
};

export type AutoFollowFrameSchedulerOptions<Target> = {
	shouldFollow: () => boolean;
	getTarget: () => Target | null | undefined;
	scroll: (target: Target) => void;
	requestFrame: (callback: FrameRequestCallback) => number;
	cancelFrame: (frameId: number) => void;
	frameCount?: number;
};

export const autoFollowResize = (node: Element, options: AutoFollowResizeOptions) => {
	let currentOptions = options;
	const Observer = currentOptions.ResizeObserverClass ?? ResizeObserver;
	const observer = new Observer(() => {
		if (currentOptions.shouldFollow()) {
			currentOptions.scheduleScroll();
		}
	});

	observer.observe(node);

	return {
		update(nextOptions: AutoFollowResizeOptions) {
			currentOptions = nextOptions;
		},
		destroy() {
			observer.disconnect();
		}
	};
};

export const createAutoFollowFrameScheduler = <Target>({
	shouldFollow,
	getTarget,
	scroll,
	requestFrame,
	cancelFrame,
	frameCount = 3
}: AutoFollowFrameSchedulerOptions<Target>) => {
	let destroyed = false;
	let frameId: number | null = null;
	let scheduledTarget: Target | null = null;
	let remainingFrames = 0;

	const clearSchedule = () => {
		scheduledTarget = null;
		remainingFrames = 0;
	};

	const runFrame = () => {
		frameId = null;
		const target = scheduledTarget;
		if (destroyed || target === null || !shouldFollow() || getTarget() !== target) {
			clearSchedule();
			return;
		}

		scroll(target);
		remainingFrames -= 1;
		if (remainingFrames > 0 && !destroyed && shouldFollow() && getTarget() === target) {
			frameId = requestFrame(runFrame);
		} else {
			clearSchedule();
		}
	};

	return {
		schedule() {
			if (destroyed || frameId !== null) return;
			const target = getTarget();
			if (!shouldFollow() || target == null) return;

			scheduledTarget = target;
			remainingFrames = Math.max(1, frameCount);
			frameId = requestFrame(runFrame);
		},
		destroy() {
			destroyed = true;
			if (frameId !== null) {
				cancelFrame(frameId);
				frameId = null;
			}
			clearSchedule();
		}
	};
};
