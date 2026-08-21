/**
 * Classifier audit — the training-loop surface, inside the sheet.
 *
 * WHAT IT DOES. You paste reviews and the label a human gave them into a tab.
 * You click "Classifier ▸ Classify + score rows". This sends each row to the
 * Replit server's /api/classify-audit, which runs the SAME classifier the
 * dashboard runs, scores its answer against your label, and hands back a
 * prediction and a verdict. The script writes those into columns to the right
 * and toasts the accuracy. Misses carry a bucket saying where the fix lives.
 *
 * This is client-side Google Apps Script. It has no test harness (the Python
 * scoring it calls is driven and mutation-tested in
 * server/services/classifier_audit.py); the endpoint it hits has its own tests.
 *
 * ── SET UP (once) ────────────────────────────────────────────────────────
 *  1. Extensions ▸ Apps Script. Paste this whole file in. Save.
 *  2. Project Settings ▸ Script Properties, add two:
 *       ENDPOINT   https://YOUR-APP.replit.app/api/classify-audit
 *       AUDIT_KEY  the value you set as AUDIT_API_KEY in the Replit secrets
 *                  (leave blank only if you left AUDIT_API_KEY unset)
 *  3. Reload the sheet. A "Classifier" menu appears.
 *
 * ── THE SHEET ────────────────────────────────────────────────────────────
 *  A header row on top, then one review per row. Columns are found BY NAME
 *  (case-insensitive), in any order:
 *       review        the text to classify        (required)
 *       l1, l2, sub_theme   the correct labels     (what it scores against)
 *       review_id     optional
 *  Results are written into: pred_l1, pred_l2, pred_sub_theme, l1_ok, l2_ok,
 *  sub_ok, miss_bucket, warnings — created to the right if absent, overwritten
 *  in place on a re-run.
 */

var CHUNK = 20;      // rows per request — keeps each call short of any timeout
var RESULT_COLS = ['pred_l1', 'pred_l2', 'pred_sub_theme',
                   'l1_ok', 'l2_ok', 'sub_ok', 'miss_bucket', 'warnings'];

// Sheets evaluates any cell whose text begins with = + - @ (or a leading tab /
// carriage return) as a formula. The values we write back are not all tame:
// `warnings` carries model warnings and the raw exception string, which is
// arbitrary text an odd review can steer. A warning that opens with `=` would
// otherwise land as a live formula in CX's audit sheet. Leading it with an
// apostrophe forces Sheets to store the whole thing as text (the apostrophe is
// the text-prefix marker and is not itself displayed). No test harness here —
// this is Apps Script — so it is asserted by reading, not by a test.
function defuse_(s) {
  return /^[=+\-@\t\r]/.test(s) ? "'" + s : s;
}

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Classifier')
    .addItem('Classify + score rows', 'classifyAndScore')
    .addToUi();
}

function _canon(s) {
  return String(s == null ? '' : s).trim().toLowerCase().replace(/[\s_]+/g, '_');
}

/** field -> 0-based column index, by header name. */
function _detect(header) {
  var alias = {
    review_id: ['review_id', 'reviewid', 'id', 'tp_id'],
    review: ['review', 'review_text', 'text', 'body', 'review_summary'],
    l1: ['l1', 'l1_category'],
    l2: ['l2', 'l2_category', 'sub_category'],
    sub_theme: ['sub_theme', 'subtheme', 'sub_themes']
  };
  var canon = header.map(_canon);
  var out = {};
  Object.keys(alias).forEach(function (field) {
    for (var i = 0; i < canon.length; i++) {
      if (alias[field].indexOf(canon[i]) !== -1 && out[field] === undefined) {
        out[field] = i;
        break;
      }
    }
  });
  return out;
}

/** RESULT_COLS -> 0-based index, reusing any already present so a re-run
 *  overwrites the previous predictions instead of opening a second block. */
function _resultCols(header) {
  var canon = header.map(_canon);
  var next = header.length;
  var out = {};
  RESULT_COLS.forEach(function (c) {
    var at = canon.indexOf(_canon(c));
    if (at !== -1) { out[c] = at; } else { out[c] = next++; }
  });
  return out;
}

