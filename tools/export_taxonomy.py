#!/usr/bin/env python3
"""Export the live classification taxonomy — L1 / L2 / sub-themes, and the
SOP-side material hanging off each pair — as CSV and HTML.

    python3 tools/export_taxonomy.py                 # -> ./taxonomy_export/
    python3 tools/export_taxonomy.py --out somewhere

WHY A TOOL AND NOT A ONE-OFF PASTE. The taxonomy is edited in
`server/taxonomy.py` and read by prompts.py and the validators in
services/claude.py. A spreadsheet someone typed by hand goes stale the first
time an L2 is added and nothing says it has — the reader cannot tell a current
export from a six-month-old one. So this reads the SAME module the running
system reads, and stamps every output with the commit it was generated from.

WHAT "EVERYTHING ELSE THAT FALLS UNDER IT" MEANS HERE. Four things attach to a
classification, and they attach at different levels — saying which is the whole
point of the coverage sheet:

  * sub-themes        per (L1, L2), from SUB_THEME_REGISTRY. Not every pair has
                      a framework; the module's own header lists several as
                      PENDING, and a pair with no framework is reported as such
                      rather than as a pair with zero sub-themes.
  * diagnostic checks per L1, from DIAGNOSTIC_CHECKS — the questions the RCA
                      panel asks, and the timeline field each is answered from.
  * support tags      per (L1, L2), from SUPPORT_TAG_MAP — the Zendesk tags
                      that map onto the pair.
  * gap taxonomy      global, from GAP_TAXONOMY — the CE-failure labels.

A pair that is absent from a map and a pair mapped to an empty list are
different facts and are printed differently. Reporting "0 tags" for a pair
nobody has mapped yet would make an unfinished framework look like a finished
one with nothing in it.
"""
import argparse
import csv
import html
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MISSING = "— not mapped —"        # nobody has written this yet
EMPTY = "(mapped, but empty)"     # somebody wrote it and it is deliberately []


