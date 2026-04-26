# PrizePicks prop tracking on iPhone / iPad — Numbers + Shortcuts

A no-server, no-Python, no-Apps-Script alternative to the Google Sheets
scraper. Everything lives in Apple-native tools that sync over iCloud:

- **Numbers** holds the workbook (raw projections, your math, results
  tracking). Touch-optimized UI, free, syncs to every device on your
  Apple ID.
- **Shortcuts** is the iPhone/iPad automation app. We use it to fetch
  PrizePicks projections in one tap and drop a CSV onto the clipboard.
- **Manual paste** is the bridge between the two. Tap the Shortcut →
  paste into Numbers `_raw`. Formulas in `picks` and `tracking` update
  instantly.

The trade vs the Apps Script version: no scheduled background fetcher.
You tap the Shortcut when you want fresh data. Most users will run it
once or twice a day right before locking in picks; that's fine for the
manual-tracking workflow this is built for.

---

## One-time setup (~10 minutes)

### Step 1 — Create the Numbers workbook

1. Open **Numbers** on iPhone, iPad, or Mac. Tap **+** → **Create
   Spreadsheet** → **Blank**.
2. Rename the workbook (top of screen): `Edge Equation — Props`.
3. The default sheet has a table called `Table 1`. We'll replace it
   with three named sheets: `_raw`, `picks`, and `tracking`.

### Step 2 — Build the `_raw` sheet

This is where the scraper drops PrizePicks data. The Shortcut overwrites
it on every run, so don't add formulas here.

1. Rename `Sheet 1` → `_raw` (double-tap the sheet tab).
2. Rename `Table 1` → `_raw_table` (double-tap the table title above the
   grid).
3. Set the column count to **12** (Format panel → Headers & Body →
   Columns: 12). Set the row count to **2** (header row + one blank for
   the first paste).
4. Set the header row to these exact values, left to right:

   | Column | Header           |
   |-------:|------------------|
   | A      | scraped_at       |
   | B      | projection_id    |
   | C      | league           |
   | D      | sport            |
   | E      | player           |
   | F      | team             |
   | G      | position         |
   | H      | description      |
   | I      | stat_type        |
   | J      | line             |
   | K      | start_time       |
   | L      | odds_type        |

### Step 3 — Build the `picks` sheet

This is where you do the actual handicapping math. Tap **+** at the
bottom → **New Sheet**. Rename it to `picks`. Rename the auto-created
table to `picks_table`.

Set up **13 columns** with these headers:

| Col | Header              | Notes                                       |
|----:|---------------------|---------------------------------------------|
| A   | projection_id       | You paste this from `_raw`                  |
| B   | player              | Auto-fills (formula below)                  |
| C   | team                | Auto-fills                                  |
| D   | league              | Auto-fills                                  |
| E   | stat_type           | Auto-fills                                  |
| F   | line                | Auto-fills                                  |
| G   | pick (over/under)   | You type "over" or "under"                  |
| H   | your_prob           | You type your fair-prob estimate (0-1)      |
| I   | break_even          | Auto-computed                               |
| J   | edge                | Auto-computed                               |
| K   | half_kelly          | Auto-computed                               |
| L   | grade               | Auto-computed                               |
| M   | notes               | Free text                                   |

Add a **payout multiplier control cell**. PrizePicks' standard 2-pick
power-play pays 3x; 3-pick is 5x; 4-pick is 10x; 5-pick is 20x; 6-pick
is 25x. We'll reference this in the formulas.

- In a free cell anywhere outside the table (say row 20, column A),
  type the label: `payout_mult`
- In the cell next to it (row 20, column B), type the number: `3`
- Right-tap that cell → **Define Name**: name it `payout_mult` and
  scope it to "Whole Document". This lets the formulas reference
  `payout_mult` by name.

Now drop the formulas into row 2 of `picks_table` (the first data row).
Numbers uses table-prefixed references; the syntax is slightly different
from Sheets but the math is identical.

