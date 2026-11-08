import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { model } from "./model";
export default function plugin(bb: BbPluginApi) {
  bb.providers.register({
    id: "chatgpt-literal-qa",
    displayName: "Literal UI QA (offline)",
    strings: {
      signInHint: "No authentication or AI model used.",
      expiredHint: "Offline fixture",
      installUrl: "https://getbb.app/plugin-guide",
    },
    maintenance: { health: false, usage: false, installation: false },
    capabilities: {
      supportsServiceTier: false,
      supportsNativeUserQuestion: false,
      fork: "none",
      supportsManualCompaction: false,
      supportsThreadArchive: false,
      supportsThreadRename: false,
      permissionModes: ["full"],
      reasoningLevels: ["low"],
    },
    reasoningLevels: [{ id: "low", label: "Fixture" }],
    composerActions: [],
    models: { fallback: [model] },
    completedTurnDisplay: "flat",
  });
}
