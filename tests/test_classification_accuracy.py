"""
tests/test_classification_accuracy.py

Regression + accuracy tests for the classification pipeline.

Runs the 14-review golden set (the batch you sent me) through the classifier
and reports:
  - Per-review predicted vs expected L1 / L2 / sub_theme
  - Overall accuracy at each level (L1, L2, sub_theme)
  - Warnings triggered (helps catch prompt drift)

Run with:
  cd /path/to/repl
  python -m tests.test_classification_accuracy

Uses MOCK_MODE for offline runs (returns hand-crafted responses matching the
golden set — proves the plumbing works). Set REAL_CLAUDE=1 to hit the live
Anthropic API and see actual accuracy.
"""
import asyncio
import json
import os
import sys
from typing import Optional

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.classifier import classify, ClassificationResult
from server import prompts


# ═════════════════════════════════════════════════════════════════════════
# GOLDEN SET — 14 real reviews with human-verified expected classifications
# ═════════════════════════════════════════════════════════════════════════
GOLDEN = [
    {
        "id": "gs_01_debora",
        "author": "Debora",
        "bid": None,
        "review": ("Absolut desaster! I got tickets months earlier for 8 o'clock for acropolis. "
                    "Shortly before the trip I was shifted to 13 o'clock. I had to stand in the heat "
                    "in the line to get a ticket for my daughter for an hour. But there was non "
                    "available on my timeslot anymore. Do book directly at the official website"),
        "expected_l1": "Operations Issue",
        "expected_l2": "Ticket Issues",
        "expected_sub_theme": "D. Wrong Ticket (Date / Time / Variant)",
        "notes": "Ambiguous re: exclusion (waiting time is consequence not primary complaint)",
    },
    {
        "id": "gs_02_fiona",
        "author": "Fiona Dow",
        "bid": None,
        "review": ("Arrived at the Sagrada Família to discover headout had not processed our booking "
                    "with the tour company despite confirming the booking with us, so spent $30AUD on "
                    "a phone call to get a refund and no tour. This company is a scam do not book through them."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Ticket Issues",
        "expected_sub_theme": "B. Ticket Not Received",
    },
    {
        "id": "gs_03_spanish_colosseum",
        "author": "(Spanish)",
        "bid": "30006382",
        "review": ("NO COMPREN en HEADOUT, ni cumplen ni reintegran dinero. Ya he escrito muchas "
                    "veces a HEADOUT para denunciar que a pesar que habíamos adquirido entradas con "
                    "derecho a visitar la Arena del Coliseo, nos cambiaron a último momento y sin "
                    "aviso la excursión. HEADOUT a pesar de reconocer el mal servicio NO han "
                    "reintegrado ninguna cantidad de dinero, he solicitado la devolución íntegra "
                    "de mis entradas, han pasado casi tres meses de mi reclamación."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Customer Support Issues",
        "expected_sub_theme": None,  # No sub-theme framework yet for this L2
    },
    {
        "id": "gs_04_rab_notredame",
        "author": "RAB - Oxford",
        "bid": "32158537",
        "review": ("Dreadful service - booked a tour of Notre Dame, Paris. Guide did not turn up, "
                    "we waited for 30 minutes in the scorching heat... and it took days for them to "
                    "admit it, and no apology or anything"),
        "expected_l1": "Supply Partner Issue",
        "expected_l2": "Guide No Show",
        "expected_sub_theme": "A. Guide No Show",
    },
    {
        "id": "gs_05_yugandhara_accademia",
        "author": "Yugandhara Singh",
        "bid": None,
        "review": ("Headout charged a massive premium for a Florence Accademia Gallery ticket under "
                    "the guise of 'hosted entry.' Their operator abandoned the meeting point at the "
                    "exact start time with zero grace period, pocketing our money. We walked right up "
                    "to the official box office and bought family tickets for just €56. Headout "
                    "customer service refused a refund, ignored our bank proof, and insulted us by "
                    "offering a '25% credit' after failing us two days in a row."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Meeting Point Issues",
        "expected_sub_theme": "C. Guide or Host Issues",
    },
    {
        "id": "gs_06_angela_paris_boat",
        "author": "Angela",
        "bid": "33644632370",  # 11-digit — tests widened regex
        "review": ("Terrible company don't use. They take your money and oversell boats in Paris- "
                    "and then after waiting in a line for 2 hours tell you they oversold the boat and "
                    "come back tomorrow. Then STILL charge your card and refuse via credit companies "
                    "to return your money. This is a HOAX of a company."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Customer Support Issues",
        "expected_sub_theme": None,
    },
    {
        "id": "gs_07_ravinder_parliament",
        "author": "Ravinder Sibia",
        "bid": "32284426",
        "review": ("BE CAREFUL. I recently booked a combo tour of the Hungarian Parliament and river "
                    "cruise through Headout. For the parliament tour, an EU citizen ticket is "
                    "preselected and it's on the customer to notice this and remove it. You also "
                    "can't remove it until you select another ticket type. Despite it being a combo, "
                    "I ended up with 2 cruise tickets and 3 parliament tickets. Headout refused to "
                    "refund the EU citizen ticket."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Content - Instructions not clear / Misleading Info",
        "expected_sub_theme": None,
    },
    {
        "id": "gs_08_anon_train",
        "author": "Anonymous",
        "bid": None,
        "review": ("Sorry - but this is my second time I dealt with headout and both experiences did "
                    "not go well. It's only after the booking that they declare them as 'headout' "
                    "after first giving you the impression of being on the original website. In my "
                    "first experience I received a train ticket long after the train was gone. I "
                    "filed a complaint, never received an answer."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Customer Support Issues",
        "expected_sub_theme": None,
    },
    {
        "id": "gs_09_kelsie_kids",
        "author": "Kelsie Brook Eckert",
        "bid": "32028270",
        "review": ("They charge money for kids tickets when local sites do not. They did not give "
                    "full information about the site and we learned when we arrived that the bus "
                    "ride to the entrance would take an hour so we missed our timed entry and were "
                    "denied entrance. The company did not offer us a refund."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Content - Instructions not clear / Misleading Info",
        "expected_sub_theme": None,
    },
    {
        "id": "gs_10_marek_alhambra",
        "author": "Marek Makieła",
        "bid": "32308536",
        "review": ("W dniu 25.06.2026 mieliśmy wykupioną wycieczkę z przewodnikiem do Alhambry. "
                    "przewodnik się nie pojawił do godziny 02.40. Bilety zostały pozostawione w "
                    "sklepie około metrów 100 dalej. Pan ze sklepu powiedział, że Państwa firma "
                    "zawsze tak robi."),
        "expected_l1": "Supply Partner Issue",
        "expected_l2": "Guide No Show",
        "expected_sub_theme": "A. Guide No Show",
    },
    {
        "id": "gs_11_eloise_fraud",
        "author": "Eloïse Beauquis",
        "bid": "31966711",
        "review": ("Headout is FRAUD. Do not use this company. They sell tickets and cancel the "
                    "visit right after you emit the payment, without any refund! Despite of 3 "
                    "attempts to get refund, they just never reply."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Customer Support Issues",
        "expected_sub_theme": None,
    },
    {
        "id": "gs_12_federica_qr",
        "author": "Federica Di Bella",
        "bid": None,
        "review": ("Nonostante avessi acquistato i biglietti con un mese di anticipo, non li ho "
                    "ricevuti né il giorno prima né il giorno della visita. Dopo oltre un'ora di "
                    "chat con l'assistenza, mi sono stati inviati prima documenti errati e poi dei "
                    "QR code che non hanno funzionato. Ho perso tempo prezioso durante uno scalo in "
                    "crociera, ho dovuto acquistare nuovamente i biglietti sul posto e mi è stato "
                    "negato anche il rimborso."),
        "expected_l1": "Operations Issue",
        "expected_l2": "Ticket Issues",
        "expected_sub_theme": "A. Ticket Invalid / Not Working",
    },
    {
        "id": "gs_13_geraldine_parc_guell",
        "author": "Geraldine Baron",
        "bid": None,
        "review": ("Le site est mensonge. Ils prennent des commissions de 6€ et on ne peut pas se "
                    "faire rembourser. Parc guell réservé pour parc + casa de gaudi et on a que le "
                    "parc.. à fuir"),
        "expected_l1": "Operations Issue",
        "expected_l2": "Ticket Issues",
        "expected_sub_theme": "D. Wrong Ticket (Date / Time / Variant)",
    },
    {
        "id": "gs_14_sve_refund",
        "author": "Sve",
        "bid": "31664756",
        "review": ("ho cancellato la mia prenotazione e mi è stato detto che avrei ricevuto il "
                    "rimborso entro 5/7 giorni lavorativi. Inutile dire che è passato un mese e "
                    "mezzo e ancora non ho ricevuto nulla. ho contattato l'assistenza e dicono di "
                    "non poter fare nulla"),
        "expected_l1": "Operations Issue",
        "expected_l2": "Customer Support Issues",
        "expected_sub_theme": None,
    },
]


# ═════════════════════════════════════════════════════════════════════════
# Mock Claude call — returns pre-canned answers so we can test the plumbing
# without hitting the API. Set REAL_CLAUDE=1 to hit live Claude.
# ═════════════════════════════════════════════════════════════════════════
MOCK_RESPONSES = {
    "gs_01_debora":               {"l1": "Operations Issue",     "l2": "Ticket Issues",                                       "sub_theme": "D. Wrong Ticket (Date / Time / Variant)"},
    "gs_02_fiona":                {"l1": "Operations Issue",     "l2": "Ticket Issues",                                       "sub_theme": "B. Ticket Not Received"},
    "gs_03_spanish_colosseum":    {"l1": "Operations Issue",     "l2": "Customer Support Issues",                             "sub_theme": None},
    "gs_04_rab_notredame":        {"l1": "Supply Partner Issue", "l2": "Guide No Show",                                       "sub_theme": "A. Guide No Show"},
    "gs_05_yugandhara_accademia": {"l1": "Operations Issue",     "l2": "Meeting Point Issues",                                "sub_theme": "C. Guide or Host Issues"},
    "gs_06_angela_paris_boat":    {"l1": "Operations Issue",     "l2": "Customer Support Issues",                             "sub_theme": None},
    "gs_07_ravinder_parliament":  {"l1": "Operations Issue",     "l2": "Content - Instructions not clear / Misleading Info",  "sub_theme": None},
    "gs_08_anon_train":           {"l1": "Operations Issue",     "l2": "Customer Support Issues",                             "sub_theme": None},
    "gs_09_kelsie_kids":          {"l1": "Operations Issue",     "l2": "Content - Instructions not clear / Misleading Info",  "sub_theme": None},
    "gs_10_marek_alhambra":       {"l1": "Supply Partner Issue", "l2": "Guide No Show",                                       "sub_theme": "A. Guide No Show"},
    "gs_11_eloise_fraud":         {"l1": "Operations Issue",     "l2": "Customer Support Issues",                             "sub_theme": None},
    "gs_12_federica_qr":          {"l1": "Operations Issue",     "l2": "Ticket Issues",                                       "sub_theme": "A. Ticket Invalid / Not Working"},
    "gs_13_geraldine_parc_guell": {"l1": "Operations Issue",     "l2": "Ticket Issues",                                       "sub_theme": "D. Wrong Ticket (Date / Time / Variant)"},
    "gs_14_sve_refund":           {"l1": "Operations Issue",     "l2": "Customer Support Issues",                             "sub_theme": None},
}


def make_mock_claude_call(review_id: str):
    async def _call(prompt: str) -> str:
        resp = MOCK_RESPONSES.get(review_id, {})
        return json.dumps({
            "l1":             resp.get("l1", ""),
            "l2":             resp.get("l2", ""),
            "sub_theme":      resp.get("sub_theme"),
            "review_summary": "Mock summary",
            "reasoning":      "Mock reasoning",
        })
    return _call


async def make_real_claude_call():
    """Real Claude via the anthropic SDK. Requires ANTHROPIC_API_KEY."""
    from anthropic import Anthropic
    client = Anthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    async def _call(prompt: str) -> str:
        msg = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()

    return _call


# ═════════════════════════════════════════════════════════════════════════
# Test runner
# ═════════════════════════════════════════════════════════════════════════

async def run_accuracy_test():
    use_real = os.getenv("REAL_CLAUDE") == "1"

    real_call = None
    if use_real:
        print("=== Running with REAL Claude API ===\n")
        real_call = await make_real_claude_call()
    else:
        print("=== Running with MOCK Claude (set REAL_CLAUDE=1 for real API) ===\n")

    results = []
    for row in GOLDEN:
        claude_call = real_call if use_real else make_mock_claude_call(row["id"])
        pred = await classify(row["review"], booking={}, timeline=[],
                              claude_call=claude_call, review_id=row["id"])
        results.append({"row": row, "pred": pred})

    # ── Report ──────────────────────────────────────────────────────────
    l1_hits, l2_hits, st_hits = 0, 0, 0
    l1_total, l2_total, st_total = 0, 0, 0

    print(f"{'ID':<32} {'L1 ✓/✗':<10} {'L2 ✓/✗':<10} {'ST ✓/✗':<10}")
    print("─" * 72)

    for r in results:
        row  = r["row"]
        pred = r["pred"]

        l1_ok = pred.l1 == row["expected_l1"]
        l2_ok = pred.l2 == row["expected_l2"]
        st_ok = pred.sub_theme == row["expected_sub_theme"]

        l1_total += 1
        l2_total += 1
        if l1_ok: l1_hits += 1
        if l2_ok: l2_hits += 1
        # sub_theme is only scored if there's an expected value or if a framework applies
        if row["expected_sub_theme"] is not None:
            st_total += 1
            if st_ok: st_hits += 1

        l1_mark = "✓" if l1_ok else "✗"
        l2_mark = "✓" if l2_ok else "✗"
        st_mark = "✓" if st_ok else ("✗" if row["expected_sub_theme"] else "—")

        print(f"{row['id']:<32} {l1_mark:<10} {l2_mark:<10} {st_mark:<10}")

        if not l1_ok:
            print(f"    L1: expected '{row['expected_l1']}' got '{pred.l1}'")
        if not l2_ok:
            print(f"    L2: expected '{row['expected_l2']}' got '{pred.l2}'")
        if not st_ok and row["expected_sub_theme"]:
            print(f"    ST: expected '{row['expected_sub_theme']}' got '{pred.sub_theme}'")
        for w in pred.warnings:
            print(f"    warn: {w}")

    print("─" * 72)
    print(f"L1 accuracy:        {l1_hits}/{l1_total} = {l1_hits/l1_total*100:.1f}%")
    print(f"L2 accuracy:        {l2_hits}/{l2_total} = {l2_hits/l2_total*100:.1f}%")
    if st_total:
        print(f"Sub-theme accuracy: {st_hits}/{st_total} = {st_hits/st_total*100:.1f}% (where framework applies)")
    print()

    # ── Coverage report — which sub-theme frameworks were tested ────────
    frameworks_hit = set()
    for r in results:
        st = r["row"]["expected_sub_theme"]
        if st:
            frameworks_hit.add((r["row"]["expected_l1"], r["row"]["expected_l2"]))

    print("Sub-theme framework coverage from this golden set:")
    for l1, l2 in sorted(frameworks_hit):
        n = sum(1 for r in results
                if r["row"]["expected_l1"] == l1 and r["row"]["expected_l2"] == l2)
        print(f"  ({l1}, {l2}): {n} review(s)")
    print()


if __name__ == "__main__":
    asyncio.run(run_accuracy_test())
