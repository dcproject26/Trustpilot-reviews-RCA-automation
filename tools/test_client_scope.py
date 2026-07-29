#!/usr/bin/env python3
"""
Are the dashboard's shared helpers actually reachable from where they are used?

    python3 tools/test_client_scope.py

Needs node. Skips (exit 0) without it rather than failing a machine that
cannot run the check.

This exists because of a bug that no syntax check could see. unwrapInsights
was declared INSIDE loadDraftOverlays and called from the window-picker
handler in renderReviewCol. Both functions parse. Both pass node --check. The
call throws a ReferenceError at runtime, into a catch that discarded it - so
the request succeeded, the reply was dropped, and the panel kept the previous
window's numbers under the previous window's label. It looked exactly like a
window picker that ignored the window, which is a different bug entirely and
is where the time went.

The check: extract the page's script, evaluate it with the browser globals
stubbed, and ask the resulting scope whether each shared helper is a function.
A helper trapped inside another function is not, and that is the failure.

Exit code is 0 only when every check passes.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Helpers called from more than one top-level function. Each one is a
# ReferenceError waiting to happen if it drifts back inside a closure.
SHARED = [
    "unwrapInsights",
    "renderReviewCol",
    "loadDraftOverlays",
]

PASS, FAIL = "ok  ", "FAIL"


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = os.path.join(root, "client", "index.html")
    if not shutil.which("node"):
        print("node not installed - skipping (this check needs a JS engine)")
        return 0
    if not os.path.exists(page):
        print(f"{page} not found - skipping")
        return 0

    with open(page) as fh:
        html = fh.read()
    js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    if not js.strip():
        print("no inline script found in client/index.html")
        return 1

    # Stub only what the script touches while DEFINING things. Anything it
    # calls at load time is allowed to fail quietly - the question here is
    # which names ended up in scope, not whether the page renders headless.
    harness = r"""
const _noop = () => {};
const _el = new Proxy({}, {
  get: (t, k) => {
    if (k === 'style' || k === 'dataset' || k === 'classList') return _el;
    if (k === 'querySelectorAll' || k === 'getElementsByTagName') return () => [];
    if (k === 'querySelector' || k === 'getElementById'
        || k === 'createElement' || k === 'closest') return () => _el;
    if (k === 'appendChild' || k === 'addEventListener'
        || k === 'removeEventListener' || k === 'remove'
        || k === 'add' || k === 'toggle' || k === 'setAttribute') return _noop;
    if (k === 'innerHTML' || k === 'textContent' || k === 'value') return '';
    if (k === Symbol.toPrimitive) return () => '';
    return _el;
  },
  set: () => true,
});
globalThis.document = _el;
globalThis.window = globalThis;
globalThis.location = { href: '', search: '', reload: _noop };
globalThis.localStorage = { getItem: () => null, setItem: _noop, removeItem: _noop };
globalThis.fetch = () => Promise.resolve({ ok: false, json: async () => ({}) });
globalThis.navigator = { clipboard: { writeText: _noop }, userAgent: 'node' };
globalThis.alert = _noop; globalThis.confirm = () => false;
globalThis.requestAnimationFrame = _noop;
globalThis.setInterval = _noop;
globalThis.EventSource = function () { return _el; };
process.on('unhandledRejection', _noop);

__SCRIPT__

// Report what actually landed in scope. eval keeps function declarations in
// this scope, which is the browser's top level for an inline script.
const NAMES = __NAMES__;
const out = {};
for (const n of NAMES) {
  try { out[n] = typeof eval(n); } catch (e) { out[n] = 'MISSING: ' + e.name; }
}
console.log('___RESULT___' + JSON.stringify(out));
"""
    script = harness.replace("__SCRIPT__", js).replace("__NAMES__", json.dumps(SHARED))

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        p = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)

    m = re.search(r"___RESULT___(\{.*\})", p.stdout)
    if not m:
        print("the page's script did not finish loading under node:\n")
        print((p.stderr or p.stdout)[-1500:])
        print("\nThis is a load-time error, not a scope problem - fix it first.")
        return 1

    kinds = json.loads(m.group(1))
    failures = []
    print("shared helpers reachable from the top level\n")
    for name in SHARED:
        kind = kinds.get(name, "absent")
        ok = kind == "function"
        print(f"  {PASS if ok else FAIL}  {name}"
              + ("" if ok else f"   is '{kind}' at the top level - it is "
                               "declared inside another function, so calling "
                               "it from a second one throws a ReferenceError"))
        if not ok:
            failures.append(name)

    print("-" * 62)
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
