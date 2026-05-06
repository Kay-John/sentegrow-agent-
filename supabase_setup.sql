-- Run this in Supabase SQL Editor

-- Add risk_accepted column to bot_leads
ALTER TABLE bot_leads ADD COLUMN IF NOT EXISTS risk_accepted BOOLEAN DEFAULT FALSE;

-- Payment submissions table
CREATE TABLE IF NOT EXISTS payment_submissions (
    id           SERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL,
    name         TEXT,
    phone        TEXT,
    method       TEXT,
    amount       TEXT,
    reference    TEXT,
    message      TEXT,
    submitted_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE payment_submissions DISABLE ROW LEVEL SECURITY;

-- Insert SenteGrow client (30 days active)
INSERT INTO bot_clients (client_id, business_name, owner_phone, expires_at, price_usd)
VALUES ('sentegrow-001', 'SenteGrow', 'N/A', NOW() + INTERVAL '30 days', 200)
ON CONFLICT (client_id) DO NOTHING;
