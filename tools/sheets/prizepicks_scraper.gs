/**
 * Edge Equation -- PrizePicks projections scraper for manual math testing.
 *
 * Goal: pull every active PrizePicks projection into a Sheet so you can
 * test the engine's math (fair-prob -> edge -> half-Kelly -> grade) by
 * hand against props the engine doesn't currently produce.
 *
 * ============================================================
 * SETUP (one time, ~5 minutes)
 * ============================================================
 *  1. Open https://sheets.new (creates a fresh Google Sheet).
 *  2. Extensions -> Apps Script. Delete the placeholder
 *     "function myFunction() {}".
 *  3. Paste this entire file. Save (Ctrl+S / Cmd+S). Name the project
 *     something like "EE PrizePicks Scraper".
 *  4. In the Apps Script toolbar choose function `setup` -> click Run.
 *     First run prompts for permissions:
 *       - "View and manage your spreadsheets"   (writing the tabs)
 *       - "Connect to an external service"      (calling api.prizepicks.com)
 *     Click Advanced -> "Go to <project> (unsafe)" -> Allow.
 *     This is normal for personal Apps Scripts; the script never sends
 *     your data anywhere except the PrizePicks public API.
 *  5. Reload the spreadsheet tab. A new "Edge Equation" menu appears.
 *     Click Edge Equation -> Scrape projections to populate `_raw`.
 *  6. (Optional) Apps Script -> Triggers (clock icon, left rail) ->
 *     Add trigger -> function `scrapeProjections`, event source
 *     "Time-driven", every hour. The raw tab will refresh automatically.
 *
 * ============================================================
 * USAGE
 * ============================================================
 *  - `_raw` is owned by the scraper -- it gets wiped + rewritten on
 *    every run. Don't add columns there.
 *  - `picks` is your worksheet. Copy a row's `projection_id` from `_raw`
 *    into column A, and the player/stat columns auto-fill via VLOOKUP.
 *    Then enter your fair-probability estimate (decimal, e.g. 0.62) in
 *    the `your_prob` column and the edge / half-Kelly / grade columns
 *    populate themselves.
 *  - `tracking` is for after the game is done. Record the actual stat
 *    and the sheet computes hit/push/miss + P&L in units.
 *
 * ============================================================
 * FORMULA LANGUAGE (matches engine/math/probability.py)
 * ============================================================
 *  break_even = sqrt(1 / payout_multiplier)         per leg, power play
 *  edge       = your_prob - break_even
 *  decimal_b  = payout_multiplier^(1/n_legs) - 1    e.g. 2-pick: ~0.732
 *  half_kelly = max(0, min(0.25, (p*b - (1-p)) / b / 2))
 *  grade      = bucketed by edge magnitude:
 *               edge >= 0.10 -> A+   (huge mispricing)
 *               edge >= 0.07 -> A
 *               edge >= 0.04 -> B
 *               edge >= 0.02 -> C
 *               edge >  0    -> D
 *               else         -> F
 *
 * The sheet defaults to the standard 2-pick power-play payout (3x).
 * Change cell `picks!M1` to override (e.g. 5 for 3-pick, 25 for 6-pick).
 * Demons / goblins have their own payout factor in the PrizePicks
 * `odds_type` field; the `_raw` tab surfaces it so you can check.
 */

const PP_API = 'https://api.prizepicks.com/projections';
const PER_PAGE = 250;
const RAW = '_raw';
const PICKS = 'picks';
const TRACK = 'tracking';

// Headers for each tab. Single source of truth -- referenced from setup
// and from scrape so column moves only need editing in one place.
const RAW_HEADERS = [
  'scraped_at', 'projection_id', 'league', 'sport', 'player', 'team',
  'position', 'description', 'stat_type', 'line', 'start_time', 'odds_type',
];
const PICKS_HEADERS = [
  'projection_id', 'player', 'team', 'league', 'stat_type', 'line',
  'pick (over/under)', 'your_prob', 'break_even', 'edge', 'half_kelly',
  'grade', 'notes',
];
const TRACK_HEADERS = [
  'projection_id', 'player', 'stat_type', 'line', 'pick',
  'actual', 'hit', 'units_risked', 'pl_units', 'note',
];

// onOpen runs each time the sheet is loaded. Adds a custom menu so the
// owner doesn't have to bounce to the Apps Script editor to scrape.
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Edge Equation')
    .addItem('Scrape projections', 'scrapeProjections')
    .addItem('Re-run setup (reset tabs)', 'setup')
    .addToUi();
}

