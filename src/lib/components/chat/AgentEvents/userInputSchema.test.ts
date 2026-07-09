import { describe, expect, it } from 'vitest';

import {
	collectAcceptedUserInputContent,
	fieldsWithoutChoiceQuestions,
	requiredUserInputFieldsComplete,
	userInputDisplayEntries,
	type UserInputChoiceQuestion,
	type UserInputField
} from './userInputSchema';

describe('user input schema helpers', () => {
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

	it('keeps mixed-form submission disabled until required supplemental fields are filled', () => {
		const fields: UserInputField[] = [
			{
				name: 'tone',
				label: 'Tone',
				type: 'string',
				description: null,
				enumValues: [],
				required: true
			},
			{
				name: 'notify',
				label: 'Notify',
				type: 'boolean',
				description: null,
				enumValues: [],
				required: true
			}
		];

		expect(requiredUserInputFieldsComplete(fields, { notify: false })).toBe(false);
		expect(requiredUserInputFieldsComplete(fields, { tone: 'Concise', notify: false })).toBe(true);
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
