#!/usr/bin/env node
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { composerExpression, waitForStableComposer, composerBlockerExpression } = require("../composer.js");
const code = readFileSync(path.join(__dirname, "..", "chrome-recipe.js"), "utf8");
const composerCode = readFileSync(path.join(__dirname, "..", "composer.js"), "utf8");

function fixture(text = "", options = {}) {
  let selected = false;
  const editor = {
    innerText:text, textContent:text, isConnected:true, isContentEditable:true,
    getClientRects:() => [1], contains:() => false,
    focus() { if (!options.blockFocus) document.activeElement = editor; },
  };
  const hidden = {...editor, innerText:"hidden stale text", getClientRects:() => []};
  const document = {activeElement:null, querySelectorAll:() => [hidden, editor]};
  const window = {getSelection:() => ({selectAllChildren(e) {
    assert.equal(e, editor); selected = true;
  }})};
  return {editor, document, selected:() => selected,
    run:(expected, select) => vm.runInNewContext(composerExpression(expected, select), {document, window})};
}

test("readiness skips hidden and detached editors without touching drafts or focus", () => {
  const f = fixture("my unrelated draft");
  assert.equal(f.run().text, "my unrelated draft");
  assert.equal(f.document.activeElement, null);
  assert.equal(f.selected(), false);
  f.editor.isConnected = false;
  assert.equal(f.run(), null);
});

test("an empty editor is focused and accepted", () => {
  const f = fixture();
  assert.equal(f.run("", true).ok, true);
  assert.equal(f.document.activeElement, f.editor);
});

test("only an exact owned draft is selected; text changes remain untouched", () => {
  const f = fixture("owned draft");
  assert.equal(f.run("other draft", true).reason, "editor changed");
  assert.equal(f.selected(), false);
  assert.equal(f.run("owned draft", true).ok, true);
  assert.equal(f.selected(), true);
  assert.equal(f.editor.innerText, "owned draft");
});

test("a leftover draft and a focus failure have different diagnostics", () => {
  assert.equal(fixture("stale").run("", true).reason, "editor was not cleared");
  assert.equal(fixture("", {blockFocus:true}).run("", true).reason, "editor focus unavailable");
});

test("ProseMirror paragraph boundaries preserve actual blank lines", () => {
  const f = fixture("one\n\ntwo\n\n\n\nthree");
  const text = value => ({nodeType:3, textContent:value});
  const p = (...childNodes) => ({nodeName:"P", childNodes});
  const br = {nodeName:"BR", classList:{contains:() => true}};
  f.editor.childNodes = [p(text("one")), p(text("two")), p(br), p(text("three"))];
  assert.equal(f.run().text, "one\ntwo\n\nthree");
  assert.equal(f.run("one\ntwo\n\nthree", true).ok, true);
});

test("verification finds the replacement editor after a frontend render", () => {
  const f = fixture("owned draft");
  assert.equal(f.run("owned draft", true).ok, true);
  f.editor.isConnected = false;
  const replacement = fixture();
  f.document.querySelectorAll = () => [f.editor, replacement.editor];
  assert.equal(f.run("").ok, true);
});

test("unrelated drafts are preserved and clearing uses native editing events", () => {
  assert.match(code, /staleText && !bbDraft && !isOwnedDraft\(staleText\)/);
  assert.match(code, /unknown draft preserved/);
  assert.match(composerCode, /Input.dispatchKeyEvent/);
  assert.doesNotMatch(code, /document.execCommand|new InputEvent/);
  assert.match(code, /const inserted = await inspectComposer\(input.prompt.trim\(\)\)/);
});

