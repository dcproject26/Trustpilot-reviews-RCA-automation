# Classifier audit — the training loop, run from a Google Sheet

The loop for improving classification accuracy, with a sheet as the surface:

1. **Paste reviews** into a tab, with the *correct* labels you want the model to
   reach. Columns are found by name (any order, case-insensitive):

   | column | purpose |
   |---|---|
   | `review` (or `review_text` / `review_summary`) | the text to classify — **required** |
   | `l1` | the correct L1 — **required to score** |
   | `l2` | the correct L2 |
   | `sub_theme` | the correct sub-theme |
   | `review_id` | optional |

2. **Classify + score** — click **Classifier ▸ Classify + score rows**. The
   script sends each row to the server's `/api/classify-audit`, which runs the
   *same* classifier the dashboard runs, scores its answer against your labels,
   and writes back: `pred_l1`, `pred_l2`, `pred_sub_theme`, `l1_ok`, `l2_ok`,
   `sub_ok`, `miss_bucket`, `warnings`.

3. **Read the misses.** Each `miss_bucket` says where the fix lives:

   | bucket | meaning | the fix |
   |---|---|---|
   | `l1l2-boundary` | wrong L1 or L2 | a rule or a worked example |
   | `sub-boundary` | wrong sub-theme, framework exists | a worked example |
   | `taxonomy-gap` | your label has no framework | create the framework / sub-theme |
   | `validator-gap` | framework rejects your exact label | fix the mapping (or the sheet) |
   | `did-not-run` | the model was not reached | see `warnings` — not a miss |

4. **Apply the fix** in the taxonomy / prompt (the code changes).

5. **Paste a FRESH batch** and re-run. Fresh matters: re-scoring the reviews the
   examples were drawn from measures memory, not learning.

6. Repeat until the misses run out.

## Setup (once)

**Server (Replit):** deploy the branch, then optionally set a secret
`AUDIT_API_KEY` to require a key on the endpoint. If unset, the endpoint serves
open (and logs a warning saying so) — like the rest of this app.

**Sheet:** Extensions ▸ Apps Script, paste `classifier_audit.gs`, save. Then
Project Settings ▸ Script Properties:

| property | value |
|---|---|
| `ENDPOINT` | `https://YOUR-APP.replit.app/api/classify-audit` |
| `AUDIT_KEY` | the same value as the server's `AUDIT_API_KEY` (blank if unset) |

Reload the sheet; a **Classifier** menu appears.

## Why the endpoint, not a local script

The classifier needs the live model and the taxonomy/prompt code, both of which
live on the deployed server. The Apps Script keeps everything *in the sheet* —
paste, click, read — with no shell and no local Python. The scoring and the
miss-bucketing are in `server/services/classifier_audit.py`, driven and
mutation-tested; the endpoint (`/api/classify-audit`) has its own tests.