function classifyAndScore() {
  var ui = SpreadsheetApp.getUi();
  var props = PropertiesService.getScriptProperties();
  var endpoint = props.getProperty('ENDPOINT');
  var key = props.getProperty('AUDIT_KEY') || '';
  if (!endpoint) {
    ui.alert('Set ENDPOINT in Project Settings ▸ Script Properties first ' +
             '(https://YOUR-APP.replit.app/api/classify-audit).');
    return;
  }

  var sheet = SpreadsheetApp.getActiveSheet();
  var range = sheet.getDataRange();
  var values = range.getValues();
  if (values.length < 2) { ui.alert('No data rows under the header.'); return; }

  var header = values[0];
  var cols = _detect(header);
  if (cols.review === undefined) {
    ui.alert('No "review" column found. Add one named review / review_text / ' +
             'review_summary.');
    return;
  }
  if (cols.l1 === undefined) {
    ui.alert('No "l1" column — there is nothing to score against. Add your ' +
             'correct l1 / l2 / sub_theme columns.');
    return;
  }

  var data = values.slice(1);
  var payloadRows = data.map(function (row) {
    return {
      review_id: cols.review_id !== undefined ? String(row[cols.review_id] || '') : '',
      review: String(row[cols.review] || ''),
      l1: cols.l1 !== undefined ? String(row[cols.l1] || '') : '',
      l2: cols.l2 !== undefined ? String(row[cols.l2] || '') : '',
      sub_theme: cols.sub_theme !== undefined ? String(row[cols.sub_theme] || '') : ''
    };
  });

  // Write the result headers (creating the columns if needed).
  var rc = _resultCols(header);
  RESULT_COLS.forEach(function (c) {
    sheet.getRange(1, rc[c] + 1).setValue(c);
  });

  var all = [];
  var totals = { scored: 0, l1: 0, l1of: 0, l2: 0, l2of: 0, sub: 0, subof: 0,
                 failed: 0 };
  for (var start = 0; start < payloadRows.length; start += CHUNK) {
    var chunk = payloadRows.slice(start, start + CHUNK);
    sheet.toast('Classifying rows ' + (start + 1) + '–' +
                (start + chunk.length) + ' of ' + payloadRows.length + '…',
                'Classifier', 30);
    var res = _post(endpoint, key, { rows: chunk });
    if (res.error) {
      ui.alert('Request failed on rows starting ' + (start + 1) + ':\n' +
               res.error + '\n\nNothing further was written.');
      return;
    }
    (res.results || []).forEach(function (r) { all.push(r); });
    // roll the summary up as we go so a partial run still reports honestly
    var s = res.summary || {};
    totals.scored += s.rows_scored || 0;
    totals.failed += s.rows_failed || 0;
    if (s.l1) { totals.l1 += s.l1.hits || 0; totals.l1of += s.l1.of || 0; }
    if (s.l1_l2) { totals.l2 += s.l1_l2.hits || 0; totals.l2of += s.l1_l2.of || 0; }
    if (s.sub) { totals.sub += s.sub.hits || 0; totals.subof += s.sub.of || 0; }
  }

  // Write results back, one row at a time in sheet order.
  var startCol = Math.min.apply(null, RESULT_COLS.map(function (c) { return rc[c]; }));
  for (var i = 0; i < all.length; i++) {
    var r = all[i];
    var line = RESULT_COLS.map(function (c) {
      var map = { pred_l1: r.pred_l1, pred_l2: r.pred_l2,
                  pred_sub_theme: r.pred_sub_theme, l1_ok: r.l1_ok,
                  l2_ok: r.l2_ok, sub_ok: r.sub_ok, miss_bucket: r.miss_bucket,
                  warnings: r.warnings };
      var v = map[c];
      return v == null ? '' : defuse_(String(v));
    });
    sheet.getRange(i + 2, startCol + 1, 1, RESULT_COLS.length).setValues([line]);
  }

  var pct = function (h, o) { return o ? (Math.round(1000 * h / o) / 10) + '%  (' + h + '/' + o + ')' : 'not scored'; };
  ui.alert('Done — ' + all.length + ' row(s).\n\n' +
           'L1:      ' + pct(totals.l1, totals.l1of) + '\n' +
           'L1+L2:   ' + pct(totals.l2, totals.l2of) + '\n' +
           '+sub:    ' + pct(totals.sub, totals.subof) + '\n\n' +
           (totals.failed ? totals.failed + ' row(s) could not be scored ' +
            '(see the warnings column) — the rates above are OF the scored ' +
            'rows, not all ' + all.length + '.\n\n' : '') +
           'Reminder: a number only means the model LEARNED if these rows ' +
           'were not the ones its examples were drawn from.');
}

function _post(endpoint, key, body) {
  var headers = {};
  if (key) { headers['X-Audit-Key'] = key; }
  try {
    var resp = UrlFetchApp.fetch(endpoint, {
      method: 'post',
      contentType: 'application/json',
      headers: headers,
      payload: JSON.stringify(body),
      muteHttpExceptions: true
    });
    var code = resp.getResponseCode();
    var text = resp.getContentText();
    if (code !== 200) {
      return { error: 'HTTP ' + code + ': ' + text.slice(0, 300) };
    }
    return JSON.parse(text);
  } catch (e) {
    return { error: String(e) };
  }
}
