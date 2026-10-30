// Opt in with CHATGPT_WEB_TEST_CDP_URL. Uses only its own about:blank tab;
// no account, website traffic, user tabs, or desktop input is involved.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {prepareComposer, inspectComposer} = require("../composer.js");
const base = process.env.CHATGPT_WEB_TEST_CDP_URL;

test("native Chrome editor clearing survives re-render, repeated turns, and hidden editors",
  {skip:!base, timeout:20000}, async () => {
    const page = await (await fetch(`${base}/json/new?about:blank`, {method:"PUT"})).json();
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    let seq = 0;
    const pending = new Map();
    ws.addEventListener("message", event => {
      const message = JSON.parse(event.data), item = pending.get(message.id);
      if (!item) return;
      pending.delete(message.id);
      clearTimeout(item.timer);
      if (message.error) item.reject(new Error(message.error.message));
      else item.resolve(message.result);
    });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 3000);
      pending.set(id, {resolve, reject, timer});
      ws.send(JSON.stringify({id, method, params}));
    });
    try {
      await new Promise((resolve, reject) => {
        ws.addEventListener("open", resolve, {once:true});
        ws.addEventListener("error", reject, {once:true});
      });
      const fixture = `document.body.innerHTML = '<div id="prompt-textarea" contenteditable="true" style="display:none">hidden</div><div data-chatgpt-composer><div contenteditable="true" style="white-space:pre-wrap"><p>owned draft</p></div></div>';
        window.events = [];
        document.addEventListener('input', event => {
          window.events.push({trusted:event.isTrusted,type:event.inputType});
          if (event.inputType === 'deleteContentBackward') {
            const clone = event.target.cloneNode(true);
            event.target.replaceWith(clone);
          }
        });`;
      await call("Runtime.evaluate", {expression:fixture});
      assert.equal((await prepareComposer(call, "different draft")).reason, "editor changed");
      assert.equal((await inspectComposer(call)).text, "owned draft");
      assert.equal((await prepareComposer(call, "owned draft")).ok, true);
      const events = await call("Runtime.evaluate", {expression:"window.events", returnByValue:true});
      assert.ok(events.result.value.some(event => event.trusted && event.type === "deleteContentBackward"));
      for (let turn = 0; turn < 5; turn++) {
        const prompt = `turn ${turn}\n\nUnicode: café 🦊\nlast line`;
        await call("Input.insertText", {text:prompt});
        const markup = await call("Runtime.evaluate", {expression:"document.querySelector('[data-chatgpt-composer]').innerHTML", returnByValue:true});
        assert.equal((await inspectComposer(call)).text, prompt, markup.result.value);
        assert.equal((await prepareComposer(call, prompt)).ok, true);
      }
      assert.equal((await inspectComposer(call)).text, "");
      const hidden = await call("Runtime.evaluate", {expression:"document.querySelector('#prompt-textarea').innerText", returnByValue:true});
      assert.equal(hidden.result.value, "hidden");
    } finally {
      for (const item of pending.values()) clearTimeout(item.timer);
      ws.close();
      await fetch(`${base}/json/close/${page.id}`);
    }
  });
