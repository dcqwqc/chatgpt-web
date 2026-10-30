#!/usr/bin/env node
// Regression tests for the frontend selectors and conservative draft recovery.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const code = readFileSync(path.join(__dirname, "..", "chrome-recipe.js"), "utf8");
function expression(variable) {
  const start = code.indexOf(`${variable} = await call("Runtime.evaluate", {`);
  assert.notEqual(start, -1, `missing CDP expression: ${variable}`);
  const match = /expression: `([^`]+)`/.exec(code.slice(start));
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
  return { result: vm.runInNewContext(expression("focused"), { document, window }), editor };
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
  const { result, editor } = checkDraft("User's unsent private work");
  assert.equal(result.ok, false);
  assert.equal(result.reason, "non-BB draft preserved");
  assert.equal(editor.innerText, "User's unsent private work");
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
  assert.doesNotMatch(code, /getJson\(\x60\$\{cdpBase\}\/json\/new/);
});