test("follow-ups load their conversation directly without a second navigation", () => {
  assert.match(code, /createIsolatedPage\(target\)/);
  assert.match(code, /json\/new\?\$\{encodeURI\(target\)\}/);
  assert.doesNotMatch(code, /call\("Page.navigate"/);
});

test("owned tabs and private contexts are closed without reusing user tabs", () => {
  assert.match(code, /closeOwnedTab\(\)/);
  assert.match(code, /Target.disposeBrowserContext/);
  assert.doesNotMatch(code, /pages.find\(/);
  assert.match(code, /CHATGPT_WEB_RECIPE_ISOLATED_CONTEXT/);
});

test("readiness and debugger calls are bounded and survive lifecycle errors", () => {
  assert.doesNotMatch(code, /awaitPromise:\s*true/);
  assert.match(code, /async function waitForValue/);
  assert.match(code, /Promise was collected/);
  assert.match(code, /CHATGPT_WEB_CDP_CALL_TIMEOUT_MS/);
  assert.match(code, /for \(const request of pending.values\(\)\) request.reject\(error\)/);
});

test("a no-op click is retried only while the exact prompt is visibly unsent", () => {
  assert.match(code, /state.result\?\.value\?\.text === input.prompt.trim\(\)/);
  assert.match(code, /submission outcome became ambiguous before capture/);
  assert.match(code, /send click did not start a request/);
});

test("owned draft recovery stores only bounded private SHA-256 markers", () => {
  assert.match(code, /createHash\("sha256"\)/);
  assert.match(code, /mode:0o700/);
  assert.match(code, /mode:0o600/);
  assert.match(code, /live.slice\(256\)/);
  assert.doesNotMatch(code, /writeFileSync\([^\n]+input.prompt/);
});

test("preflight waits through hydration editor replacement", async () => {
  let clock = 0;
  const visible = {present:true, text:""};
  const snapshots = [visible, null, visible, visible, visible, visible];
  const result = await waitForStableComposer(
    async () => snapshots.length ? snapshots.shift() : visible,
    {budgetMs:1000, stableMs:300, pollMs:100,
      now:() => clock, sleep:async ms => {clock += ms;}},
  );
  assert.equal(result, visible);
  assert.ok(clock >= 400, "a briefly visible editor must not pass preflight");
});

test("preflight fails closed when editor never stabilizes", async () => {
  let clock = 0;
  let observations = 0;
  const result = await waitForStableComposer(async () => {
    observations += 1;
    return observations % 2 ? {present:true, text:""} : null;
  }, {budgetMs:750, stableMs:300, pollMs:100,
    now:() => clock, sleep:async ms => {clock += ms;}});
  assert.equal(result, null);
  assert.ok(clock <= 750, "the wait must be strictly bounded");
});

test("recipe retries missing editors only before send, preserving unknown drafts", () => {
  assert.match(code, /const preflightDeadline = Date.now\(\) \+ 16000/);
  assert.match(code, /editor replaced during preflight/);
  assert.match(code, /unknown draft preserved/);
  assert.match(code, /submissionStarted = true/);
});

test("composer blocker diagnosis never returns page content", () => {
  function reason({path="/",title="",text="",online=true}) {
    const document = {title, body:{innerText:text}};
    const location = {pathname:path};
    const navigator = {onLine:online};
    return vm.runInNewContext(composerBlockerExpression(), {document,location,navigator});
  }
  assert.equal(reason({path:"/auth/login"}), "authentication required");
  assert.equal(reason({text:"Log in to ChatGPT"}), "authentication required");
  assert.equal(reason({title:"Just a moment ..."}), "human verification required");
  assert.equal(reason({text:"Verify you are human"}), "human verification required");
  assert.equal(reason({text:"Could not load this ChatGPT conversation"}), "conversation unavailable");
  assert.equal(reason({online:false}), "browser offline");
  assert.equal(reason({text:"confidential chat: secret message"}), "editor missing");
  assert.doesNotMatch(composerBlockerExpression(), /console\.log/);
});

test("login and challenge blockers are not treated as transient retryable editor errors", () => {
  assert.match(code, /composerFailureReason\(\)/);
  assert.match(code, /ChatGPT composer did not become ready:/);
  assert.match(code, /ChatGPT editor preflight failed:/);
  assert.match(code, /human verification required/);
});
