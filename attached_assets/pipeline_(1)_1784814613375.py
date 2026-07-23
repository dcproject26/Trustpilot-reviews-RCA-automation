"""
REPLACES existing server/pipeline.py

The pipeline for a new review. Wraps every step in try/except so one failure
doesn't kill the rest of the flow.

Steps:
  1.  Translate (if non-English)
  2.  BID regex (Tier 1)
  3.  Signal extraction (Claude) — only if no BID
  4.  BigQuery match (Tier 1 lookup or Tier 2 fuzzy search)
  5.  Persist candidate list + confidence trail (Tier 2 → picker state)
  6.  Zendesk timeline
  7.  Insights (BigQuery)
  8.  Similar complaints (BigQuery + Trustpilot)
  9.  DSS webhook (called eagerly for context; associate can also reconnect on-demand)
  10. Stated Issue summary
  11. Classification (L1/L2)
  12. Full RCA generation
  13. Response draft
  14. Save + post-back to Slack thread
  15. Metrics
"""
import asyncio, logging, re
from datetime import datetime

from sqlalchemy.orm.attributes import flag_modified

from server.config import is_live, MOCK_MODE
from server.db import SessionLocal, Review, RcaDraft, ReviewMetric
from server.services import claude, bigquery as bq, zendesk, dss, slack as slk
from server.services.canned import get_canned_responses
from server.services.insights import get_insights as _get_insights
from server.taxonomy import DIAGNOSTIC_CHECKS, BID_REGEX

log = logging.getLogger(__name__)


