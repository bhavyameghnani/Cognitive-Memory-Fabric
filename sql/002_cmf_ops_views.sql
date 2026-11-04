-- Convenience views over the operational layer.  They contain no business logic beyond filtering and summing, so a
-- future MCP tool can read from them (or from the base tables) and still own its own calculation.

-- Latest valuation of each active position in the investable portfolio (excludes deferred comp / private company interests).
CREATE VIEW cmf_ops.v_current_investable_holdings AS
SELECT h.*
FROM cmf_ops.portfolio_holdings h
JOIN cmf_ops.portfolio_accounts a ON a.account_id = h.account_id
WHERE h.holding_status = 'active' AND a.in_investable_portfolio;

-- Investable portfolio value per client and snapshot date (sum of the rounded per-holding market values).
CREATE VIEW cmf_ops.v_snapshot_investable_totals AS
SELECT s.client_id, s.snapshot_date, SUM(s.market_value) AS investable_value, COUNT(*) AS positions
FROM cmf_ops.portfolio_snapshots s
JOIN cmf_ops.portfolio_accounts a ON a.account_id = s.account_id
WHERE a.in_investable_portfolio
GROUP BY s.client_id, s.snapshot_date;

-- Investable value per client, date and asset class (basis for allocation and for allocation changes over time).
CREATE VIEW cmf_ops.v_snapshot_asset_class_totals AS
SELECT s.client_id, s.snapshot_date, h.asset_class, SUM(s.market_value) AS value
FROM cmf_ops.portfolio_snapshots s
JOIN cmf_ops.portfolio_holdings h ON h.holding_id = s.holding_id
JOIN cmf_ops.portfolio_accounts a ON a.account_id = s.account_id
WHERE a.in_investable_portfolio
GROUP BY s.client_id, s.snapshot_date, h.asset_class;
