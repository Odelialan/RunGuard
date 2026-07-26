BEGIN;

CREATE TABLE IF NOT EXISTS ingress_receipts (
  idempotency_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  created_at TEXT NOT NULL
);

DROP INDEX IF EXISTS incidents_recovery_idx;
CREATE INDEX incidents_recovery_idx
  ON incidents (status, updated_at)
  WHERE status IN (
    'TRIAGING',
    'INVESTIGATING',
    'PLAN_READY',
    'POLICY_CHECKING',
    'WAITING_APPROVAL',
    'EXECUTING',
    'VERIFYING',
    'ROLLING_BACK'
  );

COMMIT;