async def process_review(review_id: str):
    db = SessionLocal()
    try:
        review = db.query(Review).filter(Review.id == review_id).first()
        if not review:
            log.error(f"Review {review_id} not found")
            return

        log.info(f"[pipeline] {review_id} — start")

        # ── 1. Translate ──────────────────────────────────────────────────────
        if not review.body_english:
            try:
                result = await claude.translate(
                    review.body_original, review.language or "auto", review_id)
                if result and result.strip() != "ENGLISH_ALREADY":
                    review.body_english = result.strip()
                db.commit()
            except Exception as e:
                log.exception(f"Translation failed: {e}")

        review_text = review.body_english or review.body_original

        # ── Instrumentation counters ──────────────────────────────────────────
        _ctr = {
            "bid_attachment": 0, "bid_regex": 0, "bid_manual": 0, "bid_none": 0,
            "t1_attachment_confirmed": 0, "t1_manual_confirmed": 0,
            "t1_regex_verified": 0, "t1_regex_downgraded": 0, "t1_bq_missed": 0,
            "t1_auto_promoted": 0,
            "t2_venue_mapped": 0, "t2_venue_not_resolved": 0,
            "t2_auto_promoted": 0, "t2_candidates": 0, "t2_untraceable": 0,
            "untraceable_total": 0, "untraceable_has_signals": 0,
            # Step 2 — Zendesk requester lookup
            "t2_zendesk_lookup_attempted": 0,
            "t2_zendesk_auto":             0,
            "t2_zendesk_candidates":       0,
            "t2_zendesk_no_match":         0,
            # Step 3 — BQ venue+date paths
            "t2_bq_venue_date_30_auto":    0,
            "t2_bq_venue_date_30":         0,
            "t2_bq_venue_date_60_auto":    0,
            "t2_bq_venue_date_60":         0,
            "t2_bq_date_only_loose":       0,
        }

        # ── 2. BID extraction + source detection ─────────────────────────────
        confidence_trail = []
        bid_source = None

        ref_in_text = re.search(BID_REGEX, review_text or "")
        if ref_in_text and not review.reference_number:
            review.reference_number = ref_in_text.group(0)
            db.commit()

        if review.reference_number:
            if review.slack_channel == "C_MANUAL":
                bid_source = "manual"
                _ctr["bid_manual"] += 1
            elif ref_in_text and ref_in_text.group(0) == review.reference_number:
                bid_source = "regex"
                _ctr["bid_regex"] += 1
            else:
                bid_source = "attachment"
                _ctr["bid_attachment"] += 1
            confidence_trail.append({
                "mark": "pass",
                "text": f"<strong>BID extracted</strong> via {bid_source}: {review.reference_number}",
            })
        else:
            _ctr["bid_none"] += 1
            confidence_trail.append({
                "mark": "pass",
                "text": "<strong>BID</strong> — no 7–12 digit number found",
            })

        log.info(
            f"[extract] bid_source: attachment={_ctr['bid_attachment']} | "
            f"regex={_ctr['bid_regex']} | manual={_ctr['bid_manual']} | none={_ctr['bid_none']}"
        )

        # ── 3+4. Tier 1 / Tier 2 booking match ───────────────────────────────
        booking         = None
        match_tier      = None
        candidates      = []
        candidate_state = False
        narrowing_path  = None
        extracted_sigs  = {}
        narrowing_attempts = []

        if not is_live("bigquery"):
            # MOCK_MODE: fall back to existing mock-aware find_booking
            try:
                search_ctx = {
                    "id": review_id,
                    "author": review.author or "",
                    "reference_number": review.reference_number,
                    "signals": {},
                }
                match_result = await bq.find_booking(search_ctx)
                if match_result and match_result.get("candidates"):
                    candidates = match_result["candidates"]
                    candidate_state = True
                    booking = candidates[0]
                    match_tier = 2
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Mock BQ:</strong> {len(candidates)} candidates"})
                elif match_result:
                    booking = match_result
                    match_tier = booking.get("_match", {}).get("tier")
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Mock BQ:</strong> Tier {match_tier} — BID {booking.get('id')}"})
            except Exception as e:
                log.exception(f"Mock booking match failed: {e}")
        else:
            # ── LIVE: source-tiered Tier 1 trust ──────────────────────────────
            from server.services.bigquery_patch import verify_bid, run_narrowing_query
            from server.services import venue_resolver
            from server.prompts import venue_extraction_prompt

            if review.reference_number and bid_source:
                try:
                    bq_row = verify_bid(review.reference_number)
                except Exception as e:
                    log.warning(f"verify_bid raised: {e}")
                    bq_row = None

                if bq_row:
                    if bid_source in ("attachment", "manual"):
                        booking = bq_row
                        match_tier = 1
                        _ctr[f"t1_{bid_source}_confirmed"] += 1
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>BQ:</strong> BID {review.reference_number} confirmed"})
                        confidence_trail.append({"mark": "pass",
                            "text": f"<strong>Tier 1</strong> confirmed via {bid_source}"})
                    else:  # regex — run 1-of-3 verify
                        verify_hits = []
                        # 1. Name match
                        pgn = (bq_row.get("primary_guest_name") or "").lower()
                        author_lower = (review.author or "").lower()
                        if pgn and author_lower and (
                            author_lower in pgn or pgn in author_lower
                            or any(p in pgn for p in author_lower.split() if len(p) >= 2)
                        ):
                            verify_hits.append("name")
                        # 2. Date within 30 days of visit
                        visit_str = bq_row.get("date_of_visit", "") or ""
                        if visit_str:
                            try:
                                from datetime import date as _date
                                visit_dt = _date.fromisoformat(visit_str)
                                recv_dt = (review.received_at or datetime.utcnow()).date()
                                if abs((recv_dt - visit_dt).days) <= 30:
                                    verify_hits.append("date")
                            except Exception:
                                pass
                        # 3. Venue match (if we can quickly extract from text)
                        exp_name = (bq_row.get("experienceName") or "").lower()
                        if exp_name:
                            for word in (review_text or "").lower().split():
                                if len(word) >= 4 and word in exp_name:
                                    verify_hits.append("venue")
                                    break

                        if verify_hits:
                            booking = bq_row
                            match_tier = 1
                            _ctr["t1_regex_verified"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": f"<strong>BQ verify:</strong> regex BID confirmed via {', '.join(verify_hits)}"})
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1</strong> confirmed via regex"})
                        else:
                            # Weak BID — downgrade to Tier 2
                            bq_row["low_confidence_bid_match"] = True
                            candidates = [bq_row]
                            match_tier = 2
                            candidate_state = True
                            _ctr["t1_regex_downgraded"] += 1
                            confidence_trail.append({"mark": "warn",
                                "text": "<strong>Weak BID</strong> — 0/3 verify checks passed; downgraded to Tier 2"})
                else:
                    _ctr["t1_bq_missed"] += 1
                    confidence_trail.append({"mark": "warn",
                        "text": f"<strong>BQ:</strong> BID {review.reference_number} not found — falling through to Tier 2"})

            log.info(
                f"[tier1] source outcome: attachment_confirmed={_ctr['t1_attachment_confirmed']} | "
                f"manual_confirmed={_ctr['t1_manual_confirmed']} | regex_verified={_ctr['t1_regex_verified']} | "
                f"regex_downgraded={_ctr['t1_regex_downgraded']} | bq_missed={_ctr['t1_bq_missed']}"
            )

            # ── Tier 2 cascade (runs when no Tier 1 booking yet) ──────────────
            if not booking or match_tier != 1:
                # Extract venue hints via Claude
                venue_hints = None
                try:
                    prompt = venue_extraction_prompt(review_text or "")
                    raw = await claude._call(prompt, max_tokens=256)
                    import json as _json
                    parsed_venue = _json.loads(raw.strip()
                                               .removeprefix("```json").removeprefix("```")
                                               .removesuffix("```").strip())
                    venue_hints = parsed_venue.get("venue_hints") or []
                    extracted_sigs["venue_hints"] = venue_hints
                except Exception as e:
                    log.warning(f"Venue extraction failed: {e}")
                    venue_hints = []

                # Resolve venue hints → TGIDs
                tgids = None
                if venue_hints:
                    try:
                        tgids = await venue_resolver.resolve(venue_hints)
                    except Exception as e:
                        log.warning(f"Venue resolver failed: {e}")
                if tgids:
                    _ctr["t2_venue_mapped"] += 1
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Venues:</strong> {venue_hints} → {len(tgids)} TGIDs"})
                elif venue_hints:
                    _ctr["t2_venue_not_resolved"] += 1
                    confidence_trail.append({"mark": "warn",
                        "text": f"<strong>Venues extracted</strong> but no TGIDs resolved: {venue_hints}"})

                log.info(
                    f"[tier2] venue resolution: mapped_tgids={_ctr['t2_venue_mapped']} | "
                    f"not_resolved={_ctr['t2_venue_not_resolved']}"
                )

                # Author parsing
                def _parse_author(name: str):
                    if not name:
                        return None, None
                    parts = name.strip().split()
                    if not parts:
                        return None, None
                    if len(parts) == 1:
                        t = parts[0]
                        return (t, None) if t.isalpha() and len(t) >= 2 else (None, None)
                    return parts[0], parts[-1]

                author_first, author_last = _parse_author(review.author or "")
                extracted_sigs.update({
                    "author_first": author_first,
                    "author_last":  author_last,
                })

                # review_pub_date for BQ date param
                pub_date = (review.received_at or datetime.utcnow()).strftime("%Y-%m-%d")
                extracted_sigs["review_pub_date"] = pub_date

                if author_first or author_last:
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Author parsed:</strong> first='{author_first}' last='{author_last}'"})

                # ── Name parseability check (Step 0) ─────────────────────────
                def _name_parseable(first, last):
                    if not first:
                        return False
                    if last:
                        return True
                    return len(first) >= 3 and first.isalpha()

                name_parseable = _name_parseable(author_first, author_last)

                # ── Shared helpers ────────────────────────────────────────────
                from server.services.bigquery import _get_booking_extra

                def _make_candidate(r, path_name, matched_on):
                    exp = r.get("experienceName") or r.get("experience_name") or r.get("experience") or ""
                    dov = r.get("date_of_visit") or r.get("visitDate") or ""
                    return {
                        "id":                 r.get("id", ""),
                        "primary_guest_name": r.get("primary_guest_name", ""),
                        "experience":         exp,
                        "experienceName":     exp,
                        "experience_name":    exp,
                        "date_of_visit":      dov,
                        "visitDate":          dov,
                        "vendorName":         r.get("vendorName") or r.get("partner") or "",
                        "tid":                r.get("tid"),
                        "tgid":               r.get("tgid"),
                        "vid":                r.get("vid"),
                        "matched_on":         matched_on,
                        "narrowing_path":     path_name,
                        "matchReasons":       matched_on,
                        "score":              None,
                    }

                def _run_bq_attempt(path_name, date_window, tgid_list=None):
                    """Run one BQ narrowing query — NEVER passes name (BQ has PII hashes)."""
                    try:
                        rows = run_narrowing_query(
                            tgid_list=tgid_list,
                            review_pub_date=pub_date,
                            date_window=date_window,
                            author_first="",   # never pass name — BQ has PII hash
                            author_last="",
                        )
                    except Exception as e:
                        log.warning(f"[tier2] BQ attempt {path_name} failed: {e}")
                        rows = []
                    narrowing_attempts.append({
                        "path": path_name,
                        "params": {"date_window": date_window, "use_tgids": bool(tgid_list)},
                        "result_count": len(rows),
                    })
                    confidence_trail.append({
                        "mark": "pass" if rows else "warn",
                        "text": f"<strong>BQ {path_name}:</strong> {len(rows)} row(s)",
                    })
                    return rows

                cascade_done = False

                # ── Step 2: Zendesk requester lookup (primary name path) ───────
                if name_parseable and not cascade_done:
                    _ctr["t2_zendesk_lookup_attempted"] += 1
                    confidence_trail.append({"mark": "pass",
                        "text": f"<strong>Zendesk lookup:</strong> '{author_first}"
                                f"{(' ' + author_last) if author_last else ''}'"})
                    try:
                        zd_bids = await zendesk.find_bids_by_requester_name(
                            author_first, author_last, lookback_days=60)
                        log.info(
                            f"[tier2] zendesk requester: {len(zd_bids)} BIDs "
                            f"for '{author_first} {author_last or ''}'")
                    except Exception as e:
                        log.warning(f"[tier2] Zendesk requester lookup failed — continuing: {e}")
                        zd_bids = []

                    if zd_bids:
                        zd_candidates = []
                        for bid in zd_bids[:10]:
                            try:
                                bq_row = verify_bid(bid)
                            except Exception as e:
                                log.warning(f"[tier2] verify_bid({bid}) raised: {e}")
                                continue
                            if not bq_row:
                                log.info(f"[tier2] BID {bid} not in BQ — skipped")
                                continue

                            # Cross-check: venue match if hints available, else date window
                            if venue_hints:
                                exp_name = (
                                    bq_row.get("experienceName") or
                                    bq_row.get("experience_name") or ""
                                ).lower()
                                if not any(vh.lower() in exp_name for vh in venue_hints):
                                    log.info(
                                        f"[tier2] BID {bid} venue mismatch "
                                        f"(hints={venue_hints}, exp='{exp_name}') — skipped")
                                    continue
                            else:
                                visit_str = (
                                    bq_row.get("date_of_visit") or
                                    bq_row.get("visitDate") or ""
                                )
                                if visit_str:
                                    try:
                                        from datetime import date as _date
                                        visit_dt = _date.fromisoformat(visit_str[:10])
                                        recv_dt = (review.received_at or datetime.utcnow()).date()
                                        gap = abs((recv_dt - visit_dt).days)
                                        if gap > 90:
                                            log.info(
                                                f"[tier2] BID {bid} date too far ({gap}d) — skipped")
                                            continue
                                    except Exception:
                                        pass

                            zd_candidates.append((bid, bq_row))

                        n_zd = len(zd_candidates)
                        if n_zd == 1:
                            bid, bq_row = zd_candidates[0]
                            booking = bq_row.copy()
                            booking["id"] = bid
                            booking.setdefault(
                                "experienceName",
                                bq_row.get("experience_name", ""))
                            booking.update(_get_booking_extra(bid))
                            match_tier = 1
                            narrowing_path = "zendesk_requester_auto"
                            _ctr["t2_zendesk_auto"] += 1
                            _ctr["t1_auto_promoted"] += 1
                            _ctr["t2_auto_promoted"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1 auto-promote</strong> via Zendesk requester "
                                        "(single verified match)"})
                            cascade_done = True
                        elif 2 <= n_zd <= 5:
                            candidates = [_make_candidate(row, "zendesk_requester", ["name", "zendesk"])
                                          for _, row in zd_candidates]
                            for i, (bid, _) in enumerate(zd_candidates):
                                candidates[i]["id"] = bid
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "zendesk_requester_candidates"
                            _ctr["t2_zendesk_candidates"] += 1
                            _ctr["t2_candidates"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": f"<strong>Tier 2:</strong> {n_zd} Zendesk-verified candidates"})
                            cascade_done = True
                        else:
                            _ctr["t2_zendesk_no_match"] += 1
                            if n_zd > 5:
                                confidence_trail.append({"mark": "warn",
                                    "text": f"<strong>Zendesk:</strong> {n_zd} verified — too many to narrow"})
                            else:
                                confidence_trail.append({"mark": "warn",
                                    "text": "<strong>Zendesk:</strong> 0 verified candidates after cross-check"})
                    else:
                        _ctr["t2_zendesk_no_match"] += 1
                        confidence_trail.append({"mark": "warn",
                            "text": "<strong>Zendesk:</strong> no BIDs found for this name"})

                # ── Step 3: BQ narrowing — venue+date only, never name ─────────
                if not cascade_done:
                    if tgids:
                        # 3a: venue + date_30
                        rows = _run_bq_attempt("venue_date_30", 30, tgid_list=tgids)
                        n = len(rows)
                        if n == 1:
                            booking = _make_candidate(rows[0], "venue_date_30_auto", ["venue", "date"])
                            booking.update(_get_booking_extra(booking.get("id", "")))
                            match_tier = 1
                            narrowing_path = "venue_date_30_auto"
                            _ctr["t2_bq_venue_date_30_auto"] += 1
                            _ctr["t1_auto_promoted"] += 1
                            _ctr["t2_auto_promoted"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1 auto-promote</strong> via venue+date_30 (single match)"})
                            cascade_done = True
                        elif 2 <= n <= 5:
                            candidates = [_make_candidate(r, "venue_date_30", ["venue", "date"])
                                          for r in rows[:5]]
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "venue_date_30"
                            _ctr["t2_bq_venue_date_30"] += 1
                            _ctr["t2_candidates"] += 1
                            cascade_done = True

                    if not cascade_done and tgids:
                        # 3b: venue + date_60
                        rows = _run_bq_attempt("venue_date_60", 60, tgid_list=tgids)
                        n = len(rows)
                        if n == 1:
                            booking = _make_candidate(rows[0], "venue_date_60_auto", ["venue", "date"])
                            booking.update(_get_booking_extra(booking.get("id", "")))
                            match_tier = 1
                            narrowing_path = "venue_date_60_auto"
                            _ctr["t2_bq_venue_date_60_auto"] += 1
                            _ctr["t1_auto_promoted"] += 1
                            _ctr["t2_auto_promoted"] += 1
                            confidence_trail.append({"mark": "pass",
                                "text": "<strong>Tier 1 auto-promote</strong> via venue+date_60 (single match)"})
                            cascade_done = True
                        elif 2 <= n <= 10:
                            candidates = [_make_candidate(r, "venue_date_60", ["venue", "date"])
                                          for r in rows[:10]]
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "venue_date_60"
                            _ctr["t2_bq_venue_date_60"] += 1
                            _ctr["t2_candidates"] += 1
                            cascade_done = True

                    if not cascade_done:
                        # 3c: date_14 only — no venue filter
                        rows = _run_bq_attempt("date_only", 14, tgid_list=None)
                        n = len(rows)
                        if 1 <= n <= 5:
                            candidates = [_make_candidate(r, "date_only_loose", ["date"])
                                          for r in rows[:5]]
                            candidate_state = True
                            match_tier = 2
                            narrowing_path = "date_only_loose"
                            _ctr["t2_bq_date_only_loose"] += 1
                            _ctr["t2_candidates"] += 1
                            cascade_done = True
                        else:
                            # Step 4 — Untraceable
                            _ctr["t2_untraceable"] += 1
                            _ctr["untraceable_total"] += 1
                            if venue_hints or author_first or author_last:
                                _ctr["untraceable_has_signals"] += 1
                            cascade_done = True

                # ── Step 4: Untraceable fallback ──────────────────────────────
                if not cascade_done and not booking:
                    _ctr["t2_untraceable"] += 1
                    _ctr["untraceable_total"] += 1
                    if venue_hints or author_first or author_last:
                        _ctr["untraceable_has_signals"] += 1

                # Do NOT auto-assign Tier 2 candidates as the confirmed booking.
                # Candidates are kept for display; booking stays None until an
                # associate confirms one via select-candidate.

                log.info(
                    f"[tier1] auto_promoted_from_cascade={_ctr['t1_auto_promoted']}"
                )
                log.info(
                    f"[tier2] zendesk_lookup: attempted={_ctr['t2_zendesk_lookup_attempted']} "
                    f"auto={_ctr['t2_zendesk_auto']} candidates={_ctr['t2_zendesk_candidates']} "
                    f"no_match={_ctr['t2_zendesk_no_match']}"
                )
                log.info(
                    f"[tier2] bq_paths: v30_auto={_ctr['t2_bq_venue_date_30_auto']} "
                    f"v30={_ctr['t2_bq_venue_date_30']} v60_auto={_ctr['t2_bq_venue_date_60_auto']} "
                    f"v60={_ctr['t2_bq_venue_date_60']} date_only={_ctr['t2_bq_date_only_loose']}"
                )
                log.info(
                    f"[tier2] outcomes: auto_promoted_to_t1={_ctr['t2_auto_promoted']} | "
                    f"candidate_list={_ctr['t2_candidates']} | untraceable={_ctr['t2_untraceable']}"
                )
                log.info(
                    f"[untraceable] total={_ctr['untraceable_total']} | "
                    f"usable_signals_present={_ctr['untraceable_has_signals']}"
                )

                confidence_trail.append({
                    "mark": "pass",
                    "text": f"<strong>Final:</strong> Tier {match_tier} via {narrowing_path or 'none'}",
                })

        # ── 6. Zendesk timeline ──────────────────────────────────────────────
        timeline      = []
        extracted_bk  = {}
        zd_meta       = {"ticket_ids": [], "timeline_raw": []}
        bid_for_zd    = (booking or {}).get("id") or review.reference_number
        _zd_pub_date  = review.received_at.strftime("%Y-%m-%d") if review.received_at else ""
        if bid_for_zd:
            try:
                timeline, extracted_bk, zd_meta = await zendesk.get_timeline(
                    bid_for_zd, review_id,
                    booking=booking,
                    review_body=review.body_english or review.body_original or "",
                    review_pub_date=_zd_pub_date,
                )
                log.info(f"[pipeline] timeline: {len(timeline)} events "
                         f"across tickets {zd_meta.get('ticket_ids')}")
            except Exception as e:
                log.warning(f"Zendesk failed — continuing with empty timeline: {e}")
                timeline, extracted_bk = [], {}
                zd_meta = {"ticket_ids": [], "timeline_raw": []}

        # ── 6b. Ticket fact extraction (Claude, runs concurrently with support frames) ──
        ticket_facts = {}
        _timeline_raw_for_extraction = zd_meta.get("timeline_raw", [])
        _timeline_raw_ticket_ids = zd_meta.get("timeline_raw_ticket_ids", [])
        async def _extract_facts():
            try:
                return await claude.extract_ticket_facts(
                    booking or {},
                    _timeline_raw_for_extraction,
                    _timeline_raw_ticket_ids,
                )
            except Exception as e:
                log.warning(f"Ticket fact extraction failed — continuing: {e}")
                return {}

        # ── Start fact extraction concurrently (result collected before save) ──
        _facts_task = asyncio.ensure_future(_extract_facts())

        # Merge Zendesk-extracted fields as fallback for missing BQ fields
        if extracted_bk and booking:
            for key in ("tgid", "tid", "vid", "experienceName", "visitDate",
                        "vendorName", "pax"):
                if not booking.get(key) and extracted_bk.get(key):
                    booking[key] = extracted_bk[key]
            if "ticket_mail_seen" in extracted_bk:
                booking["ticket_mail_seen"] = bool(extracted_bk["ticket_mail_seen"])

        # ── 7b. Support frames (Claude summarisation of each timeline event) ─
        support_frames = []
        sp_frames      = []
        for i, ev in enumerate(timeline):
            prev_ = timeline[i - 1] if i > 0 else None
            next_ = timeline[i + 1] if i + 1 < len(timeline) else None
            try:
                frame = await claude.summarise_support_event(ev, prev_, next_)
                merged = {**ev, **(frame or
                    {"guestSaid": "", "weDid": "", "guestReply": "", "gap": ""})}
            except Exception:
                merged = {**ev, "guestSaid": "", "weDid": "", "guestReply": "", "gap": ""}
            if ev.get("thread") == "sp":
                sp_frames.append(merged)
            else:
                support_frames.append(merged)

        support_summary_text = ""
        try:
            support_summary_text = await claude.summarise_support_arc(support_frames)
        except Exception as e:
            log.exception(f"Support summary failed: {e}")

        # ── 7. Insights (moved to after step 11 — needs L1/L2) ──────────────

        # ── 8. Similar complaints ─────────────────────────────────────────────
        similar_support = []
        similar_reviews = []
        if booking:
            try:
                similar_support, similar_reviews = await bq.get_similar_complaints(booking)
            except Exception as e:
                log.exception(f"Similar complaints failed: {e}")

        # ── 9. DSS placeholder — runs after classification (step 11d) ───────────
        dss_rec = {}

        # ── 10. Stated Issue ──────────────────────────────────────────────────
        stated_issue = ""
        try:
            stated_issue = await claude.stated_issue(review_text, review_id)
        except Exception as e:
            log.exception(f"Stated issue failed: {e}")

        # ── 11. Classification ────────────────────────────────────────────────
        l1, l2, l1_reasoning, sub_theme = "", "", "", None
        try:
            from server.services.classifier import classify as classify_v2
            from server.services.claude import _call as claude_call
            result = await classify_v2(review_text, booking, timeline, claude_call, review_id)
            l1 = result.l1
            l2 = result.l2
            sub_theme = result.sub_theme
            l1_reasoning = result.reasoning
            for w in result.warnings:
                log.warning(f"[classify {review_id}] {w}")
        except Exception as e:
            log.exception(f"Classification failed: {e}")

        # ── 11b. Warehouse L1/L2 comparison (log-only; Claude stays authoritative)
        try:
            _bid = (booking or {}).get("id")
            if _bid:
                _wh = await bq.get_l1_l2_by_bid(_bid)
                if _wh.get("l1") or _wh.get("l2"):
                    log.info(
                        f"[classify {review_id}] L1/L2 comparison for BID {_bid} — "
                        f"Claude: {l1!r} / {l2!r} | warehouse: {_wh['l1']!r} / {_wh['l2']!r}")
        except Exception as e:
            log.exception(f"Warehouse L1/L2 lookup failed: {e}")

        # ── 11c. Insights (after classification — needs L1/L2) ────────────────
        insights = {}
        if booking and booking.get("tid") and booking.get("vid"):
            try:
                insights = await _get_insights(booking, l1 or None, l2 or None)
            except Exception as e:
                log.exception(f"Insights failed: {e}")

        # ── 11d. DSS (after classification — scores on L1/L2/review_text) ────
        try:
            dss_rec = await dss.get_recommendation(
                booking or {}, review_id, l1=l1, l2=l2, review_text=review_text or "")
        except Exception as e:
            log.exception(f"DSS failed: {e}")

        # ── 12. Full structured RCA ───────────────────────────────────────────
        rca_v2 = {}
        try:
            rca_v2 = await claude.generate_rca_v2(
                review_text, booking, timeline, insights, dss_rec, l1, l2, review_id)
        except Exception as e:
            log.exception(f"RCA v2 generation failed: {e}")

        # ── 12b. RCA v3 (TL;DR + WWR chain + checklist) ──────────────────────
        rca_v3 = {}
        try:
            from server.services.rca_checklist import get_checklist
            checklist = await get_checklist(l1, l2)
            rca_v3 = await claude.generate_rca_v3(
                review_text=review_text,
                booking=booking,
                timeline=timeline,
                insights=insights,
                dss_rec=dss_rec,
                l1=l1 or "",
                l2=l2 or "",
                sub_theme=sub_theme or "",
                support_summary=support_summary_text or "",
                checklist=checklist,
                review_id=review_id,
                timeline_raw=zd_meta.get("timeline_raw", []),
            )
        except Exception as e:
            log.exception(f"RCA v3 generation failed: {e}")

        # ── 6c. Collect ticket fact extraction result ─────────────────────────
        try:
            ticket_facts = await _facts_task
            if ticket_facts:
                log.info(f"[pipeline] ticket_facts extracted: {list(ticket_facts.keys())}")
        except Exception as e:
            log.warning(f"Ticket fact extraction collect failed: {e}")
            ticket_facts = {}

        # ── 13. Response draft ────────────────────────────────────────────────
        response_draft = ""
        try:
            canned         = await get_canned_responses(l1, l2, sub_theme, review_text or "")
            response_draft = await claude.draft_response_v2(
                review_text=review_text,
                l1=l1,
                l2=l2,
                resolution=rca_v2.get("resolution", ""),
                review_id=review_id,
                guest_name=(booking or {}).get("guestName") or (review.author or ""),
                dss_rec=dss_rec,
                canned_list=canned,
            )
        except Exception as e:
            log.exception(f"Response draft failed: {e}")

        # ── 14. Save ──────────────────────────────────────────────────────────
        draft = db.query(RcaDraft).filter(RcaDraft.review_id == review_id).first()
        if not draft:
            draft = RcaDraft(id=f"draft_{review_id}", review_id=review_id)
            db.add(draft)

        _match = (booking or {}).get("_match", {})
        booking_to_save = {k: v for k, v in (booking or {}).items() if k != "_match"}
        zd_requester = zd_meta.get("zendesk_requester_name", "")
        if zd_requester:
            booking_to_save["zendesk_requester_name"] = zd_requester
        draft.booking              = booking_to_save
        draft.match_tier           = match_tier or _match.get("tier")
        draft.match_confidence     = _match.get("confidence")
        draft.match_method         = _match.get("method") or narrowing_path
        draft.candidates_list      = candidates
        draft.candidate_state      = candidate_state
        draft.confidence_trail     = confidence_trail
        draft.bid_source           = bid_source
        draft.extracted_signals    = extracted_sigs or {}
        draft.narrowing_attempts   = narrowing_attempts or []
        draft.timeline         = timeline
        draft.insights         = insights
        draft.similar_support  = similar_support
        draft.similar_reviews  = similar_reviews
        draft.dss_rec          = dss_rec
        draft.zendesk_ticket_ids = zd_meta.get("ticket_ids", [])
        draft.timeline_raw       = zd_meta.get("timeline_raw", [])

        draft.stated_issue                = stated_issue
        draft.l1                          = l1
        draft.l2                          = l2
        draft.l1_reasoning                = l1_reasoning
        draft.sub_theme                   = sub_theme
        draft.diagnostic_checks           = rca_v2.get("diagnosticChecks", [])
        draft.what_went_wrong_bullets     = rca_v2.get("whatWentWrongBullets", [])
        # Zendesk-derived frames (step 7b) are authoritative; RCA output is fallback.
        draft.support_interaction_frames  = support_frames or rca_v2.get("supportInteractionFrames", [])
        draft.support_summary             = support_summary_text or rca_v2.get("supportSummary", "")
        draft.sp_interaction_frames       = sp_frames or rca_v2.get("spInteractionFrames", [])
        draft.area_of_improving           = rca_v2.get("areaOfImproving", [])
        draft.actions_taken               = rca_v2.get("actionsTaken",
                                              {"sp":[],"customer":[],"business":[],"product":[],"ce":[]})
        draft.resolution                  = rca_v2.get("resolution", "")

        # v3 fields — independent of v2, both persist. Always assign (even when
        # rca_v3 is empty) so the attributes are present in the object state and
        # the flag_modified() loop below never fails on an unset attribute.
        _v3 = rca_v3 or {}
        draft.tldr                    = _v3.get("tldr")
        draft.wwr_chain               = _v3.get("wwr_chain") or []
        draft.prevention              = _v3.get("prevention")
        draft.evidence                = _v3.get("evidence") or []
        draft.issue_specific_answers  = _v3.get("issue_specific_answers") or {}
        draft.checklist_answers       = _v3.get("checklist_answers") or []

        draft.ticket_facts                = ticket_facts or None
        draft.suggested_response          = response_draft
        draft.generated_at                = datetime.utcnow()
        review.status                     = "draft"

        # Force SQLAlchemy to detect JSON column changes on re-runs
        # (JSON type does not track in-place mutations automatically)
        for _col in (
            "booking", "candidates_list", "confidence_trail",
            "extracted_signals", "narrowing_attempts",
            "timeline", "insights", "similar_support", "similar_reviews",
            "dss_rec", "zendesk_ticket_ids", "timeline_raw",
            "diagnostic_checks", "what_went_wrong_bullets",
            "support_interaction_frames", "sp_interaction_frames",
            "area_of_improving", "actions_taken",
            "wwr_chain", "evidence", "issue_specific_answers", "checklist_answers",
            "ticket_facts",
        ):
            try:
                flag_modified(draft, _col)
            except Exception:
                # Attribute not present in object state (never assigned this run) —
                # nothing to flag, and it must not abort the commit.
                pass

        db.commit()

        # ── 15. Slack post-back — disabled until explicitly re-enabled ──────────
        # Do not post anything to Slack threads from the pipeline.

        # ── 16. Metrics ───────────────────────────────────────────────────────
        try:
            m = db.query(ReviewMetric).filter(ReviewMetric.review_id == review_id).first()
            if not m:
                m = ReviewMetric(review_id=review_id)
                db.add(m)
            m.received_at      = review.received_at
            m.channel          = review.slack_channel
            m.rating           = review.rating
            m.language         = review.language
            m.match_tier       = draft.match_tier
            m.match_confidence = draft.match_confidence
            m.auto_matched     = draft.match_tier in (1, 2)
            m.l1               = l1
            m.l2               = l2
            db.commit()
        except Exception as e:
            log.exception(f"Metrics write failed: {e}")

    finally:
        db.close()
