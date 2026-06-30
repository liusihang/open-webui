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

	it('keeps tool debug details behind a disclosure in the transcript UI', () => {
		const tool = readSource('./ToolPart.svelte');
		const detail = readSource('./AgentDetailSection.svelte');

		expect(tool).toContain('AgentDetailSection');
		expect(tool).toContain('User summary');
		expect(tool).toContain('Dev details');
		expect(detail).toContain('<details');
		// Debug payload must be formatted as <pre> blocks inside the disclosure,
		// not inlined into the default view
		expect(detail).toContain('<pre');
	});

	it('keeps approval and artifact rows visually quiet and free of fake buttons', () => {
		const approval = readSource('./ApprovalPart.svelte');
		const artifact = readSource('./ArtifactPart.svelte');

		// Approval must surface status text only; no approve/reject form actions
		expect(approval).not.toContain('<button');
		expect(approval).not.toContain('<form');
		expect(approval).toContain('Confirmation required');
		expect(approval).not.toContain('Approve selected');
		expect(approval).not.toContain('Reject selected');
		expect(approval).not.toContain('Auto-reject');
		// Artifact must show name/mime, with long path held for the detail section
		expect(artifact).not.toContain('text-base');
		expect(artifact).toContain('shortPath');
	});

	it('presents the transcript as one ReAct Agent Activity Timeline', () => {
		const transcript = readSource('./AgentTranscript.svelte');
		const part = readSource('./TranscriptPart.svelte');

		expect(transcript).toContain('Agent Activity Timeline');
		expect(transcript).toContain('agent-activity-timeline');
		expect(part).toContain('reactStage');
		expect(part).toContain('Reasoning');
		expect(part).toContain('Action');
		expect(part).toContain('Observation');
		expect(part).toContain('Final answer');
		expect(part).not.toContain('Chain of Thought');
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
});
