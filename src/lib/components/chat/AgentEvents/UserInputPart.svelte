<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { submitAgentRunUserInput } from '$lib/apis/agentRuns';
	import type { AgentRunEventPayload, AgentTranscriptUserInputPart } from './types';
	import AgentDetailSection from './AgentDetailSection.svelte';

	export let part: AgentTranscriptUserInputPart;
	export let agentRunId: string | null = null;

	type UserInputField = {
		name: string;
		label: string;
		type: string;
		description: string | null;
		enumValues: string[];
		required: boolean;
	};

	let values: Record<string, unknown> = {};
	let submitting = false;

	$: fields = schemaFields(part.requestedSchema);
	$: terminalText = terminalStatusText(part.status);

	const submit = async (status: 'accepted' | 'declined' | 'cancelled') => {
		if (!agentRunId || submitting || part.status !== 'pending') {
			return;
		}
		submitting = true;
		try {
			await submitAgentRunUserInput(localStorage.getItem('token') ?? '', agentRunId, part.userInputId, {
				status,
				content: status === 'accepted' ? collectContent(fields, values) : undefined
			});
		} catch (error) {
			toast.error(`${error}`);
			submitting = false;
			return;
		}
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

	const checkboxValue = (event: Event): boolean => (event.currentTarget as HTMLInputElement).checked;

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
		if (status === 'accepted') return 'submitted';
		if (status === 'declined') return 'declined';
		if (status === 'cancelled') return 'cancelled';
		if (status === 'timeout') return 'timed out';
		return 'waiting';
	};

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
				<button type="submit" disabled={submitting}>Submit</button>
				<button type="button" disabled={submitting} on:click={() => void submit('declined')}>
					Decline
				</button>
				{#if part.allowCancel}
					<button type="button" disabled={submitting} on:click={() => void submit('cancelled')}>
						Cancel
					</button>
				{/if}
			</div>
		</form>
	{:else if part.content !== null && part.content !== undefined}
		<pre class="agent-user-input-content">{JSON.stringify(part.content, null, 2)}</pre>
	{/if}

	<AgentDetailSection
		label="Input details"
		payload={part.details}
		metadata={part.metadata}
		open={part.defaultExpanded}
	/>
</div>

<style>
	.agent-user-input-part {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding: 0.4rem 0.5rem;
		border-radius: 0.3rem;
		margin: 0.15rem 0;
		background: var(--gray-50, #f9fafb);
		border-left: 2px solid var(--gray-300, #d1d5db);
	}
	.agent-user-input-part.pending {
		background: var(--amber-50, #fffbeb);
		border-left-color: var(--amber-300, #fcd34d);
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
	.agent-user-input-actions button {
		border-radius: 0.25rem;
		border: 1px solid var(--gray-200, #e5e7eb);
		background: var(--white, #ffffff);
		color: var(--gray-700, #374151);
		font-size: 0.68rem;
		font-weight: 500;
		padding: 0.25rem 0.45rem;
	}
	.agent-user-input-actions button[type='submit'] {
		background: var(--gray-900, #111827);
		border-color: var(--gray-900, #111827);
		color: var(--white, #ffffff);
	}
	.agent-user-input-actions button:disabled {
		opacity: 0.55;
	}
	.agent-user-input-content {
		margin: 0;
		white-space: pre-wrap;
		font-size: 0.68rem;
		color: var(--gray-600, #4b5563);
	}
</style>
