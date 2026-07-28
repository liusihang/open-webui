<script lang="ts">
	import type { ReasoningEffort } from './agentModeRequest';

	type ReasoningEffortOption = {
		value: ReasoningEffort;
		label: string;
	};

	export let value: ReasoningEffort = 'medium';
	export let options: ReasoningEffortOption[] = [];
	export let disabled = false;
	export let ariaLabel = 'Reasoning Effort';
	export let onChange: (value: ReasoningEffort) => void = () => {};
	export let onCommit: (value: ReasoningEffort) => void = () => {};

	let sliderElement: HTMLDivElement;
	let dragging = false;

	$: selectedIndex = Math.max(
		0,
		options.findIndex((option) => option.value === value)
	);
	$: selectedOption = options[selectedIndex];
	$: progress = options.length > 1 ? (selectedIndex / (options.length - 1)) * 100 : 0;
	$: controlDisabled = disabled || options.length === 0;

	const setIndex = (nextIndex: number, commit = false) => {
		if (controlDisabled) return;

		const index = Math.min(Math.max(nextIndex, 0), options.length - 1);
		const option = options[index];
		if (!option) return;

		if (value !== option.value) {
			value = option.value;
			onChange(value);
		}

		if (commit) {
			onCommit(value);
		}
	};

	const indexFromPointer = (clientX: number) => {
		const rect = sliderElement.getBoundingClientRect();
		const ratio = rect.width > 0 ? (clientX - rect.left) / rect.width : 0;
		return Math.round(Math.min(Math.max(ratio, 0), 1) * (options.length - 1));
	};

	const handlePointerDown = (event: PointerEvent) => {
		if (controlDisabled) return;
		dragging = true;
		sliderElement.setPointerCapture?.(event.pointerId);
		setIndex(indexFromPointer(event.clientX));
	};

	const handlePointerMove = (event: PointerEvent) => {
		if (!dragging) return;
		setIndex(indexFromPointer(event.clientX));
	};

	const handlePointerUp = (event: PointerEvent) => {
		if (!dragging) return;
		setIndex(indexFromPointer(event.clientX), true);
		dragging = false;
		sliderElement.releasePointerCapture?.(event.pointerId);
	};

	const handleKeydown = (event: KeyboardEvent) => {
		if (controlDisabled) return;

		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			setIndex(selectedIndex - 1, true);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			setIndex(selectedIndex + 1, true);
		} else if (event.key === 'Home') {
			event.preventDefault();
			setIndex(0, true);
		} else if (event.key === 'End') {
			event.preventDefault();
			setIndex(options.length - 1, true);
		}
	};
</script>

<div class="reasoning-effort-control" data-disabled={controlDisabled}>
	<div
		bind:this={sliderElement}
		role="slider"
		tabindex={controlDisabled ? -1 : 0}
		aria-label={ariaLabel}
		aria-disabled={controlDisabled}
		aria-valuemin={0}
		aria-valuemax={options.length - 1}
		aria-valuenow={selectedIndex}
		aria-valuetext={selectedOption?.label ?? ''}
		class="reasoning-effort-slider rounded-full outline-hidden focus-visible:ring-2 focus-visible:ring-violet-500/35 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-gray-850"
		data-dragging={dragging}
		on:pointerdown={handlePointerDown}
		on:pointermove={handlePointerMove}
		on:pointerup={handlePointerUp}
		on:pointercancel={() => (dragging = false)}
		on:keydown={handleKeydown}
	>
		<div class="reasoning-effort-track bg-gray-100 dark:bg-gray-800">
			<div
				class="reasoning-effort-range bg-violet-500 dark:bg-violet-400"
				style="width: {progress}%"
			></div>

			{#each options as option, index (option.value)}
				<span
					aria-hidden="true"
					class="reasoning-effort-tick {index <= selectedIndex
						? 'bg-white/80'
						: 'bg-gray-400/60 dark:bg-gray-500/70'}"
					data-selected={index === selectedIndex}
					style="left: {options.length > 1 ? (index / (options.length - 1)) * 100 : 0}%"
				></span>
			{/each}
		</div>

		<span
			aria-hidden="true"
			class="reasoning-effort-thumb border border-gray-300 bg-gray-50 shadow-sm dark:border-gray-600 dark:bg-gray-100"
			style="left: {progress}%"
		></span>
	</div>
</div>

<style>
	.reasoning-effort-control {
		padding: 0.125rem 0.5rem;
	}

	.reasoning-effort-control[data-disabled='true'] {
		opacity: 0.5;
	}

	.reasoning-effort-slider {
		position: relative;
		display: flex;
		align-items: center;
		height: 2rem;
		touch-action: none;
		cursor: pointer;
		user-select: none;
	}

	.reasoning-effort-control[data-disabled='true'] .reasoning-effort-slider {
		cursor: default;
	}

	.reasoning-effort-slider[data-dragging='true'] {
		cursor: grabbing;
	}

	.reasoning-effort-track {
		position: relative;
		height: 1.25rem;
		width: 100%;
		overflow: hidden;
		border-radius: 9999px;
		box-shadow: inset 0 0 0 1px rgb(0 0 0 / 0.05);
	}

	.reasoning-effort-range {
		position: absolute;
		inset-block: 0;
		inset-inline-start: 0;
		border-radius: 9999px;
		transition: width 180ms cubic-bezier(0.16, 1, 0.3, 1);
	}

	.reasoning-effort-tick {
		position: absolute;
		top: 50%;
		width: 0.25rem;
		height: 0.25rem;
		border-radius: 9999px;
		transform: translate(-50%, -50%);
		transition:
			background-color 160ms ease,
			transform 160ms cubic-bezier(0.16, 1, 0.3, 1);
	}

	.reasoning-effort-tick[data-selected='true'] {
		transform: translate(-50%, -50%) scale(1.35);
	}

	.reasoning-effort-thumb {
		position: absolute;
		top: 50%;
		width: 1.5rem;
		height: 1.5rem;
		border-radius: 9999px;
		transform: translate(-50%, -50%);
		transition: left 180ms cubic-bezier(0.16, 1, 0.3, 1);
	}

	@media (prefers-reduced-motion: reduce) {
		.reasoning-effort-range,
		.reasoning-effort-tick,
		.reasoning-effort-thumb {
			transition: none;
		}
	}
</style>
