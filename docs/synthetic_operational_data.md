# Synthetic Wealth-Management Operational Data Layer

Phase 2 of the CMF proof of concept: the **structured financial data** that future MCP tools will query and calculate on.
This phase builds data only. There is no agent, MCP server or client, LLM call, embedding, RAG or memory logic anywhere in it.

Contents: [1 Why](#1-why-this-layer-exists) · [2 Relationship to conversations.csv](#2-how-it-complements-conversationscsv) · [3 Layout](#3-repository-layout) ·
[4 Datasets](#4-datasets) · [5 Generation](#5-how-the-data-is-generated) · [6 Provenance](#6-how-provenance-is-represented) · [7 Keys](#7-how-records-connect) ·
[8 Snapshots](#8-how-historical-snapshots-are-built) · [9 Client profiles](#9-what-each-client-looks-like) · [10 Supported calculations](#10-calculations-the-data-supports) ·
[11 Limitations](#11-limitations-and-assumptions) · [12 Source inconsistencies](#12-inconsistencies-found-in-conversationscsv) · [13 Commands](#13-commands)

## 1. Why this layer exists

`data/conversations.csv` records what advisors and clients *said*. A deterministic calculation tool (asset allocation, concentration,
liquidity ratio, goal-funding gap...) cannot be run on dialogue; it needs holdings, prices, balances, goals and liabilities as rows.
Target architecture, of which only item 3 is built here:

1. `conversations.csv`: conversational evidence (built, frozen).
2. CMF Memory Orchestrator: extracts and maintains memories from that evidence (future).
3. **Operational financial database: structured financial facts (this document).**
4. MCP tools read the database and run deterministic calculations (future).
5. Tool Selector Agent chooses the tools (future).
6. Report Agent combines memory evidence and tool results (future).

## 2. How it complements conversations.csv

* The CSV is **not modified, regenerated or reformatted**. Its MD5 (`03d14ce01d5f053bd843a1d9ec559f8f`, the frozen v2-final file) is pinned in
  `src/opdata/config.py`; the generator refuses to run and the validator fails if it changes.
* The six clients, their IDs and their stories come from the CSV. The operational data is built *around* them: portfolio values, equity
  shares, cash balances, trades and goal changes are calibrated to what advisors quoted and decided, at the dates they were quoted.
* Facts are kept apart from inventions. Anything taken or derived from the CSV carries a verified link to an `interaction_id` + `turn_index`;
  everything else is labelled synthetic (section 6). **No turn reference is hand-typed without being checked:** each evidence link is verified at
  generation time (the turn exists, belongs to that client, and its utterance contains the quoted excerpt) and again by the validator.
* The conversations never say, for example, which fund houses exist, how many units of a fund are held, or what a client's salary is.
  Those are synthetic and are never presented as disclosed.

## 3. Repository layout

| Path | What |
|---|---|
| `src/opdata/specs.py` | Hand-authored per-client specifications (accounts, holdings, events, goals, liabilities, evidence). The "controlled templates". |
| `src/opdata/market.py` | Fictional instrument catalogue + the one shared synthetic market path. |
| `src/opdata/build.py` | Derives every table from the specs (ledger -> quantities -> prices -> values). |
| `src/opdata/generate.py` | CLI: writes `data/operational/*.csv`. |
| `src/opdata/load_db.py` | Loads the CSVs into PostgreSQL schema `cmf_ops` (or a SQLite file for an offline smoke test). |
| `src/opdata/validate.py` | Validation + conversation-consistency audit -> `reports/operational_validation_report.{json,md}`. |
| `sql/000_drop_cmf_ops.sql`, `001_cmf_ops_schema.sql`, `002_cmf_ops_views.sql` | Drop (only `cmf_ops`), schema, three convenience views. |
| `data/operational/*.csv` | The generated data (13 files). |
| `docs/operational_data_dictionary.md` | Concise data dictionary. |
| `reports/operational_validation_report.md` | Latest validation report. |

Python 3.9+, standard library only for generation and validation. `psycopg` (v3) is needed only to load into PostgreSQL (already installed in this environment).

**Database note.** The task brief refers to an existing PostgreSQL foundation. This repository contains none: no SQL files, migrations, connection
settings, Docker files, or running server were found (the repo is the conversation generator only). So nothing was reused or duplicated; the layer adds a
self-contained schema `cmf_ops` that can be created in any PostgreSQL database and never touches other schemas (`000_drop_cmf_ops.sql` drops only `cmf_ops`).
A schema name `cmf_memory` is reserved for the future memory layer and is deliberately not created. If your PostgreSQL foundation lives elsewhere, point
`--dsn` at it; the DDL uses portable types only.

## 4. Datasets

Full column lists are in `docs/operational_data_dictionary.md`. Current counts:

| Dataset (table in `cmf_ops`) | Rows | Notes |
|---|---:|---|
| `client_financial_profiles` | 6 | one per client, as of 2026-06-30 |
| `client_expense_history` | 7 | dated monthly-expense periods (CLIENT-006's spending rose on 2 Jun) |
| `portfolio_accounts` | 27 | 4-5 per client; 4 are non-investable (deferred comp, private company interests) |
| `portfolio_holdings` | 73 | 69 active + 4 closed positions (sold crypto; inherited-IRA positions moved to the rollover IRA); 9-14 active per client |
| `portfolio_snapshots` | 396 | 6 month-ends x every holding with quantity > 0 |
| `portfolio_transactions` | 155 | opening balances + every dated buy/sell/transfer/flow that explains snapshot changes |
| `financial_goals` | 45 | goal *versions* (`valid_from`/`valid_to`), 31 distinct goals |
| `liabilities` | 5 | 2 mortgages, 1 student loan, 2 recurring obligations; only where the conversations support one |
| `data_evidence_links` | 233 | 227 verified links to conversation turns + 6 design-document references |
| `ref_instruments`, `ref_asset_classes`, `ref_liquidity_categories`, `ref_market_path` | 35 / 7 / 4 / 6 | reference data |

Liabilities: CLIENT-001 mortgage; CLIENT-003 student loan; CLIENT-004 mortgage; CLIENT-002 (mother's care fees) and CLIENT-006 (Frank's aide) as
*recurring obligations* (`Other financial obligation`, no balance). CLIENT-005 has none: nothing in the conversations indicates debt, so none was invented.

## 5. How the data is generated

* **Deterministic, no LLM.** Plain Python, one fixed seed (`20260101`, the same convention as the conversation generator). Two runs produce byte-identical
  files (the validator generates twice and compares MD5s, then compares against the committed files).
* **Entities first, tables second.** Nothing is drawn independently per table:
  1. `specs.py` defines each client's opening portfolio (weights or explicit amounts at 2026-01-31) and a list of dated events (rebalances, vests, sales, rollovers, contributions, withdrawals).
  2. `build.py` turns events into a **ledger** of signed transactions, tracking quantities and average cost.
  3. Quantities at each month-end come from replaying the ledger.
  4. Prices come from `market.py`. Market value = `ROUND(quantity x price, 2)` (half-up) for every holding/snapshot row; portfolio totals are sums of those rounded rows, so they reconcile exactly.
  5. Holdings, snapshots, asset-class totals and allocation are all derived from the same numbers.
* **Shared market path.** One path of monthly equity and bond returns for Jan-Jun 2026 (`ref_market_path`) drives every instrument (`beta x class return + seeded noise`), as the conversation generator tracked all clients against one shared fictional market. Single names (Vantora, Northgate, the crypto holding) have explicit paths taken from what the conversations say (Vantora "down almost thirty percent since January" by 19 Mar and "up eight percent from the low" by 9 Jun). The path was **calibrated** so that flow-adjusted portfolio totals track the totals the advisors quote (section 9, validation report).
* **Opening positions** are sized to match quoted totals, equity shares and cash balances at the start of the window (e.g. CLIENT-001: $3.43M, ~72% equity, $150k cash). Purchase dates and prices (cost basis) are synthetic; `purchase_price` is an average cost across lots, with no tax-lot detail.
* **Fictional instruments.** All fund houses, funds, banks and insurers are invented (e.g. "Aldgate Index Funds"). Only issuers the conversations name (Northgate Freight, Vantora Systems, Moreau Labs) reuse conversation names; the securities and prices are still synthetic.

## 6. How provenance is represented

Three layers, from coarse to fine:

1. **`data_provenance`** on every row of every business table: `synthetic` (generated for the POC; no conversational basis) or
   `conversation_anchored` (**at least one field** comes from a conversation turn; every other field of that row is synthetic). Anchored does *not* mean the whole row was disclosed.
2. **`data_evidence_links`**: one row per (entity, field) supported by evidence, with `source_file`, `interaction_id`, `turn_index`, `interaction_date`, `speaker`, the exact `excerpt`, and `evidence_kind`:

   | kind | meaning |
   |---|---|
   | `explicit` | the figure or fact is stated in that turn |
   | `derived` | computed from explicit statements (note says how) |
   | `decision` | the turn records the decision/instruction a record implements; execution date, split and instruments are synthetic unless the excerpt says otherwise |
   | `implied` | the turn only implies it (e.g. a holding is named, but its size is not) |
   | `design_doc` | not conversation evidence: taken from `docs/dataset_design.md` (e.g. ages), no interaction/turn |

3. **Free-text `provenance_note`** (and `synthetic_fields` on profiles) naming what is synthetic. Goals also carry `amount_provenance`
   (`conversation_stated` / `derived` / `synthetic` / `none`) because a goal's *existence* can be conversational while its *amount* is invented.

`portfolio_snapshots` are model output and are always `synthetic`; their consistency with the conversations is tested in the audit rather than claimed per row.
Rules enforced by the validator: anchored rows have at least one evidence link; purely synthetic rows have none; no link points at a turn that does not exist.

## 7. How records connect

```
client_financial_profiles (client_id)
   |-- portfolio_accounts (account_id, client_id)
   |      '-- portfolio_holdings (holding_id, account_id, client_id, instrument_id)  UNIQUE(account_id, instrument_id)
   |             |-- portfolio_transactions (holding_id, account_id, client_id, instrument_id)   signed quantity, dated
   |             '-- portfolio_snapshots    (holding_id, account_id, client_id, instrument_id, snapshot_date)
   |-- financial_goals (goal_key groups versions)      |-- liabilities      |-- client_expense_history
   '-- data_evidence_links (entity_type + entity_id -> any of the above, interaction_id + turn_index -> conversations.csv)
ref_instruments (instrument_id) <- holdings, transactions, snapshots
```

IDs are stable and readable: `ACC-001-02`, `HLD-001-06`, `TXN-001-0017`, `SNP-001-202604-009`, `GOL-003-02b`, `LIA-004-01`, `EVD-0123`, `INS-014`.
`client_id` is the CSV's own `CLIENT-00N`. A holding is a position in one instrument in one account, so the same fund in two accounts has two `holding_id`s and one `instrument_id`
(use `instrument_id` to aggregate across accounts; use `issuer`/`sector`/`asset_class` for concentration).

## 8. How historical snapshots are built

* Six snapshots per client at calendar month-ends 2026-01-31 ... 2026-06-30. `portfolio_snapshots` holds one row per position **with quantity > 0**; a position that is sold disappears the month after, and a new one appears only after its buy.
* The ledger starts with `opening_balance` transactions on 2026-01-31. Every later change is a dated transaction, so for every holding and date
  `snapshot.quantity == opening + sum(transactions up to that date)`; the validator replays the ledger for all 73 holdings x 6 dates.
* Events were placed **after** the decisions that cause them. Examples (all checked in the audit): CLIENT-001's Northgate vest on 27 Feb and the sale of about a third on 1 Apr (decided 31 Mar); the April move from 75% to 60% equity, executed in the retirement accounts; CLIENT-004's tuition bucket (decided 19 Feb, funded 20 Feb), rollover (decided 8 Apr, executed 1 May) and first $24,000 equity step on 1 Jun (the only one of these dates the conversation states); CLIENT-005's secondary sale (16 Apr), three Vantora trims (5 May, 19 May, 2 Jun) and crypto sale (11 May); CLIENT-006's March de-risking and April re-risking.
* Historical prices are never overwritten by current ones: each snapshot uses the month-end price from the shared path.
* Flows that cross the portfolio boundary (`is_external_flow = true`: contributions, vests, tax and expense payments, charitable distributions, withdrawals, secondary-sale proceeds) are flagged so a tool can separate market movement from money in/out.
* Goals and expense history are **versioned**: a version never starts before the statement that created it (validator rule), so a change of mind (CLIENT-001's retirement age 65 -> 63 on 1 May) does not rewrite earlier months.

## 9. What each client looks like

Investable value is the sum of holdings in investable accounts (it excludes CLIENT-001's $640k deferred compensation plan, CLIENT-002's company and CLIENT-005's startup stake).
All dollar figures are USD (the conversations use dollars throughout).

| Client | Jan-31 -> Jun-30 investable | Equity share | Anchored in the conversations | Synthetic |
|---|---|---|---|---|
| CLIENT-001 | $3.43M -> $3.54M | ~73% -> 75% (Feb) -> ~62% (from Apr) | $150k cash; ~$640k deferred comp; $90k bond->equity (12 Feb); Northgate 9% -> 6%; 75% -> 60% | all fund choices/units, salary, 401(k) deferrals, mortgage balance ($395k at assumed 3.25%) |
| CLIENT-002 | $2.11M -> $2.11M | ~60% | $220k cash; 60% equity; nothing sold while the sale is pending; $55k paid from cash; business value range $19-23M, offer $24M | fund choices, 529 balance, gold ETF, fees ($9.8k/month) |
| CLIENT-003 | $0.42M -> $0.44M | ~91% -> ~77% (from May) | $38k -> $50k cash; $40k house fund; student loan $48k at 5.2%; house purchase exploratory -> postponed | employer name/sector, units, salary, 401(k) amounts |
| CLIENT-004 | $1.46M -> $1.49M | ~15% -> 17% (after the 1 Jun step) | $80k tuition bucket; $24k global equity buy on 1 Jun; mortgage ~$140k at 3.1%; rollover; retire at 63 | fund choices, payout size, salary, mortgage payment ($1,100) |
| CLIENT-005 | $1.17M -> $1.20M | ~91% + 5% crypto -> ~93% | Vantora ~1/3 -> 60% of original; crypto ~5% -> sold; $31k cash -> ~$62k; $75k founder-share sale; no debt | startup stake value ($1.9M), unit counts, tax set-aside amounts |
| CLIENT-006 | $1.45M -> $1.45M | 35% -> 25% (Mar) -> ~31% (from May) | $210k cash; monthly draw ~$2,600 then ~$3,800; $6k QCD; aide $6,400/month | fund choices, ladder rungs, fine-grained draw dates |

Ages: only CLIENT-001's (58) is stated in the CSV. The other five come from `docs/dataset_design.md` and are marked `design_doc`.

**How well does the model track the conversations?** (full lists in the validation report): of the 83 portfolio totals quoted by advisors, mean deviation is 0.9%, median 0.6%, max 4.9%;
of 68 quoted equity shares, mean 1.1 points, max 3.0; of 16 quoted cash balances, mean 0.5%, max 3.9%. In addition 34 explicit event/fact checks pass (trade timing, amounts, goal timelines).
The deviation is not zero by design: the monthly market path cannot reproduce intra-month moves, and the conversations quote totals with and without flows.

## 10. Calculations the data supports

Nothing here is implemented as a tool. This shows that each future tool has the inputs it needs.

| Future tool | Reads | Notes |
|---|---|---|
| `get_client_portfolio` | `v_current_investable_holdings` (or `portfolio_holdings` where `holding_status='active'`) | filter `in_investable_portfolio` for the investable view; closed positions are kept for history |
| `calculate_asset_allocation` | holdings or snapshots grouped by `asset_class`; `v_snapshot_asset_class_totals` | share = class value / investable total; works for any snapshot date |
| `calculate_portfolio_concentration` | group by `instrument_id`, `issuer`, `sector`, `asset_class` | single-name equities have their own issuer (e.g. Northgate, Vantora); funds carry the fund house as issuer and "Diversified" as sector, so **no look-through into fund holdings** |
| `compare_portfolio_snapshots` | two `snapshot_date`s of `portfolio_snapshots`, optionally `portfolio_transactions` | added/removed = present in one date only; quantity change = quantity delta; value change; allocation change from asset-class totals; the ledger says *why* (buy, sell, vest, contribution...) |
| `calculate_portfolio_returns` | month-end values + dated external flows | See below |
| `calculate_liquidity_ratio` | `liquidity_category`, `asset_class='Cash & Equivalents'` + `client_financial_profiles.monthly_expenses` (and `client_expense_history` for past dates) | liquid assets / monthly expenses; `access_restriction` on accounts separates retirement-restricted money |
| `calculate_goal_funding_gap` | `financial_goals` (valid at the chosen date) vs investable value / earmarked accounts | **Simple gap** = target_amount - currently available resources. It is *not* a retirement projection: no growth, inflation, contributions or drawdown. A proper projection needs assumptions the data deliberately does not contain |

**Returns: what is and is not possible.** Available: month-end quantity, price and value for six dates, dated external flows, and cost basis per holding.
Supported, clearly defined: (a) *price return per holding* between two dates (`price_t / price_0 - 1`); (b) *simple portfolio change* (`V_t - V_0`); (c) *flow-adjusted change* `V_t - V_0 - net external flows`;
(d) *Modified Dietz* return over a period using flows weighted by day, where the flows are the exact dated rows in `portfolio_transactions`; (e) unrealised gain per holding vs `purchase_price`.
**Not supported, do not claim:** true time-weighted return (needs a valuation at every flow date, we only have month-ends, and prices between month-ends are interpolated, not observed); IRR/money-weighted return on pre-2026 history; dividend, coupon or interest income (not modelled: equity funds are price-only and bond funds/ladders accrue through their price); realised gains and taxes (cost basis is an average, no tax lots); fees; performance vs benchmark; anything before 2026-01-31.

## 11. Limitations and assumptions

1. **All values are synthetic** except where an evidence link says otherwise; they say nothing about real markets, funds or people.
2. **Price-only valuation**: no dividends, interest or fees. Cash and money-market balances do not earn interest (price fixed at 1.00).
3. **One market path for everyone**; instrument returns are a fixed beta of it plus small seeded noise. Real dispersion between funds is understated.
4. **Month-end granularity.** Trade dates inside a month are chosen by the generator unless the conversation states them (CLIENT-004's 1 Jun purchase is the clear example). Execution dates, amounts and instrument splits for decisions are synthetic.
5. **Quantity rounding**: quantities have 4 decimals, so trades of a given dollar amount differ from it by under a cent per unit price; internal transfers reconcile within $1. CLIENT-005's Vantora ends at 4,902.0001 of 8,170 shares (60% to within 0.001%).
6. **Interpretations made explicit:** (a) "sold a third of the vested Northgate shares" reduces the total Northgate position by a third, because only that reproduces the quoted drop from ~9% to ~6%; (b) "equities around X%" is compared with `Equity` (+ the crypto holding for CLIENT-005, whom the advisor counts as risk assets); (c) "cash and short-term holdings" is compared with `Cash & Equivalents`; (d) for CLIENT-005 the quoted ~$62k equals the cash account plus the tax set-aside bucket; surplus crypto proceeds are swept into the index fund under the advisor's 16 Apr rule ("fill the reserve to nine months, whatever's left goes in the index fund"), which is a synthetic implementation choice.
7. **Not in the investable portfolio:** CLIENT-002's company (carrying value = midpoint of the accountant's $19-23M draft range), CLIENT-005's founder shares (value synthetic) and CLIENT-001's deferred-comp plan. They exist as holdings in accounts flagged `in_investable_portfolio = false` so net-worth style questions are possible without distorting allocation or concentration.
8. **CLIENT-002's sale proceeds** ($24M headline, care reserve $600k) are *not* in the data as assets: the sale had not closed by 30 Jun. The care reserve is a conditional goal.
9. **Profile is a single as-of-30-Jun row.** Changes over time live in goals (versioned) and `client_expense_history`; risk category and retirement age history is not versioned.
10. **Risk categories are synthetic labels** mapped from the stated equity targets; they are not suitability assessments.
11. **Goal amounts that the conversations do not state are synthetic** (e.g. retirement capital need = desired income x 12 / 4%, an illustrative rule, not a projection) and marked `amount_provenance = synthetic` or `derived`.
12. **PostgreSQL was not available in the build environment**, so the SQL has been executed end-to-end only against SQLite (same DDL with the schema prefix stripped), which enforces all PRIMARY KEY, FOREIGN KEY, UNIQUE and CHECK constraints. The PostgreSQL loader (`COPY ... FROM STDIN`) and PG-specific behaviour are untested against a live server; the first run on your server should be `load_db.py --reset` followed by `validate.py --dsn`.

## 12. Inconsistencies found in conversations.csv

These are properties of the frozen source; none were "fixed". The operational data takes the interpretation stated in each item.

1. **INT-031.5 vs INT-031.9** (CLIENT-001, 12 Feb): the advisor says equity is "about 72 percent" then, four turns later in the same meeting, "about 75 percent, in line with what we agreed". Data: ~72.6% before the rebalance, ~75.6% after the $90k move.
2. **Cash plus equity exceed 100%** (CLIENT-003, CLIENT-005): CLIENT-003 is quoted at ~90% equity while $50k of $415k (12%) is cash; CLIENT-005 at ~95% equity with 5% crypto and cash. Data: CLIENT-003 ~88% equity in Feb-Apr (within the tolerance); CLIENT-005 reported as equity + crypto.
3. **CLIENT-005 reserve** (INT-051 to INT-115): the advisor says the reserve was "about eight months" on 16 Apr, the plan then adds $4,000/month, fills to nine months and also receives all crypto proceeds, yet cash is quoted at $62,000 (~10 months) on 29 May. Not all of these can be true at once; see section 11, item 6(d).
4. **Statements that run ahead of execution:** in decision meetings the advisor quotes the *new* equity level at the start (INT-060.7: "near 25 percent" before the decision at INT-060.13; INT-087.7: "30 percent" on 29 Apr; INT-089.9: "75 percent" on 1 May). The audit therefore accepts a match on the day or two weeks later; every trade is dated after the decision that causes it.
5. **Portfolio totals "before any contributions or withdrawals"** are quoted by a template that ignores real flows (CLIENT-001's vest and tax payment, CLIENT-005's $75k secondary sale). The audit accepts the nearer of the raw and the flow-adjusted comparison.
6. **CLIENT-006 spending**: the advisor quotes $8,900 per month until 2 Jun, but says the agency raised the aide to five days after Frank's fall "in March" and that May's invoice was $6,400. Data: expense history switches on 2 Jun (when it is stated); the monthly portfolio draw is modelled at $3,800 from April (synthetic).
7. **CLIENT-004** has no stated emergency-reserve amount or month target; $74,400 (12 months) is synthetic. Her earlier retirement age (65) is only implied by "rather than 62 or 65" on 1 Jun, so no 65 goal version is created.
8. **Ages** for CLIENT-002 to 006 are not stated; they come from the design document.
9. **Advisor notes later corrected** are intentionally not represented as facts: CLIENT-001 "wants to retire at 60" (corrected 4 Mar), CLIENT-003 "decided to buy a house" (corrected 10 Apr), CLIENT-004 "insurance payout reserved for the mortgage" (corrected 5 Jun), CLIENT-006 "will help Brian with equipment" (corrected 8 May). The data records the corrected state.

## 13. Commands

Run from the repository root (`C:\Users\Mohor\Documents\Cognitive Neuro Agent`). Bash and PowerShell accept the same forms.

```bash
# 1. Generate the CSVs (deterministic; refuses to run if conversations.csv changed)
python src/opdata/generate.py                      # writes data/operational/*.csv   (--out DIR for another location)

# 2. Offline smoke test of the schema (no server needed; builds a scratch SQLite file under build/)
python src/opdata/load_db.py --sqlite build/cmf_ops_smoke.db

# 3. Load into PostgreSQL (creates schema cmf_ops; --reset drops and recreates only that schema)
set CMF_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/DBNAME       # PowerShell: $env:CMF_DATABASE_URL = "..."
python src/opdata/load_db.py --reset               # or: --dsn postgresql://USER:PASSWORD@localhost:5432/DBNAME

# 4. Validate (CSV checks + conversation audit + determinism; add --sqlite FILE or --dsn DSN for SQL-side checks)
python src/opdata/validate.py
python src/opdata/validate.py --sqlite build/cmf_ops_smoke.db
python src/opdata/validate.py --dsn postgresql://USER:PASSWORD@localhost:5432/DBNAME
```

`validate.py` writes `reports/operational_validation_report.json` and `.md` and exits 1 if any hard check fails.
Latest result on this repository: **109 passed, 0 failed** (1 informational) of 110 checks.
