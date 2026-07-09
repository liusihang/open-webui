export type UserInputField = {
	name: string;
	label: string;
	type: string;
	description: string | null;
	enumValues: string[];
	required: boolean;
};

export type UserInputChoiceOption = {
	id: string;
	label: string;
	description: string | null;
	value: unknown;
	recommended: boolean;
};

export type UserInputChoiceQuestion = {
	id: string;
	header: string | null;
	question: string;
	responseKey: string;
	options: UserInputChoiceOption[];
	allowCustom: boolean;
};

export type UserInputDisplayEntry = {
	label: string;
	value: string;
};

type CollectAcceptedUserInputContentOptions = {
	fields: UserInputField[];
	questions: UserInputChoiceQuestion[];
	selectedOptions: Record<string, string>;
	customAnswers: Record<string, string>;
	values: Record<string, unknown>;
};

export const fieldsWithoutChoiceQuestions = (
	fields: UserInputField[],
	questions: UserInputChoiceQuestion[]
): UserInputField[] => {
	const choiceResponseKeys = new Set(questions.map((question) => question.responseKey));
	return fields.filter((field) => !choiceResponseKeys.has(field.name));
};

export const requiredUserInputFieldsComplete = (
	fields: UserInputField[],
	values: Record<string, unknown>
): boolean =>
	fields.every((field) => {
		if (!field.required || field.type === 'boolean') return true;
		const value = values[field.name];
		if (typeof value === 'string') return value.trim().length > 0;
		return value !== null && value !== undefined && value !== '';
	});

export const collectAcceptedUserInputContent = ({
	fields,
	questions,
	selectedOptions,
	customAnswers,
	values
}: CollectAcceptedUserInputContentOptions): Record<string, unknown> => ({
	...collectFieldContent(fieldsWithoutChoiceQuestions(fields, questions), values),
	...collectChoiceContent(questions, selectedOptions, customAnswers)
});

export const userInputDisplayEntries = (content: unknown): UserInputDisplayEntry[] => {
	if (!isPlainObject(content)) {
		return [{ label: 'Response', value: formatDisplayValue(content) }];
	}

	return Object.entries(content)
		.filter(([key]) => !key.endsWith('_label') && !key.endsWith('_source'))
		.map(([key, value]) => ({
			label: humanizeFieldName(key),
			value: formatDisplayValue(content[`${key}_label`] ?? value)
		}));
};

const collectFieldContent = (
	fields: UserInputField[],
	values: Record<string, unknown>
): Record<string, unknown> => {
	if (fields.length === 1 && fields[0].name === 'response') {
		return { response: values.response ?? '' };
	}
	return fields.reduce<Record<string, unknown>>((content, field) => {
		content[field.name] = values[field.name] ?? defaultValueForType(field.type);
		return content;
	}, {});
};

const collectChoiceContent = (
	questions: UserInputChoiceQuestion[],
	selectedOptions: Record<string, string>,
	customAnswers: Record<string, string>
): Record<string, unknown> =>
	questions.reduce<Record<string, unknown>>((content, question) => {
		const customAnswer = (customAnswers[question.id] ?? '').trim();
		if (customAnswer) {
			content[question.responseKey] = customAnswer;
			content[`${question.responseKey}_source`] = 'custom';
			return content;
		}
		const option = question.options.find(
			(candidate) => candidate.id === selectedOptions[question.id]
		);
		if (option) {
			content[question.responseKey] = option.value;
			content[`${question.responseKey}_label`] = option.label;
			content[`${question.responseKey}_source`] = 'option';
		}
		return content;
	}, {});

const defaultValueForType = (type: string): unknown => {
	if (type === 'boolean') return false;
	if (type === 'number' || type === 'integer') return 0;
	return '';
};

const humanizeFieldName = (name: string): string => {
	const words = name.replace(/[_-]+/g, ' ').trim();
	return words ? `${words.charAt(0).toUpperCase()}${words.slice(1)}` : 'Response';
};

const formatDisplayValue = (value: unknown): string => {
	if (value === null || value === undefined || value === '') return '—';
	if (typeof value === 'string') return value;
	if (typeof value === 'number' || typeof value === 'boolean') return String(value);
	try {
		return JSON.stringify(value);
	} catch {
		return String(value);
	}
};

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);
