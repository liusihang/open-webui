<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { submitAgentRunUserInput } from '$lib/apis/agentRuns';
	import type { AgentRunEventPayload, AgentTranscriptUserInputPart } from './types';

	export let part: AgentTranscriptUserInputPart;
	export let agentRunId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');

	type UserInputField = {
		name: string;
		label: string;
		type: string;
		description: string | null;
		enumValues: string[];
		required: boolean;
	};

	type UserInputChoiceOption = {
		id: string;
		label: string;
		description: string | null;
		value: unknown;
		recommended: boolean;
	};

	type UserInputChoiceQuestion = {
		id: string;
		header: string | null;
		question: string;
		responseKey: string;
		options: UserInputChoiceOption[];
		allowCustom: boolean;
	};

	let values: Record<string, unknown> = {};
	let selectedOptions: Record<string, string> = {};
	let customAnswers: Record<string, string> = {};
	let currentQuestionIndex = 0;
	let submitting = false;
	let submitted = false;

	$: fields = schemaFields(part.requestedSchema);
	$: choiceQuestions = choiceQuestionsFromSchema(part.requestedSchema, part.message);
	$: activeQuestion = currentQuestion(choiceQuestions, currentQuestionIndex);
	$: if (choiceQuestions.length > 0 && currentQuestionIndex >= choiceQuestions.length) {
		currentQuestionIndex = choiceQuestions.length - 1;
	}
	$: canSubmitChoiceAnswer =
		choiceQuestions.length === 0 ||
		choiceQuestions.every(
			(question) =>
				Boolean(selectedOptions[question.id]) ||
				Boolean((customAnswers[question.id] ?? '').trim())
		);
	$: terminalText = terminalStatusText(part.status);

	const submit = async (status: 'accepted' | 'declined' | 'cancelled') => {
		if (!agentRunId || submitting || part.status !== 'pending') {
			return;
		}
		if (status === 'accepted' && choiceQuestions.length > 0 && !canSubmitChoiceAnswer) {
			return;
		}
		submitting = true;
		try {
			await submitAgentRunUserInput(localStorage.getItem('token') ?? '', agentRunId, part.userInputId, {
				status,
				content: status === 'accepted' ? collectAcceptedContent() : undefined
			});
			submitted = true;
		} catch (error) {
			toast.error(`${error}`);
			submitting = false;
			return;
		}
		submitting = false;
	};

	const collectAcceptedContent = () => {
		if (choiceQuestions.length > 0) {
			return collectChoiceContent(choiceQuestions, selectedOptions, customAnswers);
		}
		return collectContent(fields, values);
	};

	const collectContent = (inputFields: UserInputField[], inputValues: Record<string, unknown>) => {
		if (inputFields.length === 1 && inputFields[0].name === 'response') {
			return { response: inputValues.response ?? '' };
		}
		return inputFields.reduce<Record<string, unknown>>((content, field) => {
			content[field.name] = inputValues[field.name] ?? defaultValueForType(field.type);
			return content;
		}, {});
	};

	const setFieldValue = (name: string, value: unknown) => {
		values = { ...values, [name]: value };
	};

	const selectChoice = (questionId: string, optionId: string) => {
		selectedOptions = { ...selectedOptions, [questionId]: optionId };
		customAnswers = { ...customAnswers, [questionId]: '' };
	};

	const setCustomAnswer = (questionId: string, value: string) => {
		customAnswers = { ...customAnswers, [questionId]: value };
		if (value.trim()) {
			const { [questionId]: _removed, ...rest } = selectedOptions;
			selectedOptions = rest;
		}
	};

	const goToQuestion = (delta: number) => {
		if (choiceQuestions.length <= 1) {
			return;
		}
		currentQuestionIndex = Math.min(
			choiceQuestions.length - 1,
			Math.max(0, currentQuestionIndex + delta)
		);
	};

	const checkboxValue = (event: Event): boolean => (event.currentTarget as HTMLInputElement).checked;

	const textInputValue = (event: Event): string => (event.currentTarget as HTMLInputElement).value;

	const schemaFields = (schema: AgentRunEventPayload | null): UserInputField[] => {
		const required = new Set(
			Array.isArray(schema?.required)
				? schema.required.filter((item): item is string => typeof item === 'string')
				: []
		);
		const properties = isPlainObject(schema?.properties) ? schema.properties : null;
		if (!properties) {
			return [
				{
					name: 'response',
					label: 'Response',
					type: typeof schema?.type === 'string' ? schema.type : 'string',
					description: null,
					enumValues: [],
					required: true
				}
			];
		}
		return Object.entries(properties).map(([name, definition]) => {
			const field = isPlainObject(definition) ? definition : {};
			const enumValues = Array.isArray(field.enum)
				? field.enum.filter((item): item is string => typeof item === 'string')
				: [];
			return {
				name,
				label: stringValue(field.title) || name,
				type: stringValue(field.type) || 'string',
				description: stringValue(field.description),
				enumValues,
				required: required.has(name)
			};
		});
	};

	const terminalStatusText = (status: AgentTranscriptUserInputPart['status']) => {
		if (status === 'accepted') return $i18n.t('submitted');
		if (status === 'declined') return $i18n.t('declined');
		if (status === 'cancelled') return $i18n.t('cancelled');
		if (status === 'timeout') return $i18n.t('timed out');
		return $i18n.t('waiting');
	};

	const currentQuestion = (
		questions: UserInputChoiceQuestion[],
		index: number
	): UserInputChoiceQuestion | null => {
		if (questions.length === 0) {
			return null;
		}
		return questions[Math.min(index, questions.length - 1)];
	};

	const collectChoiceContent = (
		questions: UserInputChoiceQuestion[],
		selected: Record<string, string>,
		custom: Record<string, string>
	) =>
		questions.reduce<Record<string, unknown>>((content, question) => {
			const customAnswer = (custom[question.id] ?? '').trim();
			if (customAnswer) {
				content[question.responseKey] = customAnswer;
				content[`${question.responseKey}_source`] = 'custom';
				return content;
			}
			const option = question.options.find((candidate) => candidate.id === selected[question.id]);
			if (option) {
				content[question.responseKey] = option.value;
				content[`${question.responseKey}_label`] = option.label;
				content[`${question.responseKey}_source`] = 'option';
			}
			return content;
		}, {});

	const choiceQuestionsFromSchema = (
		schema: AgentRunEventPayload | null,
		fallbackMessage: string
	): UserInputChoiceQuestion[] => {
		if (!isPlainObject(schema)) {
			return [];
		}

		const directQuestions = arrayValue(schema.questions)
			.map((item, index) => choiceQuestionFromObject(item, index, fallbackMessage))
			.filter((item): item is UserInputChoiceQuestion => item !== null);
		if (directQuestions.length > 0) {
			return directQuestions;
		}

		const properties = isPlainObject(schema.properties) ? schema.properties : null;
		if (!properties) {
			return [];
		}

		return Object.entries(properties)
			.map(([name, definition], index) =>
				choiceQuestionFromProperty(name, definition, index, fallbackMessage)
			)
			.filter((item): item is UserInputChoiceQuestion => item !== null);
	};

	const choiceQuestionFromObject = (
		value: unknown,
		index: number,
		fallbackMessage: string
	): UserInputChoiceQuestion | null => {
		if (!isPlainObject(value)) {
			return null;
		}
		const options = arrayValue(value.options)
			.map((option, optionIndex) => choiceOptionFromValue(option, `${index}:${optionIndex}`))
			.filter((item): item is UserInputChoiceOption => item !== null);
		if (options.length === 0 && value.allow_custom === false && value.allowCustom === false) {
			return null;
		}
		const id = stringValue(value.id) || stringValue(value.name) || `question_${index + 1}`;
		return {
			id,
			header: stringValue(value.header) || stringValue(value.title),
			question:
				stringValue(value.question) ||
				stringValue(value.message) ||
				stringValue(value.prompt) ||
				fallbackMessage,
			responseKey: stringValue(value.response_key) || stringValue(value.responseKey) || id,
			options,
			allowCustom: value.allow_custom !== false && value.allowCustom !== false
		};
	};

	const choiceQuestionFromProperty = (
		name: string,
		definition: unknown,
		index: number,
		fallbackMessage: string
	): UserInputChoiceQuestion | null => {
		if (!isPlainObject(definition)) {
			return null;
		}
		const optionValues = [
			...arrayValue(definition.oneOf),
			...arrayValue(definition.anyOf),
			...arrayValue(definition.options),
			...(isPlainObject(definition.input) ? arrayValue(definition.input.options) : []),
			...arrayValue(definition.enum)
		];
		const options = optionValues
			.map((option, optionIndex) => choiceOptionFromValue(option, `${index}:${optionIndex}`))
			.filter((item): item is UserInputChoiceOption => item !== null);
		if (options.length === 0) {
			return null;
		}
		return {
			id: name,
			header: stringValue(definition.title),
			question: stringValue(definition.description) || fallbackMessage,
			responseKey: name,
			options,
			allowCustom: definition.allow_custom !== false && definition.allowCustom !== false
		};
	};

	const choiceOptionFromValue = (
		value: unknown,
		fallbackId: string
	): UserInputChoiceOption | null => {
		if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
			const label = String(value);
			return {
				id: fallbackId,
				label,
				description: null,
				value,
				recommended: label.toLowerCase().includes('recommended')
			};
		}
		if (!isPlainObject(value)) {
			return null;
		}
		const optionValue =
			value.value ??
			value.const ??
			(Array.isArray(value.enum) && value.enum.length > 0 ? value.enum[0] : undefined);
		const label =
			stringValue(value.label) ||
			stringValue(value.title) ||
			stringValue(value.name) ||
			(optionValue !== undefined ? String(optionValue) : null);
		if (!label) {
			return null;
		}
		return {
			id: stringValue(value.id) || fallbackId,
			label,
			description: stringValue(value.description),
			value: optionValue ?? label,
			recommended:
				value.recommended === true ||
				value.is_recommended === true ||
				label.toLowerCase().includes('recommended')
		};
	};

	const choiceLabel = (option: UserInputChoiceOption): string =>
		option.recommended && !option.label.toLowerCase().includes('recommended')
			? `${option.label} (Recommended)`
			: option.label;

	const arrayValue = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

	const stringValue = (value: unknown): string | null =>
		typeof value === 'string' && value.length > 0 ? value : null;

	const defaultValueForType = (type: string) => {
		if (type === 'boolean') return false;
		if (type === 'number' || type === 'integer') return 0;
		return '';
	};

	const isPlainObject = (value: unknown): value is AgentRunEventPayload =>
		typeof value === 'object' && value !== null && !Array.isArray(value);
