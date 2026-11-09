// Runs only inside the recipe's owned page. Inspect without editing unless an
// exact expected draft is provided; the caller authorizes owned drafts only.
function composerState(expected, select = false) {
  const selectors = '[data-composer-markdown][contenteditable="true"],'
    + '[data-chatgpt-composer] [contenteditable="true"],#prompt-textarea';
  const e = [...document.querySelectorAll(selectors)].find(node =>
    node.isConnected && node.isContentEditable && node.getClientRects().length);
  if (!e) return expected === undefined ? null : {ok:false, reason:"editor missing"};
  // ProseMirror renders one paragraph per input newline. innerText inserts
  // *two* newlines between paragraphs, so comparing it with the injected plain
  // text fails for BB's multiline instructions and leaves the draft behind.
  // Preserve real empty paragraphs instead of collapsing whitespace globally.
  function inlineText(node) {
    if (node.nodeType === 3) return node.textContent || "";
    if (node.nodeName === "BR") {
      return node.classList.contains("ProseMirror-trailingBreak") ? "" : "\n";
    }
    return [...node.childNodes].map(inlineText).join("");
  }
  const paragraphs = e.childNodes && [...e.childNodes];
  const text = (paragraphs?.length && paragraphs.every(node => node.nodeName === "P")
    ? paragraphs.map(node => node.childNodes.length === 1 && node.childNodes[0].nodeName === "BR"
      ? "" : inlineText(node)).join("\n")
    : (e.innerText || e.textContent || "")).trim();
  if (expected === undefined) return {present:true, text};
  if (text !== expected) return {ok:false, reason:expected ? "editor changed" : "editor was not cleared"};
  if (select) {
    e.focus();
    if (document.activeElement !== e && !e.contains(document.activeElement)) {
      return {ok:false, reason:"editor focus unavailable"};
    }
    if (text) window.getSelection().selectAllChildren(e);
  }
  return {ok:true, text};
}

function composerExpression(expected, select = false) {
  return `(${composerState.toString()})(${JSON.stringify(expected) ?? "undefined"},${select})`;
}

async function inspectComposer(call, expected, select = false) {
  const value = await call("Runtime.evaluate", {
    expression:composerExpression(expected, select), returnByValue:true,
  });
  if (value.exceptionDetails) throw new Error("ChatGPT editor inspection failed");
  return value.result?.value || {ok:false, reason:"editor missing"};
}

async function prepareComposer(call, expected) {
  const selected = await inspectComposer(call, expected, true);
  if (!selected.ok) return selected;
  if (selected.text) {
    // Trusted native editing keeps ProseMirror and its persisted draft in sync.
    for (const type of ["keyDown", "keyUp"]) {
      await call("Input.dispatchKeyEvent", {
        type, key:"Backspace", code:"Backspace", windowsVirtualKeyCode:8,
      });
    }
  }
  await new Promise(resolve => setTimeout(resolve, 100));
  // Hydration/editing may have replaced the original node. Focus the current one.
  return inspectComposer(call, "", true);
}

// A page can briefly expose an editor and then replace it during React
// hydration. Require consecutive visible observations before preflight, with
// a deadline on the host (never a long-lived promise in the page context).
async function waitForStableComposer(inspect, {
  budgetMs = 16000, stableMs = 350, pollMs = 125,
  now = Date.now, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
} = {}) {
  const deadline = now() + budgetMs;
  let stableSince = null;
  while (now() < deadline) {
    const snapshot = await inspect();
    if (snapshot?.present) {
      if (stableSince === null) stableSince = now();
      if (now() - stableSince >= stableMs) return snapshot;
    } else {
      stableSince = null;
    }
    const left = deadline - now();
    if (left > 0) await sleep(Math.min(pollMs, left));
  }
  return null;
}

// Return only stable reason codes. Never send or log page text, cookies,
// personal conversation contents, or login form fields in a diagnostic.
function composerBlocker() {
  const path = (location.pathname || "").toLowerCase();
  const title = (document.title || "").toLowerCase();
  const text = (document.body?.innerText || "").slice(0, 16000).toLowerCase();
  if (navigator.onLine === false) return "browser offline";
  if (/\/(?:auth|login|signin)(?:\/|$)/.test(path) ||
      /(?:log in to chatgpt|sign in to chatgpt|login to chatgpt|anmelden bei chatgpt)/.test(text)) {
    return "authentication required";
  }
  if (/just a moment|checking your browser|verify (?:you are|that you are) human|checking if the site connection is secure|security check|cloudflare challenge|human verification|confirme que eres humano/.test(title+" "+text)) {
    return "human verification required";
  }
  if (/could not load (?:this )?chatgpt conversation|unable to load (?:chat )?history|conversation not found|this conversation is unavailable/.test(text)) {
    return "conversation unavailable";
  }
  return "editor missing";
}
function composerBlockerExpression() {
  return `(${composerBlocker.toString()})()`;
}

module.exports = { composerState, composerExpression, inspectComposer, prepareComposer, waitForStableComposer, composerBlocker, composerBlockerExpression };
