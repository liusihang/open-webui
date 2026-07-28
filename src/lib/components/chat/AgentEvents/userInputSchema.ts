import type { AgentRunEventPayload } from './types';

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

export type ParsedUserInputSchema = {
	fields: UserInputField[];
	questions: UserInputChoiceQuestion[];
};

type CollectAcceptedUserInputContentOptions = {
	fields: UserInputField[];
	questions: UserInputChoiceQuestion[];
	selectedOptions: Record<string, string>;
	customAnswers: Record<string, string>;
	values: Record<string, unknown>;
};

export const parseUserInputSchema = (
	schema: AgentRunEventPayload | null,
	fallbackMessage: string
): ParsedUserInputSchema => {
	const questions = choiceQuestionsFromSchema(schema, fallbackMessage);
	return { fields: schemaFields(schema), questions };
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
): boolean => Object.keys(userInputFieldErrors(fields, values)).length === 0;

export const userInputFieldErrors = (
	fields: UserInputField[],
	values: Record<string, unknown>
): Record<string, string> =>
	fields.reduce<Record<string, string>>((errors, field) => {
		const hasValue = Object.prototype.hasOwnProperty.call(values, field.name);
		const value = values[field.name];
		if (!hasValue || isEmptyValue(value)) {
			if (field.required && field.type !== 'boolean') {
				errors[field.name] = `${field.label} is required.`;
			}
			return errors;
		}

		const error = validateFieldValue(field, value);
		if (error) errors[field.name] = error;
		return errors;
	}, {});

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

const schemaFields = (schema: AgentRunEventPayload | null): UserInputField[] => {
	const required = new Set(
		Array.isArray(schema?.required)
			? schema.required.filter((item): item is string => typeof item === 'string')
			: []
	);
	const properties = isPlainObject(schema?.properties) ? schema.properties : null;
	if (!properties) {
		if (Array.isArray(schema?.questions)) return [];
		return [
			{
				name: 'response',
				label: 'Response',
				type: stringValue(schema?.type) || 'string',
				description: null,
				enumValues: [],
				required: true
			}
		];
	}

	return Object.entries(properties).map(([name, definition]) => {
		const field = isPlainObject(definition) ? definition : {};
		return {
			name,
			label: stringValue(field.title) || humanizeFieldName(name),
			type: stringValue(field.type) || 'string',
			description: stringValue(field.description),
			enumValues: arrayValue(field.enum).filter((item): item is string => typeof item === 'string'),
			required: required.has(name)
		};
	});
};

const choiceQuestionsFromSchema = (
	schema: AgentRunEventPayload | null,
	fallbackMessage: string
): UserInputChoiceQuestion[] => {
	if (!isPlainObject(schema)) return [];

	const directQuestions = arrayValue(schema.questions)
		.map((item, index) => choiceQuestionFromObject(item, index, fallbackMessage))
		.filter((item): item is UserInputChoiceQuestion => item !== null);
	if (directQuestions.length > 0) return directQuestions;

	const properties = isPlainObject(schema.properties) ? schema.properties : null;
	if (!properties) {
		const rootQuestion = choiceQuestionFromProperty('response', schema, 0, fallbackMessage);
		return rootQuestion ? [rootQuestion] : [];
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
	if (!isPlainObject(value)) return null;
	const options = arrayValue(value.options)
		.map((option, optionIndex) => choiceOptionFromValue(option, `${index}:${optionIndex}`))
		.filter((item): item is UserInputChoiceOption => item !== null);
	const allowCustom = value.allow_custom !== false && value.allowCustom !== false;
	if (options.length === 0 && !allowCustom) return null;

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
		allowCustom
	};
};

const choiceQuestionFromProperty = (
	name: string,
	definition: unknown,
	index: number,
	fallbackMessage: string
): UserInputChoiceQuestion | null => {
	if (!isPlainObject(definition)) return null;
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
	if (options.length === 0) return null;

	return {
		id: name,
		header: stringValue(definition.title),
		question: stringValue(definition.description) || fallbackMessage,
		responseKey: name,
		options,
		allowCustom: definition.allow_custom === true || definition.allowCustom === true
	};
};

