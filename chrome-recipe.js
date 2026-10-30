#!/usr/bin/env node

// Build a ChatGPT send request in the authenticated frontend, pause it before
// transmission, and pass the request recipe to a local caller over stdout.

const http = require("http");

const cdpBase = process.env.CHATGPT_WEB_CDP_URL || "http://127.0.0.1:9222";
const timeoutMs = Number(process.env.CHATGPT_WEB_RECIPE_TIMEOUT_MS || "90000");

// Every recipe owns an isolated tab. Never type into the owner's existing
// ChatGPT window (also shared with the image workflow), or strand a draft
// there after a timeout or frontend change.
let ownedTabId = null;
let isolatedContextId = null;
let browserConnection = null;
let activeSocket = null;
let watchdogTimer = null;
let closing = false;
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
  if (activeSocket) activeSocket.close();
  process.stderr.write(`chatgpt-web recipe: ${message}\n`);
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
        pending.set(id, {resolve, reject});
        socket.send(JSON.stringify({id, method, params}));
      });
    },
    close() { socket.close(); },
  };
}
async function createIsolatedPage() {
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
    url:"https://chatgpt.com/", browserContextId:isolatedContextId,
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
  const page = privateContext
    ? await createIsolatedPage()
    : await getJson(`${cdpBase}/json/new?https://chatgpt.com/`, "PUT");
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
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
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
    const headers = { ...request.headers };
    headers.cookie = cookies.cookies
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join("; ");
    // Network.loadingFinished and Fetch.requestPaused can arrive in adjacent
    // tasks. Give the response-body callback a brief chance to complete.
    await new Promise((resolve) => setTimeout(resolve, 300));
    clearTimeout(timer);
    watchdogTimer = null;
    process.stdout.write(JSON.stringify({
      url: request.url,
      method: request.method,
      headers,
      postData: request.postData || "",
      sentinel: sentinelResponses,
    }));
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
      const target = input.conversation_id
        ? `https://chatgpt.com/c/${encodeURIComponent(input.conversation_id)}`
        : "https://chatgpt.com/";
      // Reuse the already-ready home composer instead of navigating the
      // same document. A redundant reload can race Runtime.evaluate and
      // strand the readiness probe in a destroyed execution context.
      // Navigation still happens when the target conversation differs.
      const current = await call("Runtime.evaluate", {
        expression: "location.href",
        returnByValue: true,
      });
      if (current.result.value !== target) {
        await call("Page.navigate", { url: target });
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      const ready = await call("Runtime.evaluate", {
        expression: `(async()=>{for(let i=0;i<300;i++){const e=document.querySelector('[data-composer-markdown][contenteditable="true"]')||document.querySelector('[data-chatgpt-composer] [contenteditable="true"]')||document.querySelector('#prompt-textarea');if(e)return true;await new Promise(r=>setTimeout(r,100));}return false})()`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (!ready.result.value) return fail("ChatGPT composer did not become ready");
      await new Promise((resolve) => setTimeout(resolve, 1800));
      // A crashed prior BB attempt can leave its injected instruction envelope
      // in the new-chat draft, and Chrome restores that draft in fresh tabs.
      // Clear ONLY this owned tab, ONLY for an identifiable BB envelope.
      // Never erase an unrelated draft or the owner's existing browser tab.
      let focused;
      for (let attempt = 0; attempt < 3; attempt++) {
        focused = await call("Runtime.evaluate", {
          expression: `(()=>{const e=document.querySelector('[data-composer-markdown][contenteditable="true"]')||document.querySelector('[data-chatgpt-composer] [contenteditable="true"]')||document.querySelector('#prompt-textarea');if(!e)return {ok:false,reason:'editor missing'};const text=e.innerText.trim();if(text&&!text.startsWith('[BB Agent Control — published Core Instructions]'))return {ok:false,reason:'non-BB draft preserved'};e.focus();if(text){const selection=window.getSelection();selection.selectAllChildren(e);document.execCommand('delete')}return {ok:document.activeElement===e&&e.isContentEditable&&!e.innerText.trim(),reason:'editor was not cleared'}})()`,
          returnByValue: true,
        });
        if (focused.result?.value?.ok ||
            focused.result?.value?.reason !== "non-BB draft preserved") break;
        if (attempt < 2) {
          await new Promise((resolve) => setTimeout(resolve, 1100));
        }
      }
      if (!focused.result?.value?.ok) {
        return fail(`ChatGPT editor preflight failed: ${focused.result?.value?.reason || "unknown"}`);
      }
      await call("Input.insertText", { text: input.prompt });
      const submitted = await call("Runtime.evaluate", {
        // The submit button may be outside [data-chatgpt-composer] and the
        // old data-testid no longer exists on newer ChatGPT frontends.
        expression: `(async()=>{for(let i=0;i<100;i++){const b=document.querySelector('button[type="submit"][aria-label="Send"]')||document.querySelector('button[data-testid="send-button"]')||[...document.querySelectorAll('button[type="submit"]')].find(b=>/^send( message)?$/i.test(b.getAttribute('aria-label')||''));if(b&&!b.disabled){b.click();return true;}await new Promise(r=>setTimeout(r,100));}return false})()`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (!submitted.result.value) return fail("ChatGPT send button was unavailable");
    } catch (error) {
      fail(error.message);
    }
  });
  socket.addEventListener("error", () => { if (!finished) fail("lost the Chrome debugger connection"); });
}

main().catch((error) => fail(error.message));
