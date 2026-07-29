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

	const summary = Array.from(document.querySelectorAll('button[aria-label="展开/收起详情"]')).find(
		(element) => element.textContent?.includes('get_current_timestamp')
	);
	if (!summary) throw new Error('tool detail summary button not found');

	const summaryBefore = summary.getAttribute('aria-expanded');
	summary.click();
	await new Promise((resolve) => setTimeout(resolve, 500));
	const summaryAfter = summary.getAttribute('aria-expanded');
	const toolHeader = document.querySelector('.tool-call-header');
	if (!toolHeader) throw new Error('expanded summary did not render a tool-call header');

	toolHeader.click();
	await new Promise((resolve) => setTimeout(resolve, 500));
	const body = document.querySelector('.tool-call-body');
	const bodyText = body?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

	window.removeEventListener('error', errorHandler);
	window.removeEventListener('unhandledrejection', rejectionHandler);
	return {
		summaryBefore,
		summaryAfter,
		toolHeaderText: toolHeader.textContent?.replace(/\s+/g, ' ').trim() ?? '',
		toolCallBodies: document.querySelectorAll('.tool-call-body').length,
		bodyText,
		hasInput: /Input|输入/.test(bodyText),
		hasOutput: /Output|输出/.test(bodyText),
		errors,
		rejections
	};
})();