const choiceOptionFromValue = (
	value: unknown,
	fallbackId: string
): UserInputChoiceOption | null => {
	if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
		return {
			id: fallbackId,
			label: String(value),
			description: null,
			value,
			recommended: false
		};
	}
	if (!isPlainObject(value)) return null;

	const optionValue =
		value.value ??
		value.const ??
		(Array.isArray(value.enum) && value.enum.length > 0 ? value.enum[0] : undefined);
	const label =
		stringValue(value.label) ||
		stringValue(value.title) ||
		stringValue(value.name) ||
		(optionValue !== undefined ? String(optionValue) : null);
	if (!label) return null;

	return {
		id: stringValue(value.id) || fallbackId,
		label,
		description: stringValue(value.description),
		value: optionValue ?? label,
		recommended: value.recommended === true || value.is_recommended === true
	};
};

const collectFieldContent = (
	fields: UserInputField[],
	values: Record<string, unknown>
): Record<string, unknown> => {
	const errors = userInputFieldErrors(fields, values);
	const firstError = Object.values(errors)[0];
	if (firstError) {
		throw new Error(firstError);
	}

	return fields.reduce<Record<string, unknown>>((content, field) => {
		const hasValue = Object.prototype.hasOwnProperty.call(values, field.name);
		const value = values[field.name];
		if (!hasValue || isEmptyValue(value)) {
			if (field.required && field.type === 'boolean') content[field.name] = false;
			return content;
		}
		content[field.name] = normalizeFieldValue(field, value);
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
const validateFieldValue = (field: UserInputField, value: unknown): string | null => {
	if (field.type === 'integer') {
		const number = typeof value === 'number' ? value : Number(value);
		return Number.isFinite(number) && Number.isInteger(number) ? null : 'Enter a whole number.';
	}
	if (field.type === 'number') {
		const number = typeof value === 'number' ? value : Number(value);
		return Number.isFinite(number) ? null : 'Enter a valid number.';
	}
	if (field.type === 'boolean') {
		return typeof value === 'boolean' ? null : 'Choose true or false.';
	}
	if (field.type === 'array') {
		return parseStructuredValue(value, 'array').ok ? null : 'Enter a valid JSON array.';
	}
	if (field.type === 'object') {
		return parseStructuredValue(value, 'object').ok ? null : 'Enter a valid JSON object.';
	}
	return typeof value === 'string' ? null : 'Enter a text value.';
};

const normalizeFieldValue = (field: UserInputField, value: unknown): unknown => {
	if (field.type === 'integer' || field.type === 'number') return Number(value);
	if (field.type === 'array' || field.type === 'object') {
		const parsed = parseStructuredValue(value, field.type);
		if (!parsed.ok) {
			throw new Error(
				field.type === 'array' ? 'Enter a valid JSON array.' : 'Enter a valid JSON object.'
			);
		}
		return parsed.value;
	}
	return value;
};

type StructuredValueResult = { ok: true; value: unknown } | { ok: false };

const parseStructuredValue = (value: unknown, type: 'array' | 'object'): StructuredValueResult => {
	let parsed = value;
	if (typeof value === 'string') {
		try {
			parsed = JSON.parse(value);
		} catch {
			return { ok: false };
		}
	}
	if (type === 'array') return Array.isArray(parsed) ? { ok: true, value: parsed } : { ok: false };
	return isPlainObject(parsed) ? { ok: true, value: parsed } : { ok: false };
};

const isEmptyValue = (value: unknown): boolean =>
	value === null || value === undefined || (typeof value === 'string' && value.trim().length === 0);

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

const arrayValue = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const stringValue = (value: unknown): string | null =>
	typeof value === 'string' && value.trim().length > 0 ? value : null;

const isPlainObject = (value: unknown): value is AgentRunEventPayload =>
	typeof value === 'object' && value !== null && !Array.isArray(value);
