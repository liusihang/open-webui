(() => {
	const body = document.querySelector('.tool-call-body');
	const container = document.querySelector('#messages-container');
	if (!body || !container) throw new Error('tool body or messages container missing');
	const bodyRect = body.getBoundingClientRect();
	const containerRect = container.getBoundingClientRect();
	container.scrollTop += bodyRect.top - containerRect.top - container.clientHeight / 3;
	return {
		scrollTop: container.scrollTop,
		bodyRect: body.getBoundingClientRect().toJSON(),
		containerRect: container.getBoundingClientRect().toJSON()
	};
})();
