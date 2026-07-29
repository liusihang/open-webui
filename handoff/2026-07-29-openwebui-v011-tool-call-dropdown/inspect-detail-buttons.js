Array.from(document.querySelectorAll('button[aria-label="展开/收起详情"]')).map(
	(button, index) => ({
		index,
		expanded: button.getAttribute('aria-expanded'),
		text: button.textContent?.trim(),
		connected: button.isConnected,
		html: button.outerHTML.slice(0, 500)
	})
);
