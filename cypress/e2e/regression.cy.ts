// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

const expectTerminal = Cypress.env('EXPECT_TERMINAL') === true || Cypress.env('EXPECT_TERMINAL') === 'true';
const expectRetrieval =
	Cypress.env('EXPECT_RETRIEVAL') === true || Cypress.env('EXPECT_RETRIEVAL') === 'true';
const expectCodeInterpreter =
	Cypress.env('EXPECT_CODE_INTERPRETER') === true ||
	Cypress.env('EXPECT_CODE_INTERPRETER') === 'true';

const selectFirstModel = () => {
	cy.get('button[aria-label="Select a model"]').click();
	cy.get('button[aria-roledescription="model-item"]').first().click();
};

const openSettings = () => {
	cy.get('button[aria-label="User Menu"]').click();
	cy.contains('button', 'Settings').click();
	cy.contains('[role="tab"], button', 'General').should('be.visible');
};

describe('Open WebUI regression', () => {
	after(() => {
		// eslint-disable-next-line cypress/no-unnecessary-waiting
		cy.wait(2000);
	});

	before(() => {
		cy.registerAdmin();
	});

	beforeEach(() => {
		cy.loginAdmin();
		cy.visit('/');
		cy.get('#chat-input').should('exist');
	});

	it('runs automated post-update regression checks', () => {
		cy.get('#chat-input-container').should('exist');
		cy.get('#input-menu-button').should('exist');
		cy.get('#integration-menu-button').should('exist');
		cy.get('select[aria-label="思考深度"]').should('exist');
		cy.get('button[aria-label="Controls"]').should('exist');
		cy.get('button[aria-label="New Chat"]').should('exist');

		selectFirstModel();
		cy.get('#chat-input').type('Reply with one short sentence for regression verification.', {
			force: true
		});
		cy.get('button[type="submit"]').click();
		cy.get('.chat-user').should('exist');
		cy.get('.chat-assistant', { timeout: 10_000 }).should('exist');
		cy.get('div[aria-label="Generation Info"]', { timeout: 120_000 }).should('exist');
		cy.get('#chat-context-menu-button').should('exist');

		cy.visit('/style-preview');
		cy.get('#preview-reasoning-running').should('exist');
		cy.get('#preview-reasoning-completed').should('exist');
		cy.get('#preview-tool-running .tool-call-container').should('exist');
		cy.get('#preview-tool-done .tool-call-container').should('exist');
		cy.get('#preview-sequential-thinking-running').should('exist');
		cy.get('#preview-sequential-thinking-completed').should('exist');

		cy.visit('/');
		openSettings();
		cy.contains('[role="tab"], button', 'Interface').click();
		cy.contains('button', 'Save').click();
		cy.contains('Settings saved successfully!').should('exist');

		cy.contains('[role="tab"], button', 'Integrations').click();
		cy.get('#tab-tools').should('exist');
		cy.contains('Open Terminal').should('exist');

		if (expectTerminal) {
			cy.contains('button', 'Add Connection').should('exist');
		}

		cy.visit('/');
		cy.get('#integration-menu-button').click();
		cy.contains('Web Search').should('exist');
		cy.contains('Tools').should('exist');
		cy.contains('Code Interpreter').should('exist');
		cy.contains('Deep Research').should('exist');

		if (expectCodeInterpreter) {
			cy.contains('Code Interpreter').should('be.visible');
		}

		cy.get('#input-menu-button').click();
		cy.contains('Upload Files').should('exist');
		cy.contains('Capture').should('exist');
		cy.contains('Attach Webpage').should('exist');
		cy.contains('Attach Knowledge').should('exist');

		if (expectRetrieval) {
			cy.contains('Attach Knowledge').should('be.visible');
		}
	});
});
