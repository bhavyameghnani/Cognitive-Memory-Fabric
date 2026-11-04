# Operational Data Dictionary (schema `cmf_ops`)

Concise column reference. Method, provenance model and limitations are in `docs/synthetic_operational_data.md`.
Conventions: currency is always USD; money `numeric(18,2)`; quantities and unit prices `numeric(18,4)`; rates are decimal fractions (0.031 = 3.1%);
dates ISO `YYYY-MM-DD`; `market_value = ROUND(quantity x price, 2)`. Every business table has `data_provenance` in {`synthetic`, `conversation_anchored`}.
CSV file = table name under `data/operational/`.

## client_financial_profiles (PK client_id) - 6 rows, as of 2026-06-30
`client_id` (= conversations.csv) · `age` · `employment_status` · `occupation` · `annual_income` · `monthly_income` · `monthly_expenses` (latest; history in `client_expense_history`) ·
`dependants` (count) · `retirement_target_age` (NULL when none discussed) · `investment_horizon` (text) · `investment_horizon_years` · `risk_category` (Conservative / Moderately Conservative / Moderate / Moderately Aggressive / Aggressive; synthetic label from the stated equity target) ·
`liquidity_requirements` (text) · `liquidity_target_months` · `currency` · `profile_as_of_date` · `data_provenance` · `synthetic_fields` (which fields are invented).

## client_expense_history (PK expense_period_id)
`client_id` (FK) · `effective_from` · `effective_to` (NULL = current) · `monthly_expenses` · `currency` · `data_provenance` · `provenance_note`. Use for liquidity ratios at past dates.

## portfolio_accounts (PK account_id)
`client_id` (FK) · `account_type` · `account_name` · `currency` · `account_status` (active/closed) · `opened_date` · `closed_date` ·
`in_investable_portfolio` (false for deferred comp and private company interests: exclude from allocation/concentration) ·
`access_restriction` (none / retirement_age_restricted / employer_plan / education_use / private_company) · `data_provenance` · `provenance_note`.

## portfolio_holdings (PK holding_id; UNIQUE account_id + instrument_id) - current position per account and instrument
`account_id`, `client_id`, `instrument_id` (FKs) · denormalised `instrument_name`, `instrument_type`, `asset_class`, `sector`, `issuer` · `quantity` (0 when closed) ·
`purchase_price` (average cost per unit across lots) · `purchase_date` (first acquisition) · `current_price` · `market_value` · `currency` · `valuation_date` (2026-06-30) ·
`liquidity_category` · `holding_status` (active/closed) · `closed_date` · `data_provenance` · `provenance_note`.

## portfolio_transactions (PK transaction_id) - the ledger behind every snapshot change
`client_id`, `account_id`, `holding_id`, `instrument_id` (FKs) · `transaction_date` · `transaction_type` (opening_balance, buy, sell, transfer_in, transfer_out, contribution, vest_receipt,
tax_payment, expense_payment, qcd, withdrawal, secondary_sale_proceeds) · `quantity` (signed) · `price` · `amount` (signed `ROUND(quantity x price, 2)`) · `currency` ·
`is_external_flow` (value crossing the portfolio boundary) · `group_id` (legs of one decision, e.g. a sell and its buy) · `description` · `data_provenance` · `provenance_note`.

## portfolio_snapshots (PK snapshot_id; UNIQUE holding_id + snapshot_date) - monthly, Jan-Jun 2026
`client_id`, `holding_id`, `instrument_id`, `account_id` (FKs) · `snapshot_date` (month-end) · `quantity` (> 0; closed positions have no row) · `market_price` · `market_value` · `currency` · `data_provenance` (always `synthetic`).

## financial_goals (PK goal_id) - one row per version of a goal
`goal_key` (stable across versions) · `client_id` (FK) · `goal_type` (Retirement, Education funding, Property purchase, Healthcare, Emergency fund, Wealth preservation, Business sale, Business investment, Debt reduction, Family support, Other) ·
`goal_description` · `target_amount` (NULL when unquantified) · `target_date` · `priority` (1 high - 3 low) ·
`confidence_level` (confirmed / exploratory / advisor_recommendation / unconfirmed_intention) ·
`current_status` (active, in_progress, achieved, conditional, postponed, on_hold, declined, superseded) · `currency` ·
`amount_provenance` (conversation_stated / derived / synthetic / none) · `data_provenance` · `provenance_note` · `valid_from` · `valid_to` (NULL = in effect).
Goals at date D: `valid_from <= D AND (valid_to IS NULL OR valid_to > D)`.

## liabilities (PK liability_id)
`client_id` (FK) · `liability_type` (Home loan, Personal loan, Education loan, Other financial obligation) · `description` · `outstanding_amount` (NULL for recurring obligations without a balance) ·
`interest_rate` · `monthly_payment` · `maturity_date` · `as_of_date` (2026-06-30; balances stated earlier are amortised forward) · `currency` · `data_provenance` · `provenance_note`.

## data_evidence_links (PK evidence_id) - the only place that points into conversations.csv
`client_id` (FK) · `entity_type` (profile/account/holding/transaction/goal/liability) · `entity_id` (polymorphic, validated by script) · `field_name` · `source_file` ·
`interaction_id` · `turn_index` · `interaction_date` · `speaker` · `evidence_kind` (explicit / derived / decision / implied / design_doc) · `excerpt` (exact text found in that turn) · `note`.
`design_doc` rows have no interaction/turn: the source is `docs/dataset_design.md`.

## Reference tables
* `ref_instruments` (PK instrument_id): `instrument_name`, `instrument_type`, `asset_class` (FK), `sector`, `issuer`, `currency`, `liquidity_category` (FK), `data_provenance`, `provenance_note`. All fictional.
* `ref_asset_classes`: Equity, Fixed Income, Cash & Equivalents, Multi-Asset, Commodities, Alternatives, Other Illiquid.
* `ref_liquidity_categories`: `cash_equivalent` (1), `liquid_daily` (2), `semi_liquid` (3), `illiquid` (4) with `liquidity_rank`.
* `ref_market_path`: monthly `equity_return` and `bond_return` that drive every instrument price.

## Views
* `v_current_investable_holdings`: active holdings in investable accounts.
* `v_snapshot_investable_totals`: investable value and position count per client and snapshot date.
* `v_snapshot_asset_class_totals`: investable value per client, date and asset class.

## Indexes (besides primary keys)
holdings by client / account / instrument / (client, issuer) / (client, sector) / (client, asset_class); transactions by (client, date) / (holding, date) / group;
snapshots by (client, date) / (client, instrument, date); goals by (client, valid_from) / goal_key / (client, goal_type); liabilities by client; evidence by (entity) / client / (interaction, turn).
