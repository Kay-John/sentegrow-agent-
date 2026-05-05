-- Run this in Supabase SQL Editor (only new additions needed if bot_leads already exists)

-- Add risk_accepted column if not already present
ALTER TABLE bot_leads ADD COLUMN IF NOT EXISTS risk_accepted BOOLEAN DEFAULT FALSE;

-- Insert SenteGrow as a client
INSERT INTO bot_clients (client_id, business_name, owner_phone, expires_at, price_usd)
VALUES (
    'sentegrow-001',
    'SenteGrow',
    'N/A',
    NOW() + INTERVAL '30 days',
    200
)
ON CONFLICT (client_id) DO NOTHING;