</script>

<div
	class="agent-user-input-part"
	class:pending={part.status === 'pending'}
	class:terminal={part.status !== 'pending'}
	data-user-input-id={part.userInputId}
>
	<div class="agent-user-input-row">
		<span class="agent-user-input-icon" aria-hidden="true">{part.status === 'pending' ? '?' : '✓'}</span>
		<span class="agent-user-input-message">{part.message}</span>
		<span class="agent-user-input-status">{terminalText}</span>
	</div>

	{#if part.status === 'pending' && agentRunId}
		{#if activeQuestion}
			<form
				class="agent-user-choice-form"
				on:submit|preventDefault={() => {
					void submit('accepted');
				}}
			>
				<div class="agent-user-choice-panel">
					<div class="agent-user-choice-heading">
						<div class="agent-user-choice-title">
							{activeQuestion.header ?? activeQuestion.question}
						</div>
						{#if choiceQuestions.length > 1}
							<div class="agent-user-choice-pager" aria-label={$i18n.t('Question navigation')}>
								<button
									type="button"
									disabled={currentQuestionIndex === 0 || submitting}
									aria-label={$i18n.t('Previous question')}
									on:click={() => goToQuestion(-1)}
								>
									‹
								</button>
								<span>{currentQuestionIndex + 1} {$i18n.t('of')} {choiceQuestions.length}</span>
								<button
									type="button"
									disabled={currentQuestionIndex >= choiceQuestions.length - 1 || submitting}
									aria-label={$i18n.t('Next question')}
									on:click={() => goToQuestion(1)}
								>
									›
								</button>
							</div>
						{/if}
					</div>
					{#if activeQuestion.header && activeQuestion.header !== activeQuestion.question}
						<div class="agent-user-choice-question">{activeQuestion.question}</div>
					{/if}
					<div class="agent-user-choice-options">
						{#each activeQuestion.options as option, index (option.id)}
							<button
								type="button"
								class="agent-user-choice-option"
								class:selected={selectedOptions[activeQuestion.id] === option.id}
								disabled={submitting}
								on:click={() => selectChoice(activeQuestion.id, option.id)}
							>
								<span class="agent-user-choice-number">{index + 1}</span>
								<span class="agent-user-choice-copy">
									<span class="agent-user-choice-label">{choiceLabel(option)}</span>
									{#if option.description}
										<span class="agent-user-choice-description">{option.description}</span>
									{/if}
								</span>
							</button>
						{/each}
						{#if activeQuestion.allowCustom}
							<label class="agent-user-choice-custom">
								<span class="agent-user-choice-custom-icon" aria-hidden="true">✎</span>
								<input
									type="text"
									value={customAnswers[activeQuestion.id] ?? ''}
									disabled={submitting}
									placeholder={$i18n.t('Tell the agent how to adjust')}
									on:input={(event) => setCustomAnswer(activeQuestion.id, textInputValue(event))}
								/>
							</label>
						{/if}
					</div>
				</div>
				<div class="agent-user-input-actions choice-actions">
					{#if submitted}
						<span class="agent-user-input-submitted" role="status">
							{$i18n.t('Submitted')}. {$i18n.t('Waiting for agent\u2026')}
						</span>
					{:else}
						<button type="button" disabled={submitting} on:click={() => void submit('declined')}>
							{$i18n.t('Skip')}
						</button>
						{#if part.allowCancel}
							<button type="button" disabled={submitting} on:click={() => void submit('cancelled')}>
								{$i18n.t('Cancel')}
							</button>
						{/if}
						<button type="submit" disabled={submitting || !canSubmitChoiceAnswer}>
							{$i18n.t('Continue')}
						</button>
					{/if}
				</div>
			</form>
		{:else}
			<form
				class="agent-user-input-form"
				on:submit|preventDefault={() => {
					void submit('accepted');
				}}
			>
				{#each fields as field (field.name)}
					<label class="agent-user-input-field">
						<span class="agent-user-input-label">{field.label}{field.required ? '' : ' (optional)'}</span>
						{#if field.enumValues.length > 0}
							<select bind:value={values[field.name]} disabled={submitting} required={field.required}>
								{#each field.enumValues as option}
									<option value={option}>{option}</option>
								{/each}
							</select>
						{:else if field.type === 'boolean'}
							<input
								type="checkbox"
								checked={values[field.name] === true}
								disabled={submitting}
								on:change={(event) => setFieldValue(field.name, checkboxValue(event))}
							/>
						{:else if field.type === 'number' || field.type === 'integer'}
							<input
								type="number"
								bind:value={values[field.name]}
								disabled={submitting}
								required={field.required}
							/>
						{:else}
							<textarea
								rows="2"
								bind:value={values[field.name]}
								disabled={submitting}
								required={field.required}
							></textarea>
						{/if}
						{#if field.description}
							<span class="agent-user-input-description">{field.description}</span>
						{/if}
					</label>
				{/each}
				<div class="agent-user-input-actions">
					{#if submitted}
						<span class="agent-user-input-submitted" role="status">
							{$i18n.t('Submitted')}. {$i18n.t('Waiting for agent\u2026')}
						</span>
					{:else}
						<button type="submit" disabled={submitting}>{$i18n.t('Submit')}</button>
						<button type="button" disabled={submitting} on:click={() => void submit('declined')}>
							{$i18n.t('Decline')}
						</button>
						{#if part.allowCancel}
							<button type="button" disabled={submitting} on:click={() => void submit('cancelled')}>
								{$i18n.t('Cancel')}
							</button>
						{/if}
					{/if}
				</div>
			</form>
		{/if}
	{:else if part.content !== null && part.content !== undefined}
		<pre class="agent-user-input-content">{JSON.stringify(part.content, null, 2)}</pre>
	{/if}

</div>

<style>
	.agent-user-input-part {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding: 0.35rem 0;
		border-radius: 0.4rem;
		margin: 0.15rem 0;
		background: transparent;
		border: 1px solid transparent;
	}
	.agent-user-input-part.pending {
		background: transparent;
		border-color: transparent;
	}
	.agent-user-input-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-user-input-icon {
		color: var(--amber-600, #d97706);
		font-size: 0.7rem;
		font-weight: 700;
	}
	.agent-user-input-message {
		color: var(--gray-800, #1f2937);
		font-weight: 500;
	}
	.agent-user-input-status {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
	}
	.agent-user-input-form {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 0.45rem 0.55rem;
		border-radius: 0.4rem;
		background: var(--gray-50, #f9fafb);
	}
	.agent-user-choice-form {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
	}
	.agent-user-choice-panel {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 0.45rem 0.55rem 0.5rem;
		border-radius: 0.5rem;
		background: var(--gray-50, #f9fafb);
		border: 1px solid var(--gray-100, #f3f4f6);
	}
	.agent-user-choice-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.agent-user-choice-title {
		min-width: 0;
		color: var(--gray-900, #111827);
		font-size: 0.82rem;
		font-weight: 650;
		line-height: 1.35;
	}
	.agent-user-choice-question {
		color: var(--gray-500, #6b7280);
		font-size: 0.72rem;
		line-height: 1.35;
	}
	.agent-user-choice-pager {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		flex: 0 0 auto;
		color: var(--gray-500, #6b7280);
		font-size: 0.72rem;
	}
	.agent-user-choice-pager button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.35rem;
		height: 1.35rem;
		border: 0;
		border-radius: 9999px;
		background: transparent;
		color: var(--gray-500, #6b7280);
		font-size: 1.1rem;
		line-height: 1;
	}
	.agent-user-choice-pager button:disabled {
		opacity: 0.35;
	}
	.agent-user-choice-options {
		display: flex;
		flex-direction: column;
		gap: 0.28rem;
	}
	.agent-user-choice-option,
	.agent-user-choice-custom {
		display: grid;
		grid-template-columns: 1.35rem minmax(0, 1fr);
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		min-height: 2rem;
		border: 0;
		border-radius: 0.45rem;
		background: color-mix(in srgb, var(--gray-100, #f3f4f6) 88%, transparent);
		color: var(--gray-700, #374151);
		padding: 0.35rem 0.5rem;
		text-align: left;
	}
	.agent-user-choice-option {
		cursor: pointer;
	}
	.agent-user-choice-option:hover:not(:disabled),
	.agent-user-choice-option.selected {
		background: var(--gray-100, #f3f4f6);
	}
	.agent-user-choice-number,
	.agent-user-choice-custom-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.15rem;
		height: 1.15rem;
		border-radius: 9999px;
		background: var(--gray-200, #e5e7eb);
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
		font-weight: 650;
	}
	.agent-user-choice-option.selected .agent-user-choice-number {
		background: var(--gray-950, #030712);
		color: var(--gray-50, #f9fafb);
	}
	.agent-user-choice-copy {
		display: flex;
		align-items: baseline;
		gap: 0.45rem;
		min-width: 0;
	}
	.agent-user-choice-label {
		color: var(--gray-900, #111827);
		font-size: 0.76rem;
		font-weight: 600;
		line-height: 1.3;
	}
	.agent-user-choice-description {
		min-width: 0;
		color: var(--gray-500, #6b7280);
		font-size: 0.72rem;
		line-height: 1.3;
	}
	.agent-user-choice-custom input {
		min-width: 0;
		width: 100%;
		border: 0;
		outline: none;
		background: transparent;
		color: var(--gray-800, #1f2937);
		font-size: 0.76rem;
	}
	.agent-user-choice-custom input::placeholder {
		color: var(--gray-500, #6b7280);
	}
	.agent-user-input-field {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.72rem;
		color: var(--gray-700, #374151);
	}
	.agent-user-input-label {
		font-weight: 500;
	}
	.agent-user-input-description {
		color: var(--gray-500, #6b7280);
		font-size: 0.65rem;
	}
	.agent-user-input-field textarea,
	.agent-user-input-field input,
	.agent-user-input-field select {
		width: 100%;
		border-radius: 0.25rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--white, #ffffff);
		color: var(--gray-800, #1f2937);
		font-size: 0.72rem;
		padding: 0.3rem 0.4rem;
	}
	.agent-user-input-actions {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}
	.agent-user-input-actions.choice-actions {
		justify-content: flex-end;
		padding: 0 0.1rem;
	}
	.agent-user-input-actions button {
		border-radius: 0.25rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--white, #ffffff);
		color: var(--gray-700, #374151);
		font-size: 0.68rem;
		font-weight: 500;
		padding: 0.25rem 0.45rem;
	}
	.agent-user-input-actions button[type='submit'],
	.agent-user-input-actions.choice-actions button[type='submit'] {
		background: var(--blue-500, #3b82f6);
		border-color: var(--blue-500, #3b82f6);
		color: var(--white, #ffffff);
	}
	.agent-user-input-actions button:disabled {
		opacity: 0.55;
	}
	.agent-user-input-submitted {
		font-size: 0.68rem;
		color: var(--gray-500, #6b7280);
	}
	.agent-user-input-content {
		margin: 0;
		white-space: pre-wrap;
		font-size: 0.68rem;
		color: var(--gray-600, #4b5563);
	}
</style>
