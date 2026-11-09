#!/usr/bin/env node

// Build a ChatGPT send request in the authenticated frontend, pause it before
// transmission, and pass the request recipe to a local caller over stdout.

const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { composerExpression, inspectComposer:inspectEditor,
  prepareComposer:prepareEditor, waitForStableComposer, composerBlockerExpression } = require("./composer.js");

const cdpBase = process.env.CHATGPT_WEB_CDP_URL || "http://127.0.0.1:9222";
const timeoutMs = Number(process.env.CHATGPT_WEB_RECIPE_TIMEOUT_MS || "90000");
const callTimeoutMs = Number(process.env.CHATGPT_WEB_CDP_CALL_TIMEOUT_MS || "10000");

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const stateRoot = process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state");
const draftLedgerDir = path.join(stateRoot, "chatgpt-web", "recipe-drafts");
const draftLedgerTtlMs = 24 * 60 * 60 * 1000;

function draftHash(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}
function pruneDraftLedger() {
  let entries;
  try { entries = fs.readdirSync(draftLedgerDir); }
  catch (_error) { return; }
  const live = [];
  const now = Date.now();
  for (const name of entries) {
    if (!/^[a-f0-9]{64}$/.test(name)) continue;
    const file = path.join(draftLedgerDir, name);
    try {
      const modified = fs.statSync(file).mtimeMs;
      if (now - modified > draftLedgerTtlMs) fs.unlinkSync(file);
      else live.push({file, modified});
    } catch (_error) { /* A concurrent helper may have pruned it. */ }
  }
  live.sort((left, right) => right.modified - left.modified);
  for (const entry of live.slice(256)) {
    try { fs.unlinkSync(entry.file); }
    catch (_error) { /* A concurrent helper may have pruned it. */ }
  }
}
function rememberOwnedDraft(text) {
  fs.mkdirSync(draftLedgerDir, {recursive:true, mode:0o700});
  fs.chmodSync(draftLedgerDir, 0o700);
  const file = path.join(draftLedgerDir, draftHash(text));
  fs.writeFileSync(file, `${Date.now()}\n`, {encoding:"utf8", mode:0o600});
  fs.chmodSync(file, 0o600);
  pruneDraftLedger();
}
function isOwnedDraft(text) {
  const file = path.join(draftLedgerDir, draftHash(text));
  try {
    const fresh = Date.now() - fs.statSync(file).mtimeMs <= draftLedgerTtlMs;
    if (!fresh) fs.unlinkSync(file);
    return fresh;
  } catch (_error) {
    return false;
  }
}

function isTransientLifecycleError(error) {
  const message = String(error?.message || error);
  return [
    "Promise was collected",
    "Execution context was destroyed",
    "Cannot find context with specified id",
    "Cannot find default execution context",
    "Inspected target navigated or closed",
    "No frame for given id",
  ].some((fragment) => message.includes(fragment));
}

// Every recipe owns an isolated tab. Never type into the owner's existing
// ChatGPT window (also shared with the image workflow), or strand a draft
// there after a timeout or frontend change.
let ownedTabId = null;
let isolatedContextId = null;
let browserConnection = null;
let activeSocket = null;
let watchdogTimer = null;
let closing = false;
let submissionStarted = false;
let promptInjected = false;
let clearInjectedDraft = null;
async function closeOwnedTab() {
  if (isolatedContextId && browserConnection) {
    const id = isolatedContextId;
    isolatedContextId = null;
    try { await browserConnection.call("Target.disposeBrowserContext", {browserContextId:id}); }
    catch (_error) { /* Browser may have already destroyed the context. */ }
    browserConnection.close();
    browserConnection = null;
    ownedTabId = null;
    return;
  }
  if (!ownedTabId) return;
  const id = ownedTabId;
  ownedTabId = null;
  try { await httpRequest(`${cdpBase}/json/close/${encodeURIComponent(id)}`); }
  catch (_error) { /* Chrome may already have closed the target. */ }
}
async function fail(message) {
  if (closing) return;
  closing = true;
  if (watchdogTimer) clearTimeout(watchdogTimer);
  if (promptInjected && clearInjectedDraft) {
    try { await clearInjectedDraft(); }
    catch (_error) { /* The owned page may already be gone. */ }
  }
  if (activeSocket) activeSocket.close();
  const phase = submissionStarted ? "" : " pre-submit";
  process.stderr.write(`chatgpt-web recipe${phase}: ${message}\n`);
  await closeOwnedTab();
  process.exitCode = 1;
}

