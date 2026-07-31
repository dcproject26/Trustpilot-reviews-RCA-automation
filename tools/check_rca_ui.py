#!/usr/bin/env python3
"""Drive the RCA column in a real browser and report what it actually does.

The client is one HTML file with no build step and no JS test harness, so the
suite can only make source assertions about it - and a source assertion is a
spelling check: it passes against a build where the line it names is
unreachable. This is the behaviour check.

    python3 -m uvicorn server.main:app --port 8099   # in one shell
    python3 tools/check_rca_ui.py out.png            # in another

Every claim it prints was observed by clicking, not read from the file:
whether each add path produces a row with the same field set as an existing
one, whether a delete round-trips back to its "+" button, whether the claim
empty state is the neutral sentence rather than quote marks around nothing,
and whether every × is the same grey.
"""
import json, sys
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 1600, "height": 1200})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8099/", wait_until="networkidle")
    pg.wait_for_timeout(1200)
    pg.locator(".review-item").first.click(); pg.wait_for_timeout(1500)
    print("buttons in WWR:", json.dumps(pg.evaluate(
      """() => [...document.querySelectorAll('#rca-wwr5-section button')]
             .map(b => (b.innerText||'').replace(/\\s+/g,' ').trim())"""), indent=0))
    # + Add issue by attribute
    before = pg.evaluate("() => document.querySelectorAll('.wwr-issue').length")
    pg.click("[data-wwr-issue-add]"); pg.wait_for_timeout(600)
    after = pg.evaluate("() => document.querySelectorAll('.wwr-issue').length")
    print(f"+ Add issue: {before} -> {after} issues")
    # the new issue must have the SAME field set as an existing one
    print("new issue fields:", json.dumps(pg.evaluate("""() => {
      const rows=[...document.querySelectorAll('.wwr-issue')]; const last=rows[rows.length-1];
      return {selects:[...last.querySelectorAll('select')].map(s=>s.className),
              editables:last.querySelectorAll('[contenteditable]').length,
              dels:last.querySelectorAll('.x-del').length,
              addbtns:[...last.querySelectorAll('button')].map(b=>b.innerText.trim())};}"""), indent=0))
    # analysis add/delete round trip on an AI row
    pg.click("[data-aline-del='0:pattern']"); pg.wait_for_timeout(600)
    print("after deleting pattern, buttons:", pg.evaluate(
      """() => [...document.querySelectorAll('.wwr-issue')][0].innerText.includes('+ Pattern')"""))
    pg.click("[data-aline-add='0:pattern']"); pg.wait_for_timeout(600)
    print("after re-adding, value:", pg.evaluate(
      """() => { const a=[...document.querySelectorAll('.wwr-issue')][0]
                   .querySelectorAll('.wwr-aline-key');
                 return [...a].map(k=>k.textContent.trim()); }"""))
    # claim delete -> neutral empty state, no empty quote marks
    pg.click("[data-claim-del='0']"); pg.wait_for_timeout(700)
    print("claim deleted ->", json.dumps(pg.evaluate("""() => {
      const t=[...document.querySelectorAll('.wwr-issue')][0].innerText;
      return {neutral: t.includes("does not state this in the guest's own words"),
              empty_quote: /[“"]\\s*[”"]/.test(t),
              add_claim: t.includes('+ Claim')}; }"""), indent=0))
    # The legacy migration: card-level analysis from a pre-v4 draft must still
    # reach the screen. Only rendering can prove it - the strings exist in the
    # file either way, which is how a dead `false ?` guard passed a source test.
    print("legacy block renders when present:", pg.evaluate("""() => {
        const t = document.querySelector('#rca-wwr5-section');
        return t ? t.innerText.includes('From an earlier draft') : null; }"""))
    print("every x is one colour:", pg.evaluate("""() => {
        const m={}; document.querySelectorAll('#rca-col .x-del').forEach(e=>{
          m[getComputedStyle(e).color]=(m[getComputedStyle(e).color]||0)+1;}); return m; }"""))
    pg.screenshot(path=sys.argv[1], full_page=True)
    print("page errors:", errs or "none")
    b.close()