def _commit() -> str:
    """The commit this export reflects, so a stale sheet can be spotted."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        d = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
        rev = (h.stdout or "").strip() or "unknown"
        return rev + (" +uncommitted changes" if (d.stdout or "").strip() else "")
    except Exception:
        return "unknown (not a git checkout)"


def rows(t):
    """One row per (L1, L2, sub-theme), plus the SOP material on each.

    A pair with no sub-theme framework still emits ONE row, with the sub-theme
    columns marked not-mapped. Dropping it would silently shrink the catalogue
    to the parts that happen to be finished — the reader would never learn the
    pair exists.
    """
    out = []
    for l1 in t.L1_CATEGORIES:
        l2s = t.L2_OPTIONS.get(l1) or []
        if not l2s:
            out.append({"l1": l1, "l2": MISSING, "sub_code": "", "sub_theme": "",
                        "keywords": "", "framework": MISSING})
            continue
        for l2 in l2s:
            fw = t.SUB_THEME_REGISTRY.get((l1, l2))
            base = {"l1": l1, "l2": l2}
            if fw is None:
                out.append({**base, "sub_code": "", "sub_theme": MISSING,
                            "keywords": "", "framework": MISSING})
                continue
            subs = fw.get("sub_themes") or []
            fw_name = fw.get("l2_key") or l2
            if not subs:
                out.append({**base, "sub_code": "", "sub_theme": EMPTY,
                            "keywords": "", "framework": fw_name})
            for entry in subs:
                code, name, kws = (list(entry) + ["", "", []])[:3]
                out.append({**base, "sub_code": code, "sub_theme": name,
                            "keywords": "; ".join(kws or []),
                            "framework": fw_name})
            # The exclusion bucket is part of the framework and is how a review
            # gets classified OUT of it — omitting it would show only the ways
            # in.
            if fw.get("exclusion"):
                out.append({**base, "sub_code": "",
                            "sub_theme": fw.get("exclusion_label")
                                         or "(exclusion)",
                            "keywords": "; ".join(fw.get("exclusion") or []),
                            "framework": fw_name})
    return out


def sop_rows(t):
    """The per-pair SOP material: diagnostic checks (per L1) and support tags."""
    out = []
    for l1 in t.L1_CATEGORIES:
        checks = t.DIAGNOSTIC_CHECKS.get(l1)
        for l2 in (t.L2_OPTIONS.get(l1) or [MISSING]):
            tags = t.SUPPORT_TAG_MAP.get((l1, l2))
            out.append({
                "l1": l1, "l2": l2,
                "diagnostic_checks": (MISSING if checks is None else
                                      EMPTY if not checks else
                                      " | ".join(c.get("question", "") for c in checks)),
                "check_sources": (MISSING if checks is None else
                                  EMPTY if not checks else
                                  " | ".join(c.get("data_source", "") for c in checks)),
                "support_tags": (MISSING if tags is None else
                                 EMPTY if not tags else " | ".join(tags)),
                "support_tag_count": ("" if tags is None else str(len(tags))),
            })
    return out


def coverage(t):
    """Which pairs have a sub-theme framework and which do not.

    The module header lists five frameworks as PENDING. This is that list,
    computed rather than transcribed, so it cannot drift from the code.
    """
    have, missing = [], []
    for l1 in t.L1_CATEGORIES:
        for l2 in (t.L2_OPTIONS.get(l1) or []):
            (have if (l1, l2) in t.SUB_THEME_REGISTRY else missing).append((l1, l2))
    return have, missing


def _csv(path, fieldnames, data):
    # utf-8-sig: Excel opens a plain utf-8 CSV as cp1252 and turns every
    # accented venue name into mojibake. The BOM is what makes it read as utf-8.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(data)


def _html_doc(t, tax, sop, have, missing, stamp) -> str:
    e = html.escape

    def table(cols, data, keys):
        head = "".join(f"<th>{e(c)}</th>" for c in cols)
        body = []
        for r in data:
            cells = []
            for k in keys:
                v = str(r.get(k, ""))
                cls = ' class="missing"' if v == MISSING else (
                      ' class="empty"' if v == EMPTY else "")
                cells.append(f"<td{cls}>{e(v)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table></div>')

    gaps = "".join(f"<li>{e(g)}</li>" for g in t.GAP_TAXONOMY)
    tabs = "".join(
        f"<li><b>{e(k)}</b> — {e(v.get('label',''))} "
        f"<span class=dim>{e(v.get('default_handle',''))}</span></li>"
        for k, v in t.ACTION_TABS.items())
    miss = "".join(f"<li>{e(a)} › {e(b)}</li>" for a, b in missing) or "<li>none</li>"
    l1order = "".join(f"<li>{e(x)}</li>" for x in t.L1_PRIORITY_ORDER)
    opsorder = "".join(f"<li>{e(x)}</li>" for x in t.OPERATIONS_L2_PRIORITY_ORDER)

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Classification taxonomy — L1 / L2 / sub-themes</title>
<style>
 :root {{ --fg:#1a1a1a; --dim:#6b6b6b; --line:#e3e3e3; --bg:#fff;
          --amber:#B8860B; --head:#f6f6f6; }}
 @media (prefers-color-scheme: dark) {{
   :root {{ --fg:#e8e8e8; --dim:#9a9a9a; --line:#333; --bg:#151515;
            --amber:#d9a441; --head:#1e1e1e; }} }}
 body {{ font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         color:var(--fg); background:var(--bg); margin:0; padding:32px; }}
 h1 {{ font-size:22px; margin:0 0 4px; }}
 h2 {{ font-size:15px; margin:34px 0 10px; padding-bottom:6px;
       border-bottom:1px solid var(--line); }}
 .stamp {{ color:var(--dim); font-size:12px; margin-bottom:22px; }}
 .scroll {{ overflow-x:auto; }}
 table {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
 th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
          vertical-align:top; }}
 th {{ background:var(--head); position:sticky; top:0; font-weight:600; }}
 td.missing {{ color:var(--amber); font-style:italic; }}
 td.empty {{ color:var(--dim); font-style:italic; }}
 ul {{ margin:6px 0; padding-left:20px; }}
 .dim {{ color:var(--dim); }}
 .note {{ border-left:3px solid var(--amber); padding:8px 12px; margin:12px 0;
          background:color-mix(in srgb, var(--amber) 8%, transparent); }}
</style>
<h1>Classification taxonomy</h1>
<div class="stamp">Generated from <code>server/taxonomy.py</code> at commit
 <b>{e(stamp)}</b> — the same module the running pipeline reads.</div>

<div class="note"><b>Two kinds of blank.</b>
 <span class="missing" style="color:var(--amber)"><i>{e(MISSING)}</i></span>
 means nobody has written that mapping yet — five sub-theme frameworks are
 listed PENDING in the module header.
 <i class="dim">{e(EMPTY)}</i> means somebody wrote it and it is deliberately
 empty. Printing both as "0" would make an unfinished framework look finished.
</div>

<h2>L1 priority order <span class=dim>(higher wins ties)</span></h2><ol>{l1order}</ol>
<h2>Operations Issue — L2 priority order</h2><ol>{opsorder}</ol>

<h2>L1 › L2 › sub-theme ({len(tax)} rows)</h2>
{table(["L1","L2","Code","Sub-theme","Keywords / triggers","Framework"],
       tax, ["l1","l2","sub_code","sub_theme","keywords","framework"])}

<h2>SOP material per pair</h2>
{table(["L1","L2","Diagnostic checks","Answered from","Zendesk support tags","#"],
       sop, ["l1","l2","diagnostic_checks","check_sources","support_tags",
             "support_tag_count"])}

<h2>Sub-theme framework coverage</h2>
<p>{len(have)} of {len(have)+len(missing)} L1/L2 pairs have a framework.
 Pairs still without one:</p><ul>{miss}</ul>

<h2>Gap taxonomy <span class=dim>(global — CE failure labels)</span></h2><ul>{gaps}</ul>
<h2>Action tabs</h2><ul>{tabs}</ul>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="taxonomy_export")
    a = ap.parse_args()

    import server.taxonomy as t

    os.makedirs(a.out, exist_ok=True)
    stamp = _commit()
    tax, sop = rows(t), sop_rows(t)
    have, missing = coverage(t)

    _csv(os.path.join(a.out, "taxonomy_l1_l2_subthemes.csv"),
         ["l1", "l2", "sub_code", "sub_theme", "keywords", "framework"], tax)
    _csv(os.path.join(a.out, "taxonomy_sop_per_pair.csv"),
         ["l1", "l2", "diagnostic_checks", "check_sources", "support_tags",
          "support_tag_count"], sop)
    with open(os.path.join(a.out, "taxonomy.html"), "w", encoding="utf-8") as f:
        f.write(_html_doc(t, tax, sop, have, missing, stamp))

    # Say what was produced AND what is not there to produce. A coverage line
    # that only counted what exists would read as a complete catalogue.
    print(f"commit: {stamp}")
    print(f"{len(tax)} taxonomy row(s), {len(sop)} SOP row(s) -> {a.out}/")
    print(f"sub-theme frameworks: {len(have)} of {len(have)+len(missing)} "
          f"L1/L2 pairs mapped; {len(missing)} still without one:")
    for l1, l2 in missing:
        print(f"    {l1} > {l2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
