import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const readSource = (relativePath: string) =>
	readFileSync(new URL(relativePath, import.meta.url), 'utf8');

describe('AgentTranscript presentation guardrails', () => {
	it('uses AgentTranscript for Agent Mode and keeps StatusHistory for non-agent fallback', () => {
		const source = readSource('../Messages/ResponseMessage.svelte');
		const bridge = readSource('./AgentRunStatusBridge.svelte');

		expect(source).toContain('import AgentTranscript');
		expect(source).toContain('<AgentTranscript');
		expect(source).toContain('StatusHistory');
		// Agent Mode branch must be separate from the legacy non-agent StatusHistory branch
		expect(source).toMatch(/\{#if message\?\.agent_run_id\}[\s\S]*<AgentTranscript/);
		expect(bridge).not.toContain('</script></script>');
	});

	it('renders the final answer through the normal ContentRenderer path', () => {
		const source = readSource('../Messages/ResponseMessage.svelte');
		const agentFinal = readSource('./AgentFinalAnswer.svelte');

		// ResponseMessage keeps the canonical ContentRenderer for non-agent content
		expect(source).toContain('<ContentRenderer');
		// AgentFinalAnswer (compat fallback) must also delegate to ContentRenderer
		expect(agentFinal).toContain('<ContentRenderer');
		// No raw-text streaming card that bypasses ContentRenderer
		expect(agentFinal).not.toContain('whitespace-pre-wrap');
		expect(agentFinal).not.toContain('Final answer');
		expect(agentFinal).not.toContain('agent-run-final-answer');
	});

	it('does not leak unsafe reasoning/private fields into the transcript model', () => {
		const source = readSource('./eventFold.ts');

		// Recursive sanitizer must explicitly strip these keys
		expect(source).toContain("'chain_of_thought'");
		expect(source).toContain("'private'");
		expect(source).toContain("'raw'");
		expect(source).toContain("'raw_reasoning'");
		expect(source).toContain("'reasoning'");
		expect(source).toContain("'debug'");
		expect(source).toContain("'thought'");
		// text.delta must write into textBlocks, never finalText
		expect(source).toMatch(/event_type === 'text\.delta'[\s\S]*?textBlocks/);
		expect(source).not.toMatch(/event_type === 'text\.delta'[\s\S]{0,400}finalText =/);
	});

	it('keeps successful tool details available through progressive disclosure', () => {
		const tool = readSource('./ToolPart.svelte');
		const detail = readSource('./AgentDetailSection.svelte');

		expect(tool).toContain('AgentDetailSection');
		expect(tool).toMatch(/\{#if part\.status !== 'running'[\s\S]*AgentDetailSection/);
		expect(tool).toContain("$i18n.t('View details')");
		expect(detail).toContain('<details');
		// Debug payload must be formatted as <pre> blocks inside the disclosure,
		// not inlined into the default view
		expect(detail).toContain('<pre');
	});

	it('keeps approval and artifact rows visually quiet while wiring real approval actions', () => {
		const approval = readSource('./ApprovalPart.svelte');
		const transcriptPart = readSource('./TranscriptPart.svelte');
		const artifact = readSource('./ArtifactPart.svelte');

		// Approval actions must be real API-backed controls, not decorative status text
		expect(approval).toContain('submitAgentRunApproval');
		expect(approval).toContain("part.status === 'pending'");
		expect(approval).toContain('<button');
		expect(approval).toContain("'approved'");
		expect(approval).toContain("'rejected'");
		expect(approval).toContain('idempotencyKey');
		expect(approval).toContain('no longer available');
		expect(transcriptPart).toContain('<ApprovalPart {part} {agentRunId} />');
		expect(approval).not.toContain('<form');
		expect(approval).not.toContain('AgentDetailSection');
		// Artifact must show name/mime quietly without raw metadata by default
		expect(artifact).not.toContain('text-base');
		expect(artifact).toContain('shortPath');
		expect(artifact).not.toContain('AgentDetailSection');
	});

	it('uses a Codex-like collapsible process row instead of a status dashboard', () => {
		const transcript = readSource('./AgentTranscript.svelte');

		expect(transcript).toContain('import ChevronDown');
		expect(transcript).toContain('<details class="agent-transcript"');
		expect(transcript).toContain('<summary class="agent-transcript-summary">');
		expect(transcript).toContain('<ChevronDown');
		expect(transcript).toContain('setInterval');
		expect(transcript).toContain('displayElapsedMs(model, now)');
		expect(transcript).not.toContain('artifactCount');
		expect(transcript).not.toContain('approvalCount');
		expect(transcript).not.toContain('agent-transcript-flag');
	});

	it('reopens the process disclosure when a new attention state arrives', () => {
		const transcript = readSource('./AgentTranscript.svelte');

		expect(transcript).toContain('transcriptAttentionKey');
		expect(transcript).toContain('previousAutoOpenKey');
		expect(transcript).toMatch(/nextAutoOpenKey[\s\S]*!== previousAutoOpenKey[\s\S]*open = true/);
		expect(transcript).not.toContain('let initialized = false');
	});

	it('does not use decorative side stripes in process parts', () => {
		for (const file of [
			'./AssistantNotePart.svelte',
			'./ActionSummaryPart.svelte',
			'./ToolPart.svelte',
			'./ApprovalPart.svelte',
			'./UserInputPart.svelte',
			'./ArtifactPart.svelte',
			'./ErrorPart.svelte',
			'./TranscriptPart.svelte'
		]) {
			expect(readSource(file)).not.toMatch(/border-left:\s*[2-9]px/);
		}
	});

	it('defaults failed tools and error parts to expanded so failures are visible', () => {
		const transcriptModel = readSource('./transcriptModel.ts');

		expect(transcriptModel).toMatch(/defaultExpanded:\s*status === 'error'/);
		expect(transcriptModel).toMatch(/defaultExpanded:\s*status === 'pending'/);
		expect(transcriptModel).toMatch(/defaultExpanded:\s*true/);
	});

	it('uses compact typography for the transcript summary and timeline', () => {
		const transcript = readSource('./AgentTranscript.svelte');

		expect(transcript).toContain('agent-transcript-headline');
		// Compact summary uses 0.7-0.75rem; never text-base / text-lg
		expect(transcript).not.toContain('text-base');
		expect(transcript).not.toContain('text-lg');
	});

	it('keeps commentary aligned with the final answer color and tools visually quieter', () => {
		const transcript = readSource('./AgentTranscript.svelte');
		const note = readSource('./AssistantNotePart.svelte');
		const action = readSource('./ActionSummaryPart.svelte');
		const tool = readSource('./ToolPart.svelte');

		expect(transcript).toContain('--agent-transcript-body-color: var(--tw-prose-body');
		expect(note).toContain('color: var(--agent-transcript-body-color');
		expect(action).toContain('color: var(--agent-transcript-body-color');
		expect(tool).toMatch(/color:\s*var\(\s*--agent-transcript-tool-color/);
		expect(tool).toContain('--tw-prose-captions');
	});

	it('uses shared theme-aware transcript tokens across attention and detail surfaces', () => {
		const transcript = readSource('./AgentTranscript.svelte');
		const approval = readSource('./ApprovalPart.svelte');
		const userInput = readSource('./UserInputPart.svelte');
		const detail = readSource('./AgentDetailSection.svelte');

		expect(transcript).toContain('--agent-transcript-surface-color');
		expect(transcript).toContain(':global(.dark) .agent-transcript');
		expect(approval).toContain('--agent-transcript-attention-surface');
		expect(userInput).toContain('--agent-transcript-attention-surface');
		expect(detail).toContain('--agent-transcript-surface-color');
	});

	it('supports reduced motion and applies the error state to subagent rows', () => {
		const transcript = readSource('./AgentTranscript.svelte');
		const note = readSource('./AssistantNotePart.svelte');
		const tool = readSource('./ToolPart.svelte');
		const transcriptPart = readSource('./TranscriptPart.svelte');

		expect(transcript).toContain('@media (prefers-reduced-motion: reduce)');
		expect(note).toContain('@media (prefers-reduced-motion: reduce)');
		expect(tool).toContain('@media (prefers-reduced-motion: reduce)');
		expect(transcriptPart).toContain("class:error={part.status === 'error'}");
	});

	it('uses human-readable tool names and inline recoverable action errors', () => {
		const tool = readSource('./ToolPart.svelte');
		const approval = readSource('./ApprovalPart.svelte');
		const userInput = readSource('./UserInputPart.svelte');

		expect(tool).toContain('humanizeToolName');
		expect(tool).toContain('userFacingSummary');
		expect(tool).toMatch(/if \(summary\)[\s\S]*return summary/);
		expect(approval).toContain('let submitError');
		expect(approval).toContain('role="alert"');
		expect(userInput).toContain('let submitError');
		expect(userInput).toContain('role="alert"');
		expect(approval).not.toContain('toast.error');
		expect(userInput).not.toContain('toast.error');
		expect(approval).toContain(':focus-visible');
		expect(userInput).toContain(':focus-visible');
	});

	it('renders ask-user prompts as option rows with a custom answer path', () => {
		const userInput = readSource('./UserInputPart.svelte');

		expect(userInput).toContain('parseUserInputSchema');
		expect(userInput).toContain('agent-user-choice-option');
		expect(userInput).toContain('agent-user-choice-custom');
		expect(userInput).toContain('selectedOptions');
		expect(userInput).toContain('customAnswers');
		expect(userInput).toContain('idempotencyKey');
		expect(userInput).toContain('no longer available');
		expect(userInput).toMatch(/canSubmitChoiceAnswer[\s\S]*selectedOptions\[/);
		expect(userInput).toMatch(/canSubmitChoiceAnswer[\s\S]*customAnswers\[/);
		expect(userInput).toContain('aria-pressed={selectedOptions[activeQuestion.id] === option.id}');
		expect(userInput).toContain('for={customAnswerInputId(part.userInputId, activeQuestion.id)}');
		expect(userInput).toContain('id={customAnswerInputId(part.userInputId, activeQuestion.id)}');
		expect(userInput).toContain("$i18n.t('Continue')");
		expect(userInput).toContain("$i18n.t('Skip')");
		expect(userInput).toContain("$i18n.t('Tell the agent how to adjust')");
	});
});
