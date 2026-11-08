import {
  createBridgeIo,
  experimental_defineProviderBridge,
  PROVIDER_BRIDGE_PROTOCOL_VERSION,
  THREAD_DELTA_GRAMMAR_V3,
  BRIDGE_REQUEST_METHODS as methods,
  BRIDGE_NOTIFICATION_METHODS as notifications,
  THREAD_DELTA_NOTIFICATION_METHOD,
  initializeParamsSchema,
  threadStartParamsSchema,
  threadResumeParamsSchema,
  turnStartParamsSchema,
  threadStopParamsSchema,
  modelListParamsSchema,
  runBridgeRequest,
  type ThreadDelta,
} from "@get-bb/plugin-sdk/provider-bridge";
import fixture from "./message.json";
import { model } from "./model";
const io = createBridgeIo<{ jsonrpc: "2.0" } & Record<string, unknown>>();
const delta = (threadId: string, deltas: ThreadDelta[]) =>
  io.send({
    jsonrpc: "2.0",
    method: THREAD_DELTA_NOTIFICATION_METHOD,
    params: { threadId, deltas },
  });
function identity(threadId: string, providerThreadId: string) {
  io.send({
    jsonrpc: "2.0",
    method: notifications.threadIdentity,
    params: { threadId, providerThreadId },
  });
  delta(threadId, [{ kind: "session.reset" }]);
}
function output(threadId: string, clientRequestId?: string) {
  const key = { providerItemId: "literal-rich-message" };
  delta(threadId, [
    ...(clientRequestId ? [{ kind: "input.accepted" as const, clientRequestId }] : []),
    { kind: "turn.open" },
    { kind: "item.open", key, item: { type: "agentMessage", text: "" } },
    { kind: "item.textDelta", key, channel: "agentMessage", text: fixture.text },
    { kind: "item.textClose", key, channel: "agentMessage", text: fixture.text },
    { kind: "turn.boundary", status: "completed" },
  ]);
}
export const experimental_providerBridge = experimental_defineProviderBridge({
  handleLine(line) {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      return;
    }
    if (!value || typeof value !== "object" || !("method" in value) || !("id" in value)) return;
    const raw = value as { id: string | number; method: string; params: unknown };
    runBridgeRequest({
      request: raw,
      sendError: io.sendError,
      handleRequest: async ({ id, method, params }) => {
        if (method === methods.initialize) {
          initializeParamsSchema.parse(params);
          io.sendResult(id, {
            protocolVersion: PROVIDER_BRIDGE_PROTOCOL_VERSION,
            capabilities: {
              grammarVersions: [THREAD_DELTA_GRAMMAR_V3, THREAD_DELTA_GRAMMAR_V3],
              sessionRestore: true,
              threadArchive: false,
              threadRename: false,
              threadGoalClear: false,
              fork: "none",
              approvalEnforcedBy: "runtime",
              steerMode: "queue",
            },
          });
        } else if (method === methods.modelList) {
          modelListParamsSchema.parse(params);
          io.sendResult(id, { models: [{ ...model, model: model.id }], selectedOnlyModels: [] });
        } else if (method === methods.threadStart) {
          const p = threadStartParamsSchema.parse(params);
          const providerThreadId = "literal-" + p.threadId;
          identity(p.threadId, providerThreadId);
          io.sendResult(id, { providerThreadId, sessionRestorable: true });
          if (p.input?.length) output(p.threadId);
        } else if (method === methods.threadResume) {
          const p = threadResumeParamsSchema.parse(params);
          identity(p.threadId, p.providerThreadId);
          io.sendResult(id, { providerThreadId: p.providerThreadId, sessionRestorable: true });
        } else if (method === methods.turnStart) {
          const p = turnStartParamsSchema.parse(params);
          io.sendResult(id, {});
          output(p.threadId, p.clientRequestId);
        } else if (method === methods.threadStop) {
          threadStopParamsSchema.parse(params);
          io.sendResult(id, {});
        } else io.sendError(id, -32601, "Unsupported offline fixture method");
      },
    });
  },
});
