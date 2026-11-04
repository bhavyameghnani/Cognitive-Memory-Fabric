-- CMF operational financial data layer (synthetic).  PostgreSQL 13+.
-- Logically separate from the future CMF memory layer: everything here lives in schema cmf_ops.
-- (A schema named cmf_memory is reserved for the Memory Orchestrator phase and is NOT created here.)
--
-- Conventions
--   * Money: numeric(18,2).  Quantities and unit prices: numeric(18,4).  Rates: decimal fractions (0.031 = 3.1%).
--   * market_value = ROUND(quantity * price, 2) per row; portfolio totals are sums of those rounded rows.
--   * data_provenance: 'synthetic' | 'conversation_anchored' (see data_evidence_links for the per-field evidence).
--   * Only portable DDL is used (no PostgreSQL-only types) so the same file also loads into SQLite for smoke tests.

CREATE SCHEMA IF NOT EXISTS cmf_ops;

-- ------------------------------------------------------------------ reference data
CREATE TABLE cmf_ops.ref_asset_classes (
    asset_class  text PRIMARY KEY,
    description  text NOT NULL
);

CREATE TABLE cmf_ops.ref_liquidity_categories (
    liquidity_category text PRIMARY KEY,
    description        text NOT NULL,
    liquidity_rank     integer NOT NULL UNIQUE CHECK (liquidity_rank BETWEEN 1 AND 9)
);

CREATE TABLE cmf_ops.ref_instruments (
    instrument_id      text PRIMARY KEY,
    instrument_name    text NOT NULL,
    instrument_type    text NOT NULL,
    asset_class        text NOT NULL REFERENCES cmf_ops.ref_asset_classes (asset_class),
    sector             text NOT NULL,
    issuer             text NOT NULL,
    currency           text NOT NULL CHECK (currency = 'USD'),
    liquidity_category text NOT NULL REFERENCES cmf_ops.ref_liquidity_categories (liquidity_category),
    data_provenance    text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note    text NOT NULL
);

CREATE TABLE cmf_ops.ref_market_path (
    month_end     date PRIMARY KEY,
    equity_return numeric(8,5) NOT NULL,
    bond_return   numeric(8,5) NOT NULL,
    note          text NOT NULL
);

-- ------------------------------------------------------------------ clients
CREATE TABLE cmf_ops.client_financial_profiles (
    client_id                text PRIMARY KEY CHECK (client_id LIKE 'CLIENT-%'),
    age                      integer NOT NULL CHECK (age BETWEEN 18 AND 110),
    employment_status        text NOT NULL,
    occupation               text NOT NULL,
    annual_income            numeric(18,2) NOT NULL CHECK (annual_income >= 0),
    monthly_income           numeric(18,2) NOT NULL CHECK (monthly_income >= 0),
    monthly_expenses         numeric(18,2) NOT NULL CHECK (monthly_expenses > 0),
    dependants               integer NOT NULL CHECK (dependants >= 0),
    retirement_target_age    integer CHECK (retirement_target_age IS NULL OR retirement_target_age BETWEEN 40 AND 90),
    investment_horizon       text NOT NULL,
    investment_horizon_years integer NOT NULL CHECK (investment_horizon_years >= 0),
    risk_category            text NOT NULL CHECK (risk_category IN
                             ('Conservative', 'Moderately Conservative', 'Moderate', 'Moderately Aggressive', 'Aggressive')),
    liquidity_requirements   text NOT NULL,
    liquidity_target_months  integer NOT NULL CHECK (liquidity_target_months > 0),
    currency                 text NOT NULL CHECK (currency = 'USD'),
    profile_as_of_date       date NOT NULL,
    data_provenance          text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    synthetic_fields         text NOT NULL
);

