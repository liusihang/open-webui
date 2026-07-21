import { describe, expect, it } from 'vitest';

import {
	collectAcceptedUserInputContent,
	fieldsWithoutChoiceQuestions,
	parseUserInputSchema,
	requiredUserInputFieldsComplete,
	userInputFieldErrors,
	userInputDisplayEntries,
	type UserInputChoiceQuestion,
	type UserInputField
} from './userInputSchema';

describe('user input schema helpers', () => {
	it('parses Codex-style choices and keeps a custom answer path', () => {
		const parsed = parseUserInputSchema(
			{
				type: 'object',
				questions: [
					{
						id: 'scope',
						header: 'Scope',
						question: 'Which scope should be used?',
						response_key: 'scope',
						options: [
							{ id: 'small', label: 'Small', value: 'small', recommended: true },
							{ id: 'large', label: 'Large', value: 'large' }
						],
						allow_custom: true
					}
				]
			},
			'Choose a scope'
		);

		expect(parsed.questions).toEqual([
			expect.objectContaining({
				id: 'scope',
				responseKey: 'scope',
				allowCustom: true,
				options: [
					expect.objectContaining({ id: 'small', label: 'Small', recommended: true }),
					expect.objectContaining({ id: 'large', label: 'Large', recommended: false })
				]
			})
		]);
	});

	it.each([
		['enum', { type: 'string', enum: ['small', 'large'] }],
		[
			'oneOf',
			{
				type: 'string',
				oneOf: [
					{ const: 'small', title: 'Small' },
					{ const: 'large', title: 'Large' }
				]
			}
		]
	] as const)('does not add Other to standard JSON Schema %s choices', (_kind, definition) => {
		const parsed = parseUserInputSchema(
			{
				type: 'object',
				properties: { scope: definition },
				required: ['scope']
			},
			'Choose a scope'
		);

		expect(parsed.questions[0]).toMatchObject({ responseKey: 'scope', allowCustom: false });
	});

	it.each([
		['enum', { type: 'string', enum: ['small', 'large'] }],
		[
			'oneOf',
			{
				type: 'string',
				oneOf: [
					{ const: 'small', title: 'Small' },
					{ const: 'large', title: 'Large' }
				]
			}
		]
	] as const)('supports root-level JSON Schema %s choices without Other', (_kind, schema) => {
		const parsed = parseUserInputSchema(schema, 'Choose a scope');

		expect(parsed.questions[0]).toMatchObject({ responseKey: 'response', allowCustom: false });
	});

	it('allows Other only for explicit allow_custom or the Agent questions contract', () => {
		const explicit = parseUserInputSchema(
			{
				type: 'object',
				properties: {
					scope: { type: 'string', enum: ['small', 'large'], allow_custom: true }
				}
			},
			'Choose a scope'
		);
		const agentQuestion = parseUserInputSchema(
			{
				questions: [
					{
						id: 'scope',
						question: 'Choose a scope',
						options: ['small', 'large']
					}
				]
			},
			'Choose a scope'
		);

		expect(explicit.questions[0]?.allowCustom).toBe(true);
		expect(agentQuestion.questions[0]?.allowCustom).toBe(true);
	});

	it('preserves non-choice fields when a schema mixes choices and free-form input', () => {
		const fields: UserInputField[] = [
			{
				name: 'audience',
				label: 'Audience',
				type: 'string',
				description: null,
				enumValues: ['Engineering team', 'All staff'],
				required: true
			},
			{
				name: 'tone',
				label: 'Tone',
				type: 'string',
				description: 'Optional writing tone',
				enumValues: [],
				required: false
			}
		];
		const questions: UserInputChoiceQuestion[] = [
			{
				id: 'audience',
				header: 'Audience',
				question: 'Who should receive this rollout note?',
				responseKey: 'audience',
				options: [
					{
						id: 'audience:0',
						label: 'Engineering team',
						description: null,
						value: 'Engineering team',
						recommended: true
					}
				],
				allowCustom: true
			}
		];

		expect(fieldsWithoutChoiceQuestions(fields, questions).map((field) => field.name)).toEqual([
			'tone'
		]);
		expect(
			collectAcceptedUserInputContent({
				fields,
				questions,
				selectedOptions: { audience: 'audience:0' },
				customAnswers: {},
				values: { tone: 'Concise and reassuring' }
			})
		).toEqual({
			tone: 'Concise and reassuring',
			audience: 'Engineering team',
			audience_label: 'Engineering team',
			audience_source: 'option'
		});
	});

	it('keeps submission disabled until required free-form fields are filled', () => {
		const fields: UserInputField[] = [
			{
				name: 'tone',
				label: 'Tone',
				type: 'string',
				description: null,
				enumValues: [],
				required: true
			}
		];

		expect(requiredUserInputFieldsComplete(fields, {})).toBe(false);
		expect(requiredUserInputFieldsComplete(fields, { tone: 'Concise' })).toBe(true);
	});

	it('omits untouched optional values while preserving explicitly entered falsy values', () => {
		const fields: UserInputField[] = [
			{
				name: 'note',
				label: 'Note',
				type: 'string',
				description: null,
				enumValues: [],
				required: false
			},
			{
				name: 'retries',
				label: 'Retries',
				type: 'integer',
				description: null,
				enumValues: [],
				required: false
			},
			{
				name: 'enabled',
				label: 'Enabled',
				type: 'boolean',
				description: null,
				enumValues: [],
				required: false
			}
		];

		expect(
			collectAcceptedUserInputContent({
				fields,
				questions: [],
				selectedOptions: {},
				customAnswers: {},
				values: { note: '' }
			})
		).toEqual({});
		expect(
			collectAcceptedUserInputContent({
				fields,
				questions: [],
				selectedOptions: {},
				customAnswers: {},
				values: { retries: 0, enabled: false }
			})
		).toEqual({ retries: 0, enabled: false });
	});

	it('rejects fractional values for integer fields', () => {
		const fields: UserInputField[] = [
			{
				name: 'retries',
				label: 'Retries',
				type: 'integer',
				description: null,
				enumValues: [],
				required: true
			}
		];

		expect(userInputFieldErrors(fields, { retries: 1.5 })).toEqual({
			retries: 'Enter a whole number.'
		});
		expect(requiredUserInputFieldsComplete(fields, { retries: 1.5 })).toBe(false);
		expect(requiredUserInputFieldsComplete(fields, { retries: 2 })).toBe(true);
	});

	it('parses validated JSON for array and object fields and blocks invalid shapes', () => {
		const fields: UserInputField[] = [
			{
				name: 'tags',
				label: 'Tags',
				type: 'array',
				description: null,
				enumValues: [],
				required: true
			},
			{
				name: 'settings',
				label: 'Settings',
				type: 'object',
				description: null,
				enumValues: [],
				required: true
			}
		];

		expect(userInputFieldErrors(fields, { tags: '{"not":"an array"}', settings: '[]' })).toEqual({
			tags: 'Enter a valid JSON array.',
			settings: 'Enter a valid JSON object.'
		});
		expect(
			collectAcceptedUserInputContent({
				fields,
				questions: [],
				selectedOptions: {},
				customAnswers: {},
				values: { tags: '["frontend", 2]', settings: '{"strict":true}' }
			})
		).toEqual({ tags: ['frontend', 2], settings: { strict: true } });
	});

	it('renders submitted content as readable fields without choice metadata', () => {
		expect(
			userInputDisplayEntries({
				tone: 'Concise and reassuring',
				audience: 'engineering',
				audience_label: 'Engineering team',
				audience_source: 'option'
			})
		).toEqual([
			{ label: 'Tone', value: 'Concise and reassuring' },
			{ label: 'Audience', value: 'Engineering team' }
		]);
	});
});
