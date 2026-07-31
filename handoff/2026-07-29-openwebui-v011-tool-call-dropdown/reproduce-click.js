(async () => {
	const errors = [];
	const rejections = [];
	const errorHandler = (event) => {
		errors.push({
			message: event.message,
			filename: event.filename,
			line: event.lineno,
			column: event.colno,
			error: String(event.error ?? '')
		});
	};
	const rejectionHandler = (event) => {
		rejections.push(String(event.reason ?? ''));
	};
	window.addEventListener('error', errorHandler);
	window.addEventListener('unhandledrejection', rejectionHandler);

	const button = Array.from(document.querySelectorAll('button[aria-label="展开/收起详情"]')).find(
		(element) => element.textContent?.includes('get_current_timestamp')
	);
	if (!button) throw new Error('tool detail summary button not found');

	const before = button.getAttribute('aria-expanded');
	button.click();
	await new Promise((resolve) => setTimeout(resolve, 750));
	const after = button.isConnected ? button.getAttribute('aria-expanded') : 'detached';
	const toolCallBodies = document.querySelectorAll('.tool-call-body').length;

	window.removeEventListener('error', errorHandler);
	window.removeEventListener('unhandledrejection', rejectionHandler);
	return { before, after, toolCallBodies, errors, rejections };
})();
