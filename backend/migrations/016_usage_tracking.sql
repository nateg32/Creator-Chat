CREATE TABLE IF NOT EXISTS user_usage_monthly (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  scrape_searches INTEGER NOT NULL DEFAULT 0,
  scrape_items_requested INTEGER NOT NULL DEFAULT 0,
  scrape_items_found INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, period_start)
);