function readInput() {
  return new Promise((resolve, reject) => {
    let body = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { body += chunk; });
    process.stdin.on("end", () => {
      try { resolve(JSON.parse(body)); }
      catch (_error) { reject(new Error("invalid recipe request")); }
    });
  });
}

function httpRequest(url, method = "GET") {
  return new Promise((resolve, reject) => {
    const request = http.request(url, { method }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`Chrome debugger HTTP ${response.statusCode}`));
        } else resolve(body);
      });
    });
    request.setTimeout(5000, () => request.destroy(new Error("Chrome debugger timed out")));
    request.on("error", reject);
    request.end();
  });
}
async function getJson(url, method = "GET") {
  try { return JSON.parse(await httpRequest(url, method)); }
  catch (error) { throw new Error(`Chrome debugger request failed: ${error.message}`); }
}

// A dedicated ephemeral Chrome browser context prevents ChatGPT's shared
// localStorage/sessionStorage drafts from leaking into recipe preparation. Copy
// only first-party login cookies at invocation time; do not persist them here.
async function browserCDP() {
  const version = await getJson(`${cdpBase}/json/version`);
  if (!version.webSocketDebuggerUrl) throw new Error("Chrome browser CDP unavailable");
  const socket = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, {once:true});
    socket.addEventListener("error", reject, {once:true});
  });
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    const item = pending.get(msg.id);
    if (!item) return;
    pending.delete(msg.id);
    if (msg.error) item.reject(new Error(msg.error.message));
    else item.resolve(msg.result);
  });
  return {
    call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`Chrome debugger ${method} timed out`));
        }, callTimeoutMs);
        pending.set(id, {
          resolve(value) { clearTimeout(timer); resolve(value); },
          reject(error) { clearTimeout(timer); reject(error); },
        });
        socket.send(JSON.stringify({id, method, params}));
      });
    },
    close() { socket.close(); },
  };
}
async function createIsolatedPage(target) {
  // Never fall back silently to the user's persistent browser context.
  // A failed isolated setup should fail safely without editing any draft.
  browserConnection = await browserCDP();
  const context = await browserConnection.call("Target.createBrowserContext",
    {disposeOnDetach:true});
  isolatedContextId = context.browserContextId;
  if (!isolatedContextId) throw new Error("Chrome did not allocate isolated context");
  const stored = await browserConnection.call("Storage.getCookies");
  const cookies = (stored.cookies || []).filter((cookie) =>
    /(^|\.)chatgpt\.com$|(^|\.)openai\.com$/.test(cookie.domain));
  if (!cookies.length) throw new Error("Authenticated ChatGPT cookies missing");
  await browserConnection.call("Storage.setCookies",
    {cookies, browserContextId:isolatedContextId});
  const created = await browserConnection.call("Target.createTarget", {
    url:target, browserContextId:isolatedContextId,
  });
  ownedTabId = created.targetId;
  if (!ownedTabId) throw new Error("Isolated ChatGPT target missing");
  for (let retry = 0; retry < 80; retry++) {
    const pages = await getJson(`${cdpBase}/json/list`);
    const page = pages.filter((item) => item.id === ownedTabId)[0];
    if (page?.webSocketDebuggerUrl) return page;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Isolated Chrome target did not attach");
}

async function main() {
  const input = await readInput();
  if (typeof input.prompt !== "string" || !input.prompt.trim()) {
    return fail("prompt must not be empty");
  }
  // The authenticated persistent profile is the verified production path.
  // Ephemeral browser contexts remain experimental: cookies alone do not
  // consistently reproduce session storage and a real send can silently stall.
  const privateContext = process.env.CHATGPT_WEB_RECIPE_ISOLATED_CONTEXT === "1";
  const target = input.conversation_id
    ? `https://chatgpt.com/c/${encodeURIComponent(input.conversation_id)}`
    : "https://chatgpt.com/";
  const page = privateContext
    ? await createIsolatedPage(target)
    : await getJson(`${cdpBase}/json/new?${encodeURI(target)}`, "PUT");
  if (!page.id && !page.webSocketDebuggerUrl) {
    throw new Error("Chrome could not allocate a ChatGPT page");
  }
  if (!privateContext) ownedTabId = page.id;
  if (!page.webSocketDebuggerUrl) {
    throw new Error("Chrome could not open the authenticated ChatGPT page");
  }
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  activeSocket = socket;
  const pending = new Map();
  const sentinelRequests = new Map();
  const sentinelResponses = [];
  let sequence = 0;
  let finished = false;

  function call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Chrome debugger ${method} timed out`));
      }, callTimeoutMs);
      pending.set(id, {
        resolve(value) { clearTimeout(timer); resolve(value); },
        reject(error) { clearTimeout(timer); reject(error); },
      });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }

  // Never ask Chrome to retain a long-lived remote Promise. ChatGPT replaces
  // execution contexts while hydrating and navigating, and V8 may collect a
  // pending Runtime.evaluate promise. Short host-side polls survive that page
  // lifecycle churn and make "Promise was collected" a recoverable event.
  async function waitForValue(expression, budgetMs) {
    const deadline = Date.now() + budgetMs;
    let lastLifecycleError = null;
    while (Date.now() < deadline && !closing && !finished) {
      try {
        const evaluated = await call("Runtime.evaluate", {
          expression,
          returnByValue: true,
        });
        if (evaluated.exceptionDetails) {
          throw new Error(evaluated.exceptionDetails.text || "page evaluation failed");
        }
        lastLifecycleError = null;
        if (evaluated.result?.value) return evaluated.result.value;
      } catch (error) {
        if (!isTransientLifecycleError(error)) throw error;
        lastLifecycleError = error;
      }
      await delay(100);
    }
    if (lastLifecycleError) throw lastLifecycleError;
    return null;
  }

  async function waitForCapture(budgetMs) {
    const deadline = Date.now() + budgetMs;
    while (Date.now() < deadline && !closing && !finished) await delay(100);
    return finished;
  }

  clearInjectedDraft = async () => {
    if (!promptInjected || socket.readyState !== WebSocket.OPEN) return;
    const cleared = await prepareComposer(input.prompt.trim());
    promptInjected = false;
    if (cleared.ok) await delay(200);
  };

  async function composerFailureReason() {
    try {
      const response = await call("Runtime.evaluate", {
        expression:composerBlockerExpression(), returnByValue:true,
      });
      const reason = response.result?.value;
      if (["browser offline", "authentication required",
           "human verification required", "conversation unavailable",
           "editor missing"].includes(reason)) return reason;
    } catch (_error) {
      // Keep transient navigation failures local and never disclose page text.
    }
    return "editor missing";
  }

  async function inspectComposer(expected, select = false) {
    return inspectEditor(call, expected, select);
  }

  async function prepareComposer(expected) {
    return prepareEditor(call, expected);
  }

  const timer = watchdogTimer = setTimeout(() => {
    if (!closing) {
      socket.close();
      fail("timed out waiting for the send request");
    }
  }, timeoutMs);

  socket.addEventListener("message", (event) => {
    handleMessage(event).catch((error) => fail(error.message));
  });
  async function handleMessage(event) {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const request = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) request.reject(new Error(message.error.message));
      else request.resolve(message.result);
      return;
    }
    if (message.method === "Network.requestWillBeSent") {
      const request = message.params.request;
      const url = new URL(request.url);
      if (request.method === "POST" &&
          (url.pathname === "/backend-api/sentinel/chat-requirements/prepare" ||
           url.pathname === "/backend-api/sentinel/chat-requirements/finalize")) {
        sentinelRequests.set(message.params.requestId, {
          path: url.pathname,
          requestBody: request.postData || "",
        });
      }
      return;
    }
    if (message.method === "Network.loadingFinished" &&
        sentinelRequests.has(message.params.requestId)) {
      const capturedRequest = sentinelRequests.get(message.params.requestId);
      try {
        const response = await call("Network.getResponseBody", {
          requestId: message.params.requestId,
        });
        sentinelResponses.push({
          ...capturedRequest,
          responseBody: response.body,
          base64Encoded: response.base64Encoded,
        });
      } catch (_error) {
        // The request recipe is still useful if Chrome evicted a response body.
      }
      return;
    }
    if (finished || message.method !== "Fetch.requestPaused") return;
    const request = message.params.request;
    const url = new URL(request.url);
    if (request.method !== "POST" ||
        url.pathname !== "/backend-api/f/conversation") {
      await call("Fetch.continueRequest", { requestId: message.params.requestId });
      return;
    }
    finished = true;
    const cookies = await call("Network.getCookies", { urls: [request.url] });
    await call("Fetch.failRequest", {
      requestId: message.params.requestId,
      errorReason: "Aborted",
    });
    const body = JSON.parse(request.postData || "{}");
    const parts = body.messages?.[0]?.content?.parts;
    if (!Array.isArray(parts) || parts.length !== 1 || typeof parts[0] !== "string"
        || parts[0].trim() !== input.prompt.trim()) {
      submissionStarted = false; // The intercepted request was aborted above.
      return fail("captured prompt did not match the intended message; request aborted");
    }
    const headers = { ...request.headers };
    headers.cookie = cookies.cookies
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join("; ");
    await clearInjectedDraft();
    // Network.loadingFinished and Fetch.requestPaused can arrive in adjacent
    // tasks. Give the response-body callback a brief chance to complete.
    await delay(300);
    clearTimeout(timer);
    watchdogTimer = null;
    process.stdout.write(JSON.stringify({
      url: request.url,
      method: request.method,
      headers,
      postData: request.postData || "",
      sentinel: sentinelResponses,
    }));
    closing = true;
    socket.close();
    await closeOwnedTab();
  }

  socket.addEventListener("open", async () => {
    try {
      await call("Network.enable");
      await call("Fetch.enable", {
        patterns: [{
          urlPattern: "https://chatgpt.com/backend-api/f/conversation*",
          requestStage: "Request",
        }],
      });
      // Load the requested conversation once. Loading home first and then
      // navigating raced both editor hydration and restored conversation drafts.
      const ready = await waitForValue(
        composerExpression(),
        30000,
      );
      if (!ready) return fail(
        `ChatGPT composer did not become ready: ${await composerFailureReason()}`);
      // ChatGPT may replace an editor several times during hydration.
      // Never enter a draft until consecutive snapshots confirm readiness.
      const stable = await waitForStableComposer(async () => {
        try { return await inspectComposer(); }
        catch (error) {
          if (isTransientLifecycleError(error)) return null;
          throw error;
        }
      }, {budgetMs:16000, stableMs:350, pollMs:125});
      if (!stable) return fail(
        `ChatGPT composer was replaced or hidden during hydration: ${await composerFailureReason()}`);
      if (input.check === true) {
        clearTimeout(timer);
        watchdogTimer = null;
        process.stdout.write(JSON.stringify({ok:true, composer_ready:true,
          draft_present:!!stable.text}));
        closing = true;
        socket.close();
        await closeOwnedTab();
        return;
      }
      // Chrome can restore a draft from an earlier helper even after that
      // helper cleared its owned DOM. A private hash ledger recognizes only
      // prompts this helper injected; unknown user drafts remain untouched.
      let focused;
      const preflightDeadline = Date.now() + 16000;
      while (Date.now() < preflightDeadline) {
        let snapshot;
        try { snapshot = await inspectComposer(); }
        catch (error) {
          if (!isTransientLifecycleError(error)) throw error;
          snapshot = null;
        }
        if (!snapshot?.present) {
          focused = {ok:false,reason:"editor missing"};
          await delay(200);
          continue;
        }
        const staleText = snapshot.text || "";
        const bbDraft = staleText.startsWith(
          "[BB Agent Control — published Core Instructions]"
        );
        if (staleText && !bbDraft && !isOwnedDraft(staleText)) {
          focused = {ok:false,reason:"unknown draft preserved"};
          break;
        }
        try { focused = await prepareComposer(staleText); }
        catch (error) {
          if (!isTransientLifecycleError(error)) throw error;
          focused = {ok:false,reason:"editor replaced during preflight"};
        }
        if (focused.ok) break;
        if (!["editor missing", "editor focus unavailable",
              "editor was not cleared", "editor replaced during preflight"]
              .includes(focused.reason)) break;
        await delay(200);
      }
      if (!focused?.ok) {
        const reason = focused?.reason === "editor missing"
          ? await composerFailureReason() : focused?.reason || "unknown";
        return fail(`ChatGPT editor preflight failed: ${reason}`);
      }
      await call("Input.insertText", { text: input.prompt });
      promptInjected = true;
      rememberOwnedDraft(input.prompt.trim());
      const inserted = await inspectComposer(input.prompt.trim());
      if (!inserted.ok) return fail(`ChatGPT editor input failed: ${inserted.reason}`);
      // The submit button may be outside [data-chatgpt-composer] and the old
      // data-testid no longer exists on newer ChatGPT frontends.
      for (let clickAttempt = 0; clickAttempt < 3; clickAttempt++) {
        const sendReady = await waitForValue(
          `(()=>{const b=document.querySelector('button[type="submit"][aria-label="Send"]')||document.querySelector('button[data-testid="send-button"]')||[...document.querySelectorAll('button[type="submit"]')].find(b=>/^send( message)?$/i.test(b.getAttribute('aria-label')||''));return !!b&&!b.disabled})()`,
          10000,
        );
        if (!sendReady) return fail("ChatGPT send button was unavailable");
        // From this point, errors are ambiguous until the DOM proves that the
        // exact unsent prompt is still in the composer and no response started.
        submissionStarted = true;
        const submitted = await call("Runtime.evaluate", {
          expression: `(()=>{const b=document.querySelector('button[type="submit"][aria-label="Send"]')||document.querySelector('button[data-testid="send-button"]')||[...document.querySelectorAll('button[type="submit"]')].find(b=>/^send( message)?$/i.test(b.getAttribute('aria-label')||''));if(!b||b.disabled)return false;b.click();return true})()`,
          returnByValue: true,
        });
        if (!submitted.result?.value) {
          submissionStarted = false;
          continue;
        }
        if (await waitForCapture(8000)) return;
        const state = await call("Runtime.evaluate", {
          expression: `(()=>{const editor=${composerExpression()};const stop=document.querySelector('button[data-testid="stop-button"]')||[...document.querySelectorAll('button')].find(b=>/stop generating/i.test(b.getAttribute('aria-label')||''));return {text:editor?.text??null,stop:!!stop}})()`,
          returnByValue: true,
        });
        if (finished) return;
        const unchanged = state.result?.value?.text === input.prompt.trim();
        if (!unchanged || state.result?.value?.stop) {
          return fail("submission outcome became ambiguous before capture");
        }
        // The click was a frontend no-op: the exact draft remains and no
        // request or response exists. This is the only safe click retry case.
        submissionStarted = false;
        if (clickAttempt === 2) {
          return fail("send click did not start a request");
        }
      }
    } catch (error) {
      fail(error.message);
    }
  });
  socket.addEventListener("close", () => {
    const error = new Error("lost the Chrome debugger connection");
    for (const request of pending.values()) request.reject(error);
    pending.clear();
    if (!finished && !closing) fail(error.message);
  });
  socket.addEventListener("error", () => { if (!finished) fail("lost the Chrome debugger connection"); });
}

main().catch((error) => fail(error.message));
