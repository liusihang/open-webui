<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { submitAgentRunUserInput } from '$lib/apis/agentRuns';
	import type { AgentTranscriptUserInputPart } from './types';
	import {
		collectAcceptedUserInputContent,
		fieldsWithoutChoiceQuestions,
		parseUserInputSchema,
		userInputDisplayEntries,
		userInputFieldErrors,
		type UserInputChoiceOption,
		type UserInputChoiceQuestion
	} from './userInputSchema';

	export let part: AgentTranscriptUserInputPart;
	export let agentRunId: string | null = null;

	type SubmissionStatus = 'accepted' | 'declined' | 'cancelled';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let values: Record<string, unknown> = {};
	let selectedOptions: Record<string, string> = {};
	let customAnswers: Record<string, string> = {};
	let currentQuestionIndex = 0;
	let submitting: SubmissionStatus | null = null;
	let submittedStatus: SubmissionStatus | null = null;
	let submitError: string | null = null;
	const idempotencyKeys = new Map<string, string>();

	$: parsedSchema = parseUserInputSchema(part.requestedSchema, part.message);
	$: fields = parsedSchema.fields;
	$: choiceQuestions = parsedSchema.questions;
	$: supplementalFields = fieldsWithoutChoiceQuestions(fields, choiceQuestions);
	$: fieldErrors = userInputFieldErrors(supplementalFields, values);
	$: displayEntries = part.content == null ? [] : userInputDisplayEntries(part.content);
	$: activeQuestion = currentQuestion(choiceQuestions, currentQuestionIndex);
	$: if (choiceQuestions.length > 0 && currentQuestionIndex >= choiceQuestions.length) {
		currentQuestionIndex = choiceQuestions.length - 1;
	}
	$: canSubmitChoiceAnswer =
		Object.keys(fieldErrors).length === 0 &&
		(choiceQuestions.length === 0 ||
			choiceQuestions.every(
				(question) =>
					Boolean(selectedOptions[question.id]) ||
					Boolean((customAnswers[question.id] ?? '').trim())
			));
	$: effectiveStatus = part.status === 'pending' && submittedStatus ? submittedStatus : part.status;
	$: terminalText = terminalStatusText(effectiveStatus);

	const submit = async (status: SubmissionStatus) => {
		if (!agentRunId || submitting || submittedStatus || part.status !== 'pending') {
			return;
		}
		if (status === 'accepted' && !canSubmitChoiceAnswer) {
			return;
		}

		submitError = null;
		submitting = status;
		try {
			const content =
				status === 'accepted'
					? collectAcceptedUserInputContent({
							fields,
							questions: choiceQuestions,
							selectedOptions,
							customAnswers,
							values
						})
					: undefined;
			const submissionFingerprint = status + ':' + JSON.stringify(content ?? null);
			const idempotencyKey =
				idempotencyKeys.get(submissionFingerprint) ??
				'user-input:' + part.userInputId + ':' + status + ':' + createSubmissionNonce();
			idempotencyKeys.set(submissionFingerprint, idempotencyKey);

			await submitAgentRunUserInput(
				localStorage.getItem('token') ?? '',
				agentRunId,
				part.userInputId,
				{
					status,
					content,
					idempotencyKey
				}
			);
			submittedStatus = status;
		} catch (error) {
			submitError = errorMessage(error);
		} finally {
			submitting = null;
		}
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
			const nextSelectedOptions = { ...selectedOptions };
			delete nextSelectedOptions[questionId];
			selectedOptions = nextSelectedOptions;
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

	const checkboxValue = (event: Event): boolean =>
		(event.currentTarget as HTMLInputElement).checked;

	const inputValue = (event: Event): string =>
		(event.currentTarget as HTMLInputElement | HTMLTextAreaElement).value;

	const numberValue = (event: Event): number | '' => {
		const value = (event.currentTarget as HTMLInputElement).value;
		return value === '' ? '' : Number(value);
	};

	const terminalStatusText = (status: AgentTranscriptUserInputPart['status']) => {
		if (status === 'accepted') return $i18n.t('submitted');
		if (status === 'declined') return $i18n.t('declined');
		if (status === 'cancelled') return $i18n.t('cancelled');
		if (status === 'timeout') return $i18n.t('timed out');
		if (status === 'stale') return $i18n.t('no longer available');
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

	const choiceLabel = (option: UserInputChoiceOption): string =>
		option.recommended ? option.label + ' (Recommended)' : option.label;

	const customAnswerInputId = (userInputId: string, questionId: string): string =>
		('agent-user-input-custom-' + userInputId + '-' + questionId).replace(/[^a-zA-Z0-9_-]/g, '-');

	const fieldErrorId = (userInputId: string, fieldName: string): string =>
		('agent-user-input-error-' + userInputId + '-' + fieldName).replace(/[^a-zA-Z0-9_-]/g, '-');

	const createSubmissionNonce = (): string =>
		typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
			? crypto.randomUUID()
			: String(Date.now()) + '-' + Math.random().toString(36).slice(2);

	const errorMessage = (error: unknown): string => {
		if (error instanceof Error) return error.message;
		if (typeof error === 'string') return error;
		if (typeof error === 'object' && error !== null && 'message' in error) {
			return String((error as { message: unknown }).message);
		}
		return $i18n.t('Unable to submit your response.');
	};
</script>

<div
	class="agent-user-input-part"
	class:pending={effectiveStatus === 'pending'}
	class:terminal={effectiveStatus !== 'pending'}
	data-user-input-id={part.userInputId}
>
	<div class="agent-user-input-row">
		<span class="agent-user-input-icon" aria-hidden="true"
			>{effectiveStatus === 'pending' ? '?' : '✓'}</span
		>
		<span class="agent-user-input-message">{part.message}</span>
		<span class="agent-user-input-status">{terminalText}</span>
	</div>
	{#if submitError}
		<p class="agent-user-input-error" role="alert">{submitError}</p>
	{/if}

	{#if part.status === 'pending' && agentRunId && !submittedStatus}
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
									disabled={currentQuestionIndex === 0 || submitting !== null}
									aria-label={$i18n.t('Previous question')}
									on:click={() => goToQuestion(-1)}
								>
									‹
								</button>
								<span>{currentQuestionIndex + 1} {$i18n.t('of')} {choiceQuestions.length}</span>
								<button
									type="button"
									disabled={currentQuestionIndex >= choiceQuestions.length - 1 ||
										submitting !== null}
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
								disabled={submitting !== null}
								aria-pressed={selectedOptions[activeQuestion.id] === option.id}
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
							<label
								class="agent-user-choice-custom"
								for={customAnswerInputId(part.userInputId, activeQuestion.id)}
							>
								<span class="agent-user-choice-custom-icon" aria-hidden="true">✎</span>
								<input
									id={customAnswerInputId(part.userInputId, activeQuestion.id)}
									type="text"
									value={customAnswers[activeQuestion.id] ?? ''}
									disabled={submitting !== null}
									aria-label={$i18n.t('Custom answer')}
									placeholder={$i18n.t('Tell the agent how to adjust')}
									on:input={(event) => setCustomAnswer(activeQuestion.id, inputValue(event))}
								/>
							</label>
						{/if}
					</div>
				</div>
				{#if supplementalFields.length > 0}
					<div class="agent-user-input-form agent-user-input-supplemental">
						{#each supplementalFields as field (field.name)}
							<label class="agent-user-input-field">
								<span class="agent-user-input-label"
									>{field.label}{field.required ? '' : ' (optional)'}</span
								>
								{#if field.type === 'boolean'}
									<input
										type="checkbox"
										checked={values[field.name] === true}
										disabled={submitting !== null}
										aria-invalid={Boolean(fieldErrors[field.name])}
										on:change={(event) => setFieldValue(field.name, checkboxValue(event))}
									/>
								{:else if field.type === 'number' || field.type === 'integer'}
									<input
										type="number"
										step={field.type === 'integer' ? '1' : 'any'}
										value={values[field.name] ?? ''}
										disabled={submitting !== null}
										aria-invalid={Boolean(fieldErrors[field.name])}
										aria-describedby={fieldErrors[field.name]
											? fieldErrorId(part.userInputId, field.name)
											: undefined}
										on:input={(event) => setFieldValue(field.name, numberValue(event))}
									/>
								{:else if field.type === 'array' || field.type === 'object'}
									<textarea
										rows="4"
										value={String(values[field.name] ?? '')}
										placeholder={field.type === 'array'
											? 'JSON array, e.g. ["draft", "final"]'
											: 'JSON object, e.g. {"enabled": true}'}
										disabled={submitting !== null}
										aria-invalid={Boolean(fieldErrors[field.name])}
										aria-describedby={fieldErrors[field.name]
											? fieldErrorId(part.userInputId, field.name)
											: undefined}
										on:input={(event) => setFieldValue(field.name, inputValue(event))}
									></textarea>
								{:else}
									<textarea
										rows="2"
										value={String(values[field.name] ?? '')}
										disabled={submitting !== null}
										aria-invalid={Boolean(fieldErrors[field.name])}
										aria-describedby={fieldErrors[field.name]
											? fieldErrorId(part.userInputId, field.name)
											: undefined}
										on:input={(event) => setFieldValue(field.name, inputValue(event))}
									></textarea>
								{/if}
								{#if field.description}
									<span class="agent-user-input-description">{field.description}</span>
								{/if}
								{#if fieldErrors[field.name]}
									<span
										class="agent-user-input-field-error"
										id={fieldErrorId(part.userInputId, field.name)}
										role="alert">{fieldErrors[field.name]}</span
									>
								{/if}
							</label>
						{/each}
					</div>
				{/if}
				<div class="agent-user-input-actions choice-actions">
					<button
						type="button"
						disabled={submitting !== null}
						on:click={() => void submit('declined')}
					>
						{$i18n.t('Skip')}
					</button>
					{#if part.allowCancel}
						<button
							type="button"
							disabled={submitting !== null}
							on:click={() => void submit('cancelled')}
						>
							{$i18n.t('Cancel')}
						</button>
					{/if}
					<button type="submit" disabled={submitting !== null || !canSubmitChoiceAnswer}>
						{$i18n.t('Continue')}
					</button>
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
						<span class="agent-user-input-label"
							>{field.label}{field.required ? '' : ' (optional)'}</span
						>
						{#if field.enumValues.length > 0}
							<select
								value={String(values[field.name] ?? '')}
								disabled={submitting !== null}
								aria-invalid={Boolean(fieldErrors[field.name])}
								aria-describedby={fieldErrors[field.name]
									? fieldErrorId(part.userInputId, field.name)
									: undefined}
								on:change={(event) => setFieldValue(field.name, inputValue(event))}
							>
								<option value="" disabled={field.required}>{$i18n.t('Select')}</option>
								{#each field.enumValues as option}
									<option value={option}>{option}</option>
								{/each}
							</select>
						{:else if field.type === 'boolean'}
							<input
								type="checkbox"
								checked={values[field.name] === true}
								disabled={submitting !== null}
								aria-invalid={Boolean(fieldErrors[field.name])}
								on:change={(event) => setFieldValue(field.name, checkboxValue(event))}
							/>
						{:else if field.type === 'number' || field.type === 'integer'}
							<input
								type="number"
								step={field.type === 'integer' ? '1' : 'any'}
								value={values[field.name] ?? ''}
								disabled={submitting !== null}
								aria-invalid={Boolean(fieldErrors[field.name])}
								aria-describedby={fieldErrors[field.name]
									? fieldErrorId(part.userInputId, field.name)
									: undefined}
								on:input={(event) => setFieldValue(field.name, numberValue(event))}
							/>
						{:else if field.type === 'array' || field.type === 'object'}
							<textarea
								rows="4"
								value={String(values[field.name] ?? '')}
								placeholder={field.type === 'array'
									? 'JSON array, e.g. ["draft", "final"]'
									: 'JSON object, e.g. {"enabled": true}'}
								disabled={submitting !== null}
								aria-invalid={Boolean(fieldErrors[field.name])}
								aria-describedby={fieldErrors[field.name]
									? fieldErrorId(part.userInputId, field.name)
									: undefined}
								on:input={(event) => setFieldValue(field.name, inputValue(event))}
							></textarea>
						{:else}
							<textarea
								rows="2"
								value={String(values[field.name] ?? '')}
								disabled={submitting !== null}
								aria-invalid={Boolean(fieldErrors[field.name])}
								aria-describedby={fieldErrors[field.name]
									? fieldErrorId(part.userInputId, field.name)
									: undefined}
								on:input={(event) => setFieldValue(field.name, inputValue(event))}
							></textarea>
						{/if}
						{#if field.description}
							<span class="agent-user-input-description">{field.description}</span>
						{/if}
						{#if fieldErrors[field.name]}
							<span
								class="agent-user-input-field-error"
								id={fieldErrorId(part.userInputId, field.name)}
								role="alert">{fieldErrors[field.name]}</span
							>
						{/if}
					</label>
				{/each}
				<div class="agent-user-input-actions">
					<button type="submit" disabled={submitting !== null || !canSubmitChoiceAnswer}
						>{$i18n.t('Submit')}</button
					>
					<button
						type="button"
						disabled={submitting !== null}
						on:click={() => void submit('declined')}
					>
						{$i18n.t('Decline')}
					</button>
					{#if part.allowCancel}
						<button
							type="button"
							disabled={submitting !== null}
							on:click={() => void submit('cancelled')}
						>
							{$i18n.t('Cancel')}
						</button>
					{/if}
				</div>
			</form>
		{/if}
	{:else if submittedStatus}
		<p class="agent-user-input-submitted" role="status">
			{$i18n.t('Submitted')}. {$i18n.t('Waiting for agent\u2026')}
		</p>
	{:else if part.content !== null && part.content !== undefined}
		<div class="agent-user-input-content">
			{#each displayEntries as entry (entry.label)}
				<div class="agent-user-input-content-row">
					<span class="agent-user-input-content-label">{entry.label}</span>
					<span class="agent-user-input-content-value">{entry.value}</span>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.agent-user-input-part {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding: 0.35rem 0;
		border-radius: 0.75rem;
		margin: 0.15rem 0;
		background: transparent;
		border: 1px solid transparent;
	}
	.agent-user-input-part.pending {
		padding: 0.65rem 0.7rem;
		background: var(--agent-transcript-attention-surface, #faf5ff);
		border-color: var(--agent-transcript-attention-border, #ddd6fe);
	}
	.agent-user-input-row {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.75rem;
	}
	.agent-user-input-icon {
		color: var(--agent-transcript-warning-color, #d97706);
		font-size: 0.7rem;
		font-weight: 700;
	}
	.agent-user-input-message {
		color: var(--agent-transcript-body-color, #1f2937);
		font-weight: 500;
	}
	.agent-user-input-status {
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.65rem;
	}
	.agent-user-input-error {
		margin: 0;
		font-size: 0.72rem;
		line-height: 1.4;
		color: var(--agent-transcript-danger-color, #b91c1c);
	}
	.agent-user-input-form {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 0.45rem 0.55rem;
		border-radius: 0.65rem;
		background: var(--agent-transcript-surface-color, #f9fafb);
		border: 1px solid var(--agent-transcript-border-color, #e5e7eb);
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
		background: var(--agent-transcript-surface-color, #f9fafb);
		border: 1px solid var(--agent-transcript-border-color, #e5e7eb);
	}
	.agent-user-choice-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.agent-user-choice-title {
		min-width: 0;
		color: var(--agent-transcript-body-color, #111827);
		font-size: 0.82rem;
		font-weight: 650;
		line-height: 1.35;
	}
	.agent-user-choice-question {
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.72rem;
		line-height: 1.35;
	}
	.agent-user-choice-pager {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		flex: 0 0 auto;
		color: var(--agent-transcript-muted-color, #6b7280);
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
	.agent-user-choice-pager button:focus-visible {
		outline: 2px solid var(--agent-transcript-focus-color, #8b5cf6);
		outline-offset: 1px;
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
		background: color-mix(
			in srgb,
			var(--agent-transcript-raised-surface, #f3f4f6) 88%,
			transparent
		);
		color: var(--agent-transcript-body-color, #374151);
		padding: 0.35rem 0.5rem;
		text-align: left;
	}
	.agent-user-choice-option {
		cursor: pointer;
	}
	.agent-user-choice-option:hover:not(:disabled),
	.agent-user-choice-option.selected {
		background: var(--agent-transcript-raised-surface, #f3f4f6);
		box-shadow: inset 0 0 0 1px var(--agent-transcript-attention-border, #ddd6fe);
	}
	.agent-user-choice-option:focus-visible,
	.agent-user-choice-custom:focus-within {
		outline: 2px solid var(--agent-transcript-focus-color, #8b5cf6);
		outline-offset: 1px;
	}
	.agent-user-choice-number,
	.agent-user-choice-custom-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.15rem;
		height: 1.15rem;
		border-radius: 9999px;
		background: var(--agent-transcript-border-color, #e5e7eb);
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.65rem;
		font-weight: 650;
	}
	.agent-user-choice-option.selected .agent-user-choice-number {
		background: var(--agent-transcript-accent-color, #7c3aed);
		color: #fafafa;
	}
	.agent-user-choice-copy {
		display: flex;
		align-items: baseline;
		gap: 0.45rem;
		min-width: 0;
	}
	.agent-user-choice-label {
		color: var(--agent-transcript-body-color, #111827);
		font-size: 0.76rem;
		font-weight: 600;
		line-height: 1.3;
	}
	.agent-user-choice-description {
		min-width: 0;
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.72rem;
		line-height: 1.3;
	}
	.agent-user-choice-custom input {
		min-width: 0;
		width: 100%;
		border: 0;
		outline: none;
		background: transparent;
		color: var(--agent-transcript-body-color, #1f2937);
		font-size: 0.76rem;
	}
	.agent-user-choice-custom input::placeholder {
		color: var(--agent-transcript-muted-color, #6b7280);
	}
	.agent-user-input-field {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.72rem;
		color: var(--agent-transcript-body-color, #374151);
	}
	.agent-user-input-label {
		font-weight: 500;
	}
	.agent-user-input-description {
		color: var(--agent-transcript-muted-color, #6b7280);
		font-size: 0.65rem;
	}
	.agent-user-input-field-error {
		color: var(--agent-transcript-danger-color, #b91c1c);
		font-size: 0.65rem;
	}
	.agent-user-input-field textarea,
	.agent-user-input-field input,
	.agent-user-input-field select {
		width: 100%;
		border-radius: 0.5rem;
		border: 1px solid var(--agent-transcript-border-color, #e5e7eb);
		background: var(--agent-transcript-surface-color, #f9f9f9);
		color: var(--agent-transcript-body-color, #1f2937);
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
		border-radius: 0.5rem;
		border: 1px solid var(--agent-transcript-border-color, #e5e7eb);
		background: var(--agent-transcript-surface-color, #f9f9f9);
		color: var(--agent-transcript-body-color, #374151);
		font-size: 0.72rem;
		font-weight: 500;
		padding: 0.34rem 0.58rem;
	}
	.agent-user-input-actions button[type='submit'],
	.agent-user-input-actions.choice-actions button[type='submit'] {
		background: var(--agent-transcript-accent-color, #7c3aed);
		border-color: var(--agent-transcript-accent-color, #7c3aed);
		color: #fafafa;
	}
	.agent-user-input-actions button:focus-visible {
		outline: 2px solid var(--agent-transcript-focus-color, #8b5cf6);
		outline-offset: 2px;
	}
	.agent-user-input-actions button:disabled {
		opacity: 0.55;
	}
	.agent-user-input-submitted {
		font-size: 0.68rem;
		color: var(--agent-transcript-muted-color, #6b7280);
	}
	.agent-user-input-content {
		display: grid;
		gap: 0.2rem;
		margin: 0.1rem 0 0 1.1rem;
		font-size: 0.7rem;
	}
	.agent-user-input-content-row {
		display: grid;
		grid-template-columns: minmax(4.5rem, auto) minmax(0, 1fr);
		gap: 0.55rem;
	}
	.agent-user-input-content-label {
		color: var(--agent-transcript-muted-color, #6b7280);
		font-weight: 500;
	}
	.agent-user-input-content-value {
		min-width: 0;
		color: var(--agent-transcript-body-color, #374151);
		overflow-wrap: anywhere;
	}
	@media (max-width: 640px) {
		.agent-user-choice-copy {
			align-items: flex-start;
			flex-direction: column;
			gap: 0.08rem;
		}
		.agent-user-input-actions.choice-actions {
			justify-content: stretch;
			flex-wrap: wrap;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.agent-user-choice-option,
		.agent-user-input-actions button {
			transition: none;
		}
	}
</style>
