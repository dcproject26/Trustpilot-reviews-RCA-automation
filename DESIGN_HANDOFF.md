# Dashboard design handoff — ORM Review RCA

Everything the design team needs to work on the dashboard's look, where each
thing sits, and — the part that matters most for a redesign — **what is
deliberately not on screen and is staying off**.

Written against the build on `claude/vectorshift-pipeline-review-coj74p`.

---

## 1. The only file you need

```
client/index.html          8,167 lines — the whole dashboard
```

That is not a simplification. **There is no build step, no framework, no CSS
file and no component tree.** One HTML file holds:

| What | Where in the file | How to find it |
|---|---|---|
| Design tokens | `:root` at line ~16 | `:root {` |
| All CSS | one `<style>` block, lines ~16–1,340 | `</style>` ends it |
| Markup shell | lines ~1,350–1,425 | `<div class="inbox-list"` |
| All JS | two `<script>` blocks after the shell | `<script>` |

Every card in the app is produced by a JS template literal inside that second
`<script>`, so **the markup for a section and the logic that decides whether it
renders live in the same place.** Search for the section's `id` and you are
standing in both.

### What this means for how you hand work back

- A CSS-only change is safe to make directly — it is one `<style>` block.
- Anything that moves markup is editing a template literal inside JS. Backticks
  and `${...}` inside those strings terminate them. This has broken the build
  three times. **Run `node --check` on the extracted script before handing it
  back**, or hand back a description and let engineering apply it.
- There is nothing to `npm install` and nothing to compile. Open the file, or
  run the server and load `/`.

### Files you do *not* need

`server/` is the backend. Nothing there decides layout or colour. The one thing
worth knowing: the server sends each review a **`bucket`** (`sent`,
`candidates`, `identified`, `untraceable`, `processing`) and the client picks
which card shape to draw from it. If a design depends on "which state is this
card in", that word is the state.

---

## 2. Tokens

Defined once on `:root`. Use these rather than literal hex — several states are
recoloured by swapping a token.

**Surfaces** `--page` `--facts-bg` `--card` `--inset` `--hover-fill`
**Lines** `--border` `--hairline` `--input-bd` `--dashed`
**Text, lightest to darkest** `--empty` `--dim` `--muted-2` `--muted` `--body` `--text`
**Accent (purple)** `--accent` `--accent-hov` `--accent-bg` `--accent-bd` `--accent-bd-2` `--chip-bg` `--chip-fg`
**Status** `--green`/`--green-bg`, `--amber`/`--amber-bg`, `--red`/`--red-bg`
**Type** `--sans`, `--mono`

Base is `13px` / `1.5` on `--sans`. The palette is warm off-white, near-black
text, one purple accent.

> **There is no dark mode.** Nothing reads `prefers-color-scheme`. If dark mode
> is in scope, say so — it is a real piece of work, not a token swap, because
> status backgrounds and the accent set would each need a second definition.

---

## 3. Layout

Four columns, each separated by a draggable divider (`.col-resize`, widths
persist per user).

```
┌──────────┬─┬─────────────────┬─┬──────────────────────┐
│  INBOX   │║│   REVIEW COL    │║│      RCA COL         │
│ (aside)  │║│  #review-col    │║│     #rca-col         │
│          │║│                 │║│                      │
│ search   │║│ the review, the │║│ the analysis and the │
│ 6 tabs   │║│ booking, and    │║│ reply — everything   │
│ list     │║│ how we found it │║│ an associate writes  │
│ + footer │║│                 │║│                      │
└──────────┴─┴─────────────────┴─┴──────────────────────┘
```

**Inbox tabs, in order:** All · Matched · Possible matches · Processing ·
Untraceable · Sent. Each carries a live count (`#cnt-*`). These are the
`bucket` values above, and a review is in exactly one.

Every section in columns 2 and 3 is a `.section` with a `.section-label`
header and a `.section-chev` (▾) — one delegated listener collapses any of
them, keyed on `data-sec-key`. **Collapse state is per section and persists.**

---

## 4. Section inventory

### Column 2 — `#review-col`, "what we know"

| Order | Section | id / class | Notes |
|---|---|---|---|
| 1 | Review header, stars, language | — | rating, author, date, `EN`/`FR` chip |
| 2 | Review body | — | translated text; original underneath when translated |
| 3 | Reference number | — | only when the review names one |
| 4 | **Booking match** | `.match-*` | tier badge (`T1`/`T2`) + title + `↻ Re-run` |
| 5 | Extracted from review | — | guest name, pax, "Searched Zendesk as" |
| 6 | How we built this match | `.trail-*` | the confidence trail — N steps, collapsed |
| 7 | Slack mentions of this BID | — | |
| 8 | **Candidate picker** | `.candidate-*` | *only* in bucket `candidates` — replaces 9–10 |
| 9 | Set booking ID | `.bid-set-*` | always available |
| 10 | Booking details | `.facts-block` | *only* in bucket `identified` |
| 11 | Events timeline | `#rca-booking-logs-section` | id is legacy; the section is the timeline |

**Two states worth designing for explicitly**, because they are what an
associate actually spends time in:

- **Candidate picker** (`bucket: candidates`). A list of `.candidate-card`, each
  with `#BID`, a score bar, the experience, a meta line, and reason chips.
  Below: `Confirm · run full RCA` (primary), `None → Untraceable`,
  `Close out → Sent`. When a booking's details could not be read, the meta line
  carries a **sentence saying which of three things happened** — the warehouse
  does not have it / the lookup did not complete / it was read and is empty.
  Those sentences are load-bearing; please keep room for a full line of text
  rather than a truncated chip.
- **Untraceable / Processing.** Distinct on purpose: "we searched and found
  nothing" vs "nobody has searched yet". They must not converge visually.

### Column 3 — `#rca-col`, "what we write"

| Order | Section | id | Rendered? |
|---|---|---|---|
| 1 | Guest's stated issue | — | yes |
| 2 | **Case findings** | `#rca-casefindings-section` | yes |
| 3 | **What went wrong** | `#rca-wwr5-section` | yes — per-issue, with a claim-accuracy chip |
| 4 | Flags | `#rca-flags-section` | yes |
| 5 | Guest ↔ support | — | yes — contact count in the header hint |
| 6 | SP interaction | — | yes |
| 7 | Actions taken | — | yes — **tabbed**, Unrouted first, then per team |
| 8 | DSS followed | `.dss-followed` | yes |
| 9 | Resolution & takedown | — | yes |
| 10 | **Response to guest** | `#rca-reply-section` | yes — see below |
| 11 | Slack thread post | `#rca-slack-section` | yes — preview + section picker |

**The response block (10) is two boxes and the distinction is the whole point:**

- **Top box — "Response to guest · FR"** — this is what is sent. Editable.
- **Bottom box — "English working copy — not sent"** — a projection. Edits here
  are applied by translating them into the top box.

For an English review only the top box draws, with no hint that a translation
happened. The guest's language is detected from the review automatically — there
is no language input to design.

---

## 5. Removed, and staying removed

These were taken off the dashboard by request. **Treat them as absent when you
redesign — do not reintroduce them, and do not design around a gap where they
used to be.**

| Section | Status on the card | Backend |
|---|---|---|
| **Fixes (§3)** | Not rendered. `fxHtml` is still built and simply not appended. | **Kept.** `what_went_wrong.fixes` is validated and stored, and **Actions taken is a view over exactly that array** — it is still live. |
| **Area of improvement** | Not rendered. | **Kept** — column, validator and Slack section all still work. |
| **Cancellation policy** | Not rendered. | **Kept** — still fetched and stored. |
| **Evidence (legacy)** | Not rendered (`evidenceBlock` is dead). | Superseded by Case findings. |
| **Evidence merged into Case findings** | Off. | Was producing every point twice — once plain, once with a Zendesk link. |
| **Booking logs (Slack post)** | Not in the post. | Timeline still holds the events. |
| **Events timeline (RCA column)** | Merged into the timeline in column 2. | — |

Two of these matter for layout:

1. **Actions taken is not orphaned.** Fixes is invisible; the tabs that route
   from it are not. If you redesign Actions taken, it is showing data whose
   source card is currently hidden.
2. **Nothing under these was deleted**, so any of them can come back. If your
   design has a natural slot for "Fixes" or "Area of improvement", note it —
   restoring Fixes is one line.

---

## 6. Things the design has to keep doing

These are not stylistic preferences; the codebase treats them as correctness,
and a redesign that loses them will be sent back.

1. **An absence must never look like a pending state.** "Booking details load on
   confirm" shipped once as a permanent message with no request in flight. No
   spinners, skeletons or ellipses for states that are final.
2. **"Found nothing" and "did not run" must look different.** The trail marks
   are `pass` / `warn` / `fail` and they mean different things. A repair or a
   coercion is a `warn`, not a `pass`.
3. **Empty sections say why.** An empty Actions-taken tab means either "nobody
   raised anything with this team" or "this team was never at fault" — the card
   says which. Keep room for that sentence.
4. **Counts are shown where they can be trusted.** `3 findings`, `2 events`,
   `10 steps · 5 to read` are in the section headers deliberately.
5. **A guest name that is a hash is never displayed as a name.** The warehouse
   stores digests; the card shows the readable Zendesk copy or nothing.
6. **Long text is text.** Trail lines, empty-state reasons and candidate meta
   lines are full sentences and are not safe to truncate to a chip.

---

## 7. Getting it running

```bash
git checkout claude/vectorshift-pipeline-review-coj74p
# open client/index.html directly for pure CSS work, or run the app:
python3 -m uvicorn server.main:app --port 8000     # then load http://127.0.0.1:8000/
```

`MOCK_MODE=true` seeds sample reviews so every bucket has something in it —
useful for seeing the candidate picker and the untraceable card without live
data.

---

## 8. Open questions for the design team

1. **Dark mode** — currently none. In scope?
2. **The candidate picker** is where a wrong click makes the entire RCA about
   somebody else's booking. It currently looks like the rest of the app. Should
   it read as a decision point?
3. **`T2 · Direct match`** can currently appear together on a sent review. That
   is a known bug being fixed in code, not a thing to design around — flagged so
   you do not treat it as intended.
