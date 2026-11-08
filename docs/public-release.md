# Public release readiness

**Reviewed:** 2026-11-08. Repository: [dcqwqc/chatgpt-web](https://github.com/dcqwqc/chatgpt-web).

## Public GitHub fork: ready for source collaboration

- [x] Public source branch consolidated with the current upstream-derived fork; active user installations and dirty working trees were not overwritten.
- [x] Explicit operator policies, image workers, and external service integrations replace hardcoded private host settings in the current source.
- [x] Main-branch CI passes on Python 3.10, 3.12 and 3.14; release branch also passed 303 Python tests and 12 offline Node tests (one Chrome-dependent test skipped).
- [x] The current tracked working tree passes `scripts/check_public_tree.py` and Python compilation.
- [x] A clean-home ACP initialization and local CLI help smoke test pass without contacting ChatGPT or using account credentials.
- [x] The main branch requires PRs, green Python matrix checks, linear history and resolved discussions; admins are covered. Main force-push and deletion are disabled.
- [x] Dependabot vulnerability alerts and automated security-fix PRs are enabled. GitHub secret scanning and push protection are enabled.
- [x] Two independent upstream contributions were proposed: [HTTP 429 headers](https://github.com/tritao/chatgpt-web/pull/2) and [compact TUI layout](https://github.com/tritao/chatgpt-web/pull/4).

## Independent redistribution / tagged release: blocked

- [ ] **Licensing:** The original upstream did not publish an explicit LICENSE or COPYING file when inspected. [Issue #3](https://github.com/tritao/chatgpt-web/issues/3) requests clarification. No third-party code is unilaterally relicensed.
- [ ] **Historical privacy:** An audit across nine public remote branches inspected 367 text Git blobs (one binary/large blob excluded). It found no matches for a limited set of API-token/private-key/JWT patterns, but 71 historical blobs contain personal device/user references and three distinct commit-author email addresses are not GitHub noreply addresses. The audit cannot prove no secrets exist. Do not rewrite shared branches without a coordinated migration and explicit impact review.
- [ ] **Authenticated smoke test:** Run one disposable authenticated conversation and verify the same result in the browser and CLI, including the expected model and Project, without interfering with active conversations.
- [ ] **Actual ACP host acceptance:** Exercise an authentic rejected 429 and cancellation in the real target UI, verifying one countdown/progress entry, no duplicate accepted requests, and prompt preservation. Mocked tests are not enough.
- [ ] **Packaging:** Resolve licensing and third-party notices, then review artifacts, version, changelog, installation on a clean machine, and required user actions before publishing a tag or downloadable binaries.

The existing public GitHub fork is a collaborative source repository, **not an independently relicensed software release**. Never describe unresolved items above as verified.