**B2 — player**:
```
=IFERROR(VLOOKUP(A2, _raw_table::B2:L1000, 4, FALSE), "")
```
**C2 — team**:
```
=IFERROR(VLOOKUP(A2, _raw_table::B2:L1000, 5, FALSE), "")
```
**D2 — league**:
```
=IFERROR(VLOOKUP(A2, _raw_table::B2:L1000, 2, FALSE), "")
```
**E2 — stat_type**:
```
=IFERROR(VLOOKUP(A2, _raw_table::B2:L1000, 8, FALSE), "")
```
**F2 — line**:
```
=IFERROR(VLOOKUP(A2, _raw_table::B2:L1000, 9, FALSE), "")
```
**I2 — break_even**:
```
=IF(payout_mult="", "", SQRT(1/payout_mult))
```
**J2 — edge**:
```
=IF(OR(H2="", I2=""), "", H2-I2)
```
**K2 — half_kelly**:
```
=IFERROR(IF(OR(H2="", J2<=0), 0, MIN(0.25, MAX(0, (H2*(payout_mult^(1/2)-1) - (1-H2))/(payout_mult^(1/2)-1)/2))), 0)
```
**L2 — grade**:
```
=IF(J2="", "", IF(J2>=0.10, "A+", IF(J2>=0.07, "A", IF(J2>=0.04, "B", IF(J2>=0.02, "C", IF(J2>0, "D", "F"))))))
```

After typing row 2, **drag the formulas down** to row ~50 so any future
projection_id you paste in column A auto-computes everything else.

**Format the columns**:
- Column F (line): one decimal place.
- Columns H, I, J, K (probabilities): percentage with 2 decimals.
- Column L (grade): center-aligned, bold.

### Step 4 — Build the `tracking` sheet

Same idea: tap **+** → **New Sheet** → rename to `tracking`. Rename the
table to `tracking_table`. Set up 10 columns:

| Col | Header          |
|----:|-----------------|
| A   | projection_id   |
| B   | player          |
| C   | stat_type       |
| D   | line            |
| E   | pick            |
| F   | actual          |
| G   | hit             |
| H   | units_risked    |
| I   | pl_units        |
| J   | note            |

Drop these into row 2:

**G2 — hit**:
```
=IF(F2="", "", IF(E2="over", IF(F2>D2, 1, IF(F2<D2, 0, "push")), IF(F2<D2, 1, IF(F2>D2, 0, "push"))))
```
**I2 — pl_units**:
```
=IF(G2="push", 0, IF(G2=1, H2*(payout_mult^(1/2)-1), IF(G2=0, -H2, "")))
```

Drag down to row 50.

That's the full workbook. Save / let iCloud sync. From now on you only
need to paste raw data and type your own fair-prob estimates.

---

## One-time setup — the Shortcut

