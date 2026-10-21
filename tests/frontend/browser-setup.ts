// jsdom has no layout engine. Real-browser checks cover ResizeObserver geometry.
if (typeof ResizeObserver === 'undefined') {
	globalThis.ResizeObserver = class implements ResizeObserver {
		observe() {}
		unobserve() {}
		disconnect() {}
	};
}