CREATE TABLE cmf_ops.client_expense_history (
    expense_period_id text PRIMARY KEY,
    client_id         text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    effective_from    date NOT NULL,
    effective_to      date,
    monthly_expenses  numeric(18,2) NOT NULL CHECK (monthly_expenses > 0),
    currency          text NOT NULL CHECK (currency = 'USD'),
    data_provenance   text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note   text NOT NULL,
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

-- ------------------------------------------------------------------ accounts
CREATE TABLE cmf_ops.portfolio_accounts (
    account_id              text PRIMARY KEY,
    client_id               text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    account_type            text NOT NULL,
    account_name            text NOT NULL,
    currency                text NOT NULL CHECK (currency = 'USD'),
    account_status          text NOT NULL CHECK (account_status IN ('active', 'closed')),
    opened_date             date NOT NULL,
    closed_date             date,
    in_investable_portfolio boolean NOT NULL,
    access_restriction      text NOT NULL CHECK (access_restriction IN
                            ('none', 'retirement_age_restricted', 'employer_plan', 'education_use', 'private_company')),
    data_provenance         text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note         text NOT NULL,
    CHECK ((account_status = 'closed') = (closed_date IS NOT NULL)),
    CHECK (closed_date IS NULL OR closed_date >= opened_date)
);

-- ------------------------------------------------------------------ holdings
CREATE TABLE cmf_ops.portfolio_holdings (
    holding_id         text PRIMARY KEY,
    account_id         text NOT NULL REFERENCES cmf_ops.portfolio_accounts (account_id),
    client_id          text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    instrument_id      text NOT NULL REFERENCES cmf_ops.ref_instruments (instrument_id),
    instrument_name    text NOT NULL,
    instrument_type    text NOT NULL,
    asset_class        text NOT NULL REFERENCES cmf_ops.ref_asset_classes (asset_class),
    sector             text NOT NULL,
    issuer             text NOT NULL,
    quantity           numeric(18,4) NOT NULL CHECK (quantity >= 0),
    purchase_price     numeric(18,4) NOT NULL CHECK (purchase_price > 0),
    purchase_date      date NOT NULL,
    current_price      numeric(18,4) NOT NULL CHECK (current_price > 0),
    market_value       numeric(18,2) NOT NULL CHECK (market_value >= 0),
    currency           text NOT NULL CHECK (currency = 'USD'),
    valuation_date     date NOT NULL,
    liquidity_category text NOT NULL REFERENCES cmf_ops.ref_liquidity_categories (liquidity_category),
    holding_status     text NOT NULL CHECK (holding_status IN ('active', 'closed')),
    closed_date        date,
    data_provenance    text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note    text NOT NULL,
    CONSTRAINT uq_holding_account_instrument UNIQUE (account_id, instrument_id),
    CHECK ((holding_status = 'closed') = (quantity = 0)),
    CHECK ((holding_status = 'closed') = (closed_date IS NOT NULL))
);
CREATE INDEX ix_holdings_client      ON cmf_ops.portfolio_holdings (client_id);
CREATE INDEX ix_holdings_account     ON cmf_ops.portfolio_holdings (account_id);
CREATE INDEX ix_holdings_instrument  ON cmf_ops.portfolio_holdings (instrument_id);
CREATE INDEX ix_holdings_issuer      ON cmf_ops.portfolio_holdings (client_id, issuer);
CREATE INDEX ix_holdings_sector      ON cmf_ops.portfolio_holdings (client_id, sector);
CREATE INDEX ix_holdings_assetclass  ON cmf_ops.portfolio_holdings (client_id, asset_class);

-- ------------------------------------------------------------------ transactions (explains every change between snapshots)
CREATE TABLE cmf_ops.portfolio_transactions (
    transaction_id   text PRIMARY KEY,
    client_id        text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    account_id       text NOT NULL REFERENCES cmf_ops.portfolio_accounts (account_id),
    holding_id       text NOT NULL REFERENCES cmf_ops.portfolio_holdings (holding_id),
    instrument_id    text NOT NULL REFERENCES cmf_ops.ref_instruments (instrument_id),
    transaction_date date NOT NULL,
    transaction_type text NOT NULL CHECK (transaction_type IN
                     ('opening_balance', 'buy', 'sell', 'transfer_in', 'transfer_out', 'contribution', 'vest_receipt',
                      'tax_payment', 'expense_payment', 'qcd', 'withdrawal', 'secondary_sale_proceeds')),
    quantity         numeric(18,4) NOT NULL CHECK (quantity <> 0),   -- signed: + increases the holding, - decreases it
    price            numeric(18,4) NOT NULL CHECK (price > 0),
    amount           numeric(18,2) NOT NULL,                          -- signed, ROUND(quantity * price, 2)
    currency         text NOT NULL CHECK (currency = 'USD'),
    is_external_flow boolean NOT NULL,                                -- money/shares crossing the portfolio boundary
    group_id         text NOT NULL,
    description      text NOT NULL,
    data_provenance  text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note  text NOT NULL
);
CREATE INDEX ix_txn_client_date ON cmf_ops.portfolio_transactions (client_id, transaction_date);
CREATE INDEX ix_txn_holding     ON cmf_ops.portfolio_transactions (holding_id, transaction_date);
CREATE INDEX ix_txn_group       ON cmf_ops.portfolio_transactions (group_id);

-- ------------------------------------------------------------------ monthly snapshots (positions with quantity > 0 only)
CREATE TABLE cmf_ops.portfolio_snapshots (
    snapshot_id     text PRIMARY KEY,
    client_id       text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    snapshot_date   date NOT NULL,
    holding_id      text NOT NULL REFERENCES cmf_ops.portfolio_holdings (holding_id),
    instrument_id   text NOT NULL REFERENCES cmf_ops.ref_instruments (instrument_id),
    account_id      text NOT NULL REFERENCES cmf_ops.portfolio_accounts (account_id),
    quantity        numeric(18,4) NOT NULL CHECK (quantity > 0),
    market_price    numeric(18,4) NOT NULL CHECK (market_price > 0),
    market_value    numeric(18,2) NOT NULL CHECK (market_value >= 0),
    currency        text NOT NULL CHECK (currency = 'USD'),
    data_provenance text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    CONSTRAINT uq_snapshot_holding_date UNIQUE (holding_id, snapshot_date)
);
CREATE INDEX ix_snap_client_date ON cmf_ops.portfolio_snapshots (client_id, snapshot_date);
CREATE INDEX ix_snap_instrument  ON cmf_ops.portfolio_snapshots (client_id, instrument_id, snapshot_date);

-- ------------------------------------------------------------------ goals (versioned: one row per version of a goal)
CREATE TABLE cmf_ops.financial_goals (
    goal_id            text PRIMARY KEY,
    goal_key           text NOT NULL,          -- stable across versions of the same goal
    client_id          text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    goal_type          text NOT NULL CHECK (goal_type IN
                       ('Retirement', 'Education funding', 'Property purchase', 'Healthcare', 'Emergency fund',
                        'Wealth preservation', 'Business sale', 'Business investment', 'Debt reduction', 'Family support', 'Other')),
    goal_description   text NOT NULL,
    target_amount      numeric(18,2) CHECK (target_amount IS NULL OR target_amount > 0),
    target_date        date,
    priority           integer NOT NULL CHECK (priority BETWEEN 1 AND 3),   -- 1 = highest
    confidence_level   text NOT NULL CHECK (confidence_level IN
                       ('confirmed', 'exploratory', 'advisor_recommendation', 'unconfirmed_intention')),
    current_status     text NOT NULL CHECK (current_status IN
                       ('active', 'in_progress', 'achieved', 'conditional', 'postponed', 'on_hold', 'declined', 'superseded')),
    currency           text NOT NULL CHECK (currency = 'USD'),
    amount_provenance  text NOT NULL CHECK (amount_provenance IN ('conversation_stated', 'derived', 'synthetic', 'none')),
    data_provenance    text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note    text NOT NULL,
    valid_from         date NOT NULL,
    valid_to           date,                   -- NULL = still in effect
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX ix_goals_client     ON cmf_ops.financial_goals (client_id, valid_from);
CREATE INDEX ix_goals_key        ON cmf_ops.financial_goals (goal_key);
CREATE INDEX ix_goals_type       ON cmf_ops.financial_goals (client_id, goal_type);

-- ------------------------------------------------------------------ liabilities
CREATE TABLE cmf_ops.liabilities (
    liability_id       text PRIMARY KEY,
    client_id          text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    liability_type     text NOT NULL CHECK (liability_type IN ('Home loan', 'Personal loan', 'Education loan', 'Other financial obligation')),
    description        text NOT NULL,
    outstanding_amount numeric(18,2) CHECK (outstanding_amount IS NULL OR outstanding_amount >= 0),
    interest_rate      numeric(8,6) NOT NULL CHECK (interest_rate >= 0),
    monthly_payment    numeric(18,2) NOT NULL CHECK (monthly_payment >= 0),
    maturity_date      date,
    as_of_date         date NOT NULL,
    currency           text NOT NULL CHECK (currency = 'USD'),
    data_provenance    text NOT NULL CHECK (data_provenance IN ('synthetic', 'conversation_anchored')),
    provenance_note    text NOT NULL
);
CREATE INDEX ix_liabilities_client ON cmf_ops.liabilities (client_id);

-- ------------------------------------------------------------------ evidence links (only verified references to conversations.csv)
-- entity_id is polymorphic (profile / account / holding / transaction / goal / liability) so it carries no FK;
-- validate_operational.py checks every row against the entity tables and against conversations.csv.
CREATE TABLE cmf_ops.data_evidence_links (
    evidence_id      text PRIMARY KEY,
    client_id        text NOT NULL REFERENCES cmf_ops.client_financial_profiles (client_id),
    entity_type      text NOT NULL CHECK (entity_type IN ('profile', 'account', 'holding', 'transaction', 'goal', 'liability')),
    entity_id        text NOT NULL,
    field_name       text NOT NULL,
    source_file      text NOT NULL,
    interaction_id   text,                   -- NULL only when source_file is the design document
    turn_index       integer,
    interaction_date date,
    speaker          text CHECK (speaker IS NULL OR speaker IN ('Advisor', 'Client')),
    evidence_kind    text NOT NULL CHECK (evidence_kind IN ('explicit', 'derived', 'decision', 'implied', 'design_doc')),
    excerpt          text,
    note             text NOT NULL,
    CHECK ((evidence_kind = 'design_doc') = (interaction_id IS NULL))
);
CREATE INDEX ix_evidence_entity ON cmf_ops.data_evidence_links (entity_type, entity_id);
CREATE INDEX ix_evidence_client ON cmf_ops.data_evidence_links (client_id);
CREATE INDEX ix_evidence_turn   ON cmf_ops.data_evidence_links (interaction_id, turn_index);