// setup -- creates / resets the three tabs with headers and formulas.
// Safe to re-run; clears each tab before writing.
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  ensureSheet_(ss, RAW);
  ensureSheet_(ss, PICKS);
  ensureSheet_(ss, TRACK);

  writeHeader_(ss.getSheetByName(RAW), RAW_HEADERS);
  setupPicks_(ss.getSheetByName(PICKS));
  setupTracking_(ss.getSheetByName(TRACK));

  SpreadsheetApp.getUi().alert(
    'Setup complete.\n\n' +
    'Next: Edge Equation menu -> Scrape projections.\n' +
    'Then copy a projection_id from _raw into picks!A2 to test math.'
  );
}

function ensureSheet_(ss, name) {
  const existing = ss.getSheetByName(name);
  if (existing) {
    existing.clear();
    return existing;
  }
  return ss.insertSheet(name);
}

function writeHeader_(sheet, headers) {
  sheet
    .getRange(1, 1, 1, headers.length)
    .setValues([headers])
    .setFontWeight('bold')
    .setBackground('#1e2229')
    .setFontColor('#5BC0E8');
  sheet.setFrozenRows(1);
}

function setupPicks_(sheet) {
  writeHeader_(sheet, PICKS_HEADERS);

  // Payout multiplier control cell. Default 3 = standard 2-pick power.
  // Change to 5 for 3-pick, 10 for 4-pick, 20 for 5-pick, 25 for 6-pick.
  sheet.getRange('M1').setValue('payout_mult');
  sheet.getRange('N1').setValue(3).setFontWeight('bold');

  // Row 2 formulas. The user puts a projection_id in A2; everything else
  // auto-fills (player/team/league/stat/line via VLOOKUP into _raw, then
  // the math chain).
  const formulas = [[
    '',                                                              // A: projection_id (manual)
    '=IFERROR(VLOOKUP($A2, _raw!$B:$L, 4, FALSE), "")',              // B: player
    '=IFERROR(VLOOKUP($A2, _raw!$B:$L, 5, FALSE), "")',              // C: team
    '=IFERROR(VLOOKUP($A2, _raw!$B:$L, 2, FALSE), "")',              // D: league
    '=IFERROR(VLOOKUP($A2, _raw!$B:$L, 8, FALSE), "")',              // E: stat_type
    '=IFERROR(VLOOKUP($A2, _raw!$B:$L, 9, FALSE), "")',              // F: line
    '',                                                              // G: pick (over/under)
    '',                                                              // H: your_prob
    '=IF($N$1="", "", SQRT(1/$N$1))',                                // I: break_even
    '=IF(OR($H2="", $I2=""), "", $H2-$I2)',                          // J: edge
    '=IFERROR(IF(OR($H2="", $J2<=0), 0, ' +
      'MIN(0.25, MAX(0, ($H2*($N$1^(1/2)-1) - (1-$H2))/($N$1^(1/2)-1)/2))), 0)', // K: half_kelly
    '=IFS($J2="","",$J2>=0.10,"A+",$J2>=0.07,"A",$J2>=0.04,"B",' +
      '$J2>=0.02,"C",$J2>0,"D",TRUE,"F")',                           // L: grade
    '',                                                              // M: notes
  ]];
  sheet.getRange(2, 1, 1, PICKS_HEADERS.length).setValues(formulas);

  // Number formatting -- probabilities as %, kelly as %, line as decimal.
  sheet.getRange('F2:F').setNumberFormat('0.0');
  sheet.getRange('H2:H').setNumberFormat('0.00%');
  sheet.getRange('I2:J').setNumberFormat('0.00%');
  sheet.getRange('K2:K').setNumberFormat('0.00%');

  // Conditional format for grades (cyan A+ / A, dim D / F).
  const range = sheet.getRange('L2:L');
  const rules = [
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('A+').setBackground('#5BC0E8').setFontColor('#08090b').setRanges([range]).build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('A').setBackground('#A8DEF5').setFontColor('#08090b').setRanges([range]).build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo('F').setFontColor('#3a4050').setRanges([range]).build(),
  ];
  sheet.setConditionalFormatRules(rules);

  sheet.setColumnWidth(1, 110); // projection_id
  sheet.setColumnWidth(2, 160); // player
  sheet.setColumnWidth(13, 240); // notes
}