Open **Shortcuts** on iPhone (also works on iPad and Mac, but iPhone is
where you'll most often tap it).

1. Tap **+** at top-right → **Add Action**.
2. Build the actions in the order below. Each is a separate block; tap
   **+** between blocks to insert the next.

**Action 1 — Text** (the URL):
- Search "Text", pick **Text** action.
- Paste exactly: `https://api.prizepicks.com/projections?per_page=250&page=1`

**Action 2 — Get Contents of URL**:
- Search "Get Contents of URL", pick that action.
- The URL field should auto-fill from Action 1's output. If not, tap
  the URL field and pick **Text** from the magic-variable list.
- Tap **Show More** → set **Method** to `GET`.
- Add a **Header**: name `User-Agent`, value `Mozilla/5.0 (iPhone)`.
  This is the dance that keeps PrizePicks' Cloudflare layer from
  blocking the call.
- Add another **Header**: name `Accept`, value `application/json`.

**Action 3 — Get Dictionary from Input**:
- Search and add. Parses the JSON response.

**Action 4 — Get Dictionary Value**:
- Get value for key `data` (the array of projections).

**Action 5 — Set Variable**:
- Set a variable called `csv_rows` to an empty Text. This is the
  accumulator. We'll append a row per projection inside the loop.
- The action: search "Set Variable", set name `csv_rows`, value blank.

**Action 6 — Repeat with Each**:
- Add **Repeat with Each** action.
- Set the input to the `Dictionary Value` from Action 4 (the `data`
  array). Inside the loop, you'll add the actions below.

   **Action 6a (inside the loop) — Get Dictionary Value**:
   - Get value for key `attributes` from the **Repeat Item**.

   **Action 6b — Get Dictionary Value**:
   - Get value for key `id` from the **Repeat Item**. This is the
     projection_id. Store as a magic variable named `proj_id`.

   **Action 6c — Get Dictionary Value**:
   - From `attributes` (Action 6a output), get key `description`.

   **Action 6d — Get Dictionary Value**:
   - From `attributes`, get key `stat_type`.

   **Action 6e — Get Dictionary Value**:
   - From `attributes`, get key `line_score`.

   **Action 6f — Get Dictionary Value**:
   - From `attributes`, get key `start_time`.

   **Action 6g — Get Dictionary Value**:
   - From `attributes`, get key `odds_type`. If empty, default to
     `standard`.

   **Action 6h — Text** (build the row):
   - Add a **Text** action containing tab-separated values, in this
     order, dragging the magic variables into place:
     ```
     <Current Date>	<proj_id>	<league name (skip — leave blank)>	<sport (skip — leave blank)>	<player (skip — leave blank)>	<team (skip — blank)>	<position (skip — blank)>	<description>	<stat_type>	<line_score>	<start_time>	<odds_type>
     ```
   - Tabs separate columns; this matches the 12-column `_raw` layout.
     Players, teams, leagues come back inside the JSON `included`
     array which is painful to traverse in Shortcuts; the
     `description` field (e.g. "Aaron Judge vs BAL") usually contains
     the player + opp inline, which is enough for picking. If you
     want true player breakouts, set up two extra Repeat blocks before
     this one to build a player_id → name dictionary from
     `payload.included`; the extra effort isn't worth it for v1.

   **Action 6i — Combine Text**:
   - Combine the existing `csv_rows` variable + the new row + a
     newline. Save back to `csv_rows`.

End of the Repeat block.

**Action 7 — Combine Text** (add the header):
- Combine: a header line, a newline, and `csv_rows`.
- Header line (tab-separated):
  ```
  scraped_at	projection_id	league	sport	player	team	position	description	stat_type	line	start_time	odds_type
  ```

**Action 8 — Copy to Clipboard**:
- Add **Copy to Clipboard** action; pass it the combined text from
  Action 7.

**Action 9 — Show Notification** (optional, nice to have):
- Title: `PrizePicks fetched`
- Body: `Tab-separated rows on the clipboard. Open Numbers → _raw → A2 → paste.`

Tap the Shortcut name at the top, rename it to **Fetch PrizePicks**,
and tap **Done**. You can then long-press it on the Shortcuts list and
**Add to Home Screen** for one-tap access.

---

## Daily workflow

1. Tap **Fetch PrizePicks** on Home Screen (or in Shortcuts).
2. Wait 5-15 seconds; you'll see the "PrizePicks fetched" notification.
3. Open **Numbers**, the `Edge Equation — Props` workbook.
4. Tap the `_raw` sheet → tap cell **A2** → tap **Paste**. The 250
   projections drop in across all 12 columns.
5. Switch to the `picks` sheet. Pick a projection_id from `_raw` you
   want to evaluate, and paste it into column A of any empty row.
   Player / team / league / stat / line auto-fill.
6. Type your `pick` (over/under) in column G, your `your_prob` estimate
   in column H. Edge / half-Kelly / grade compute themselves.
7. After the game, switch to `tracking`. Paste the same projection_id,
   fill in `actual` (the real stat the player put up) and
   `units_risked`. The sheet computes hit / push / loss and P&L in
   units.

---

## Limitations vs the Apps Script version

- **No scheduled refresh.** Shortcut runs only when tapped. (You can
  set up a Personal Automation in the Shortcuts app to run it at a
  specific time daily, but Apple still requires a confirmation tap.)
- **No player-name lookup.** The Shortcut leaves player / team /
  league columns blank because traversing the JSON-API `included`
  array twice in Shortcuts is slow. The `description` field carries
  enough info ("Aaron Judge vs BAL") for human picking.
- **One page only.** PrizePicks pages projections at 250 per request;
  the Shortcut grabs page 1 (~250 projections, usually all of MLB
  + headline NBA / NHL props). If you need the full board, duplicate
  Actions 1-6 with `&page=2`, `&page=3`.

If you want any of these limitations lifted, the cleanest path is a
small Python script running on your Mac (via launchd) that writes a
CSV to iCloud Drive — Numbers picks up the file automatically and you
get full server-style automation without leaving the Apple stack.

---

## Troubleshooting

- **Shortcut returns "401 Unauthorized" or empty data.** Tap the Action
  2 (Get Contents of URL), edit the User-Agent header to a current
  iPhone Safari UA string. PrizePicks occasionally tightens their
  Cloudflare config.
- **Paste into Numbers splits incorrectly.** Make sure the Shortcut
  uses **tabs** between columns, not commas — Numbers' paste-into-cell
  splits on tabs by default. Commas would land everything in one cell.
- **Formulas show `#REF!` after pasting.** The `_raw_table` reference
  may have moved. Re-tap the formula cell, drag the range selector to
  cover the new pasted range.
- **`half_kelly` always shows 0.** Confirm `payout_mult` is defined as
  a named cell (Format → Names → "Whole Document" scope). If only the
  cell label exists without the named reference, the formula can't
  read it.
