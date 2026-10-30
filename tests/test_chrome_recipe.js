#!/usr/bin/env node
// Regression tests for the frontend selectors and conservative draft recovery.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const code = readFileSync(path.join(__dirname, "..", "chrome-recipe.js"), "utf8");
function expression(variable) {
  const start = code.indexOf(`${variable} = await`);
  assert.notEqual(start, -1, `missing CDP expression: ${variable}`);
  const match = /(?:expression:\s*)?`([^`]+)`/.exec(code.slice(start));
  assert.ok(match, `missing JS expression: ${variable}`);
  return match[1];
}

test("the current top-level Send button is clicked without an old wrapper", async () => {
  let clicked = 0;
  const button = { disabled: false, click() { clicked++; } };
  const document = {
    querySelector(selector) {
      return selector === 'button[type="submit"][aria-label="Send"]' ? button : null;
    },
    querySelectorAll() { return []; },
  };
  const result = await vm.runInNewContext(expression("submitted"), {
    document, Promise, setTimeout,
  });
  assert.equal(result, true);
  assert.equal(clicked, 1);
});

function checkDraft(draft) {
  const editor = {
    innerText: draft, isContentEditable: true,
    focus() { document.activeElement = editor; },
    dispatchEvent() {},
  };
  const document = {
    activeElement: null,
    querySelector() { return editor; },
    execCommand(name) {
      assert.equal(name, "delete");
      editor.innerText = "";
      return true;
    },
  };
  const window = { getSelection() { return { selectAllChildren(e) {
    assert.equal(e, editor);
  } }; } };
  class InputEvent {}
  const source = expression("focused").replace("${expected}", JSON.stringify(draft.trim()));
  return {
    result: vm.runInNewContext(source, { document, window, InputEvent }), editor,
  };
}

test("empty isolated composer is accepted", () => {
  assert.equal(checkDraft("").result.ok, true);
});

test("a restored BB instruction draft can be removed from only the isolated composer", () => {
  const { result, editor } = checkDraft("[BB Agent Control — published Core Instructions]\nold attempt");
  assert.equal(result.ok, true);
  assert.equal(editor.innerText, "");
});

test("unrelated drafts are never cleared", () => {
  assert.match(code, /staleText && !bbDraft && !isOwnedDraft\(staleText\)/);
  assert.match(code, /unknown draft preserved/);
});

test("helper allocates an owned temporary Chrome context and closes it", () => {
  assert.match(code, /Target\.createTarget/);
  assert.match(code, /closeOwnedTab\(\)/);
  assert.doesNotMatch(code, /pages\.find\(/);
});


test("recipe uses a private browser context with first-party cookies", () => {
  assert.match(code, /Target\.createBrowserContext/);
  assert.match(code, /Storage\.setCookies/);
  assert.match(code, /chatgpt\\\.com/);
  assert.match(code, /Target\.disposeBrowserContext/);
  assert.match(code, /CHATGPT_WEB_RECIPE_ISOLATED_CONTEXT/);
  assert.match(code, /getJson\(\x60\$\{cdpBase\}\/json\/new/);
});

test("page readiness never depends on a collectible remote promise", () => {
  assert.doesNotMatch(code, /awaitPromise:\s*true/);
  assert.match(code, /async function waitForValue/);
  assert.match(code, /Promise was collected/);
  assert.match(code, /Execution context was destroyed/);
});

test("each Chrome debugger call has a deadline and pending calls reject on close", () => {
  assert.match(code, /CHATGPT_WEB_CDP_CALL_TIMEOUT_MS/);
  assert.match(code, /Chrome debugger \$\{method\} timed out/);
  assert.match(code, /for \(const request of pending\.values\(\)\) request\.reject\(error\)/);
});

test("a no-op click is retried only while the exact prompt is visibly unsent", () => {
  assert.match(code, /state\.result\?\.value\?\.text === input\.prompt\.trim\(\)/);
  assert.match(code, /submission outcome became ambiguous before capture/);
  assert.match(code, /send click did not start a request/);
  assert.match(code, /submissionStarted = false/);
});

test("the helper removes only its exact injected draft before closing", () => {
  assert.match(code, /let promptInjected = false/);
  assert.match(code, /trim\(\)!==\$\{expected\}/);
  assert.match(code, /await clearInjectedDraft\(\)/);
  assert.match(code, /e\.dispatchEvent\(new InputEvent\('input'/);
});

test("owned draft recovery stores only bounded private SHA-256 markers", () => {
  assert.match(code, /createHash\("sha256"\)/);
  assert.match(code, /mode:0o700/);
  assert.match(code, /mode:0o600/);
  assert.match(code, /live\.slice\(256\)/);
  assert.match(code, /draftLedgerTtlMs = 24 \* 60 \* 60 \* 1000/);
  assert.doesNotMatch(code, /writeFileSync\([^\n]+input\.prompt/);
});