function setupTracking_(sheet) {
  writeHeader_(sheet, TRACK_HEADERS);

  // Row 2: hit/pl formulas; everything else manual.
  const formulas = [[
    '', '', '', '', '', '',
    // hit: 1 if pick covers, 0 if misses, "push" on exact line
    '=IF($F2="", "", IF($E2="over", IF($F2>$D2,1,IF($F2<$D2,0,"push")),' +
      'IF($F2<$D2,1,IF($F2>$D2,0,"push"))))',
    '',
    // pl_units: requires payout_mult on the picks tab (cell N1).
    // For a 2-pick @ 3x, a winning leg pays b = sqrt(3)-1 ~= 0.732 of risk.
    '=IF($G2="push",0,IF($G2=1,$H2*(picks!$N$1^(1/2)-1),' +
      'IF($G2=0,-$H2,"")))',
    '',
  ]];
  sheet.getRange(2, 1, 1, TRACK_HEADERS.length).setValues(formulas);

  sheet.getRange('H2:I').setNumberFormat('0.00');
}

// scrapeProjections -- one HTTP call -> JSON:API parse -> _raw rewrite.
//
// PrizePicks returns up to PER_PAGE projections per request; their public
// endpoint usually returns ~500-2000 active rows total in one pull when
// you don't filter by league. The script pages through until either
// links.next is empty or a page comes back empty.
function scrapeProjections() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const raw = ss.getSheetByName(RAW);
  if (!raw) {
    throw new Error('Run "Edge Equation -> Re-run setup" first.');
  }

  const players = {};
  const leagues = {};
  const allRows = [];
  const scrapedAt = new Date().toISOString();

  let page = 1;
  let safety = 0;
  while (safety++ < 30) {  // hard cap so a runaway loop can't burn the daily quota
    const url = `${PP_API}?per_page=${PER_PAGE}&page=${page}`;
    const resp = UrlFetchApp.fetch(url, {
      headers: {
        'Accept': 'application/json',
        // Plain user-agent. PrizePicks has been fine with this; if a future
        // run starts 403'ing, swap in a browser UA string here.
        'User-Agent': 'Mozilla/5.0 (compatible; EE-PP-Scraper/1.0)',
      },
      muteHttpExceptions: true,
    });
    const code = resp.getResponseCode();
    if (code !== 200) {
      throw new Error(
        `PrizePicks API returned HTTP ${code} on page ${page}: ` +
        resp.getContentText().slice(0, 200)
      );
    }

    const payload = JSON.parse(resp.getContentText());

    // Build / extend lookup tables from `included`. PrizePicks uses the
    // JSON:API spec -- player + league records arrive in a separate array,
    // referenced by relationship id from each projection.
    for (const inc of payload.included || []) {
      if (inc.type === 'new_player') players[inc.id] = inc.attributes || {};
      else if (inc.type === 'league') leagues[inc.id] = inc.attributes || {};
    }

    const dataArr = payload.data || [];
    if (dataArr.length === 0) break;

    for (const proj of dataArr) {
      const a = proj.attributes || {};
      const r = proj.relationships || {};
      const playerId = r.new_player && r.new_player.data ? r.new_player.data.id : null;
      const leagueId = r.league && r.league.data ? r.league.data.id : null;
      const pl = playerId ? players[playerId] || {} : {};
      const lg = leagueId ? leagues[leagueId] || {} : {};

      allRows.push([
        scrapedAt,
        proj.id,
        lg.name || '',
        lg.sport || '',
        pl.name || pl.display_name || '',
        pl.team || pl.team_name || '',
        pl.position || '',
        a.description || '',          // often "PLAYER vs OPP"
        a.stat_type || '',
        a.line_score != null ? a.line_score : '',
        a.start_time || '',
        a.odds_type || 'standard',
      ]);
    }

    // Pagination guard: stop if we got fewer than PER_PAGE (last page) or
    // if links.next is missing.
    const nextLink = (payload.links || {}).next;
    if (!nextLink || dataArr.length < PER_PAGE) break;
    page += 1;
    Utilities.sleep(400);  // be polite -- PrizePicks doesn't publish a rate limit
  }

  // Wipe everything below header, then write new rows.
  if (raw.getLastRow() > 1) {
    raw
      .getRange(2, 1, raw.getLastRow() - 1, RAW_HEADERS.length)
      .clearContent();
  }
  if (allRows.length) {
    raw.getRange(2, 1, allRows.length, RAW_HEADERS.length).setValues(allRows);
  }

  // Quick toast confirms it worked, no popup needed.
  ss.toast(
    `Scraped ${allRows.length} projections across ` +
    `${Object.keys(leagues).length} leagues at ${scrapedAt}`,
    'PrizePicks',
    5
  );
}
