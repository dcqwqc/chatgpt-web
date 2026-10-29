import { definePluginApp } from "@get-bb/plugin-sdk/app";

// BB draws SVG provider marks as theme-aware monochrome masks. Use its
// namespaced SVG assets rather than editing BB's bundled UI or CSS.
const mask = (asset: "codex" | "chatgpt") => {
  const url = `/api/v1/plugins/provider-icons/assets/icons/${asset}.svg`;
  return function ProviderMark({ className }: { className?: string }) {
    return <span aria-hidden="true" className={className} style={{
      display: "inline-block", flexShrink: 0,
      backgroundColor: "currentColor",
      mask: `url("${url}") center / contain no-repeat`,
      WebkitMask: `url("${url}") center / contain no-repeat`,
    }} />;
  };
};
const CodexMark = mask("codex");
const ChatGPTMark = mask("chatgpt");

export default definePluginApp((app) => {
  app.slots.experimental_providerIcon({ providerKind: "agent", providerId: "codex", icon: CodexMark });
  app.slots.experimental_providerIcon({ providerKind: "agent", providerId: "acp-codex-acp", icon: CodexMark });
  app.slots.experimental_providerIcon({ providerKind: "agent", providerId: "acp-chatgpt-web", icon: ChatGPTMark });
});
