(async () => {
	const errors = [];
	const rejections = [];
	const errorHandler = (event) => errors.push(String(event.error ?? event.message ?? ''));
	const rejectionHandler = (event) => rejections.push(String(event.reason ?? ''));
	window.addEventListener('error', errorHandler);
	window.addEventListener('unhandledrejection', rejectionHandler);

	const expandedSummary = Array.from(
		document.querySelectorAll('button[aria-label="展开/收起详情"][aria-expanded="true"]')
	).find((element) => element.textContent?.includes('get_current_timestamp'));
	if (!expandedSummary) throw new Error('expanded multi-tool summary not found');

	const group = expandedSummary.parentElement;
	if (!group) throw new Error('expanded multi-tool group not found');
	const headers = Array.from(group.querySelectorAll('.tool-call-header'));
	const cards = [];
	for (const header of headers) {
		const container = header.closest('.tool-call-container');
		if (!container) throw new Error('tool-call header has no container');
		if (!container.querySelector('.tool-call-body')) {
			header.click();
			await new Promise((resolve) => setTimeout(resolve, 350));
		}
		const body = container.querySelector('.tool-call-body');
		const text = body?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
		cards.push({
			header: header.textContent?.replace(/\s+/g, ' ').trim() ?? '',
			hasBody: Boolean(body),
			hasOutput: /Output|输出/.test(text),
			textLength: text.length
		});
	}

	window.removeEventListener('error', errorHandler);
	window.removeEventListener('unhandledrejection', rejectionHandler);
	return { cardCount: cards.length, cards, errors, rejections };
})();
