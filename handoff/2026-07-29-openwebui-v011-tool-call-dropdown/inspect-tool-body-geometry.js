(() => {
	const body = document.querySelector('.tool-call-body');
	if (!body) return null;
	const ancestors = [];
	let current = body.parentElement;
	while (current) {
		const style = getComputedStyle(current);
		if (/(auto|scroll)/.test(`${style.overflow} ${style.overflowY}`)) {
			ancestors.push({
				tag: current.tagName,
				id: current.id,
				className: current.className,
				scrollTop: current.scrollTop,
				scrollHeight: current.scrollHeight,
				clientHeight: current.clientHeight,
				rect: current.getBoundingClientRect().toJSON()
			});
		}
		current = current.parentElement;
	}
	return { rect: body.getBoundingClientRect().toJSON(), ancestors };
})();
