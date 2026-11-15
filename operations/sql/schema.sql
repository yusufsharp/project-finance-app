-- Idempotent schema and migration from the original expense-only version.
CREATE TABLE IF NOT EXISTS operations (
    id UUID PRIMARY KEY,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    category TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('income', 'expense')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE operations ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'expense'
    CHECK (kind IN ('income', 'expense'));
CREATE TABLE IF NOT EXISTS outbox (
    id UUID PRIMARY KEY,
    payload TEXT NOT NULL
);
-- Events are independent creation snapshots. Deleting an operation must not
-- delete a pending event or fail while the broker is temporarily unavailable.
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_id_fkey;
