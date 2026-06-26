type IframeSandboxSettings = {
	iframeSandboxAllowForms?: boolean;
	iframeSandboxAllowSameOrigin?: boolean;
} | null;

export const getToolCallIframePolicy = (settings: IframeSandboxSettings) => ({
	// Tool-call embeds can contain interactive approval UIs such as Run Command.
	// They must remain submittable even when generic iframe forms stay disabled.
	allowForms: true,
	allowSameOrigin: settings?.iframeSandboxAllowSameOrigin ?? false
});
