BEGIN;

CREATE TABLE IF NOT EXISTS event_outbox (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  claimed_at TEXT,
  claimed_by TEXT,
  published_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS event_outbox_pending_idx
  ON event_outbox (created_at)
  WHERE published_at IS NULL;

CREATE INDEX IF NOT EXISTS event_outbox_incident_pending_idx
  ON event_outbox (incident_id, created_at, id)
  WHERE published_at IS NULL;

CREATE INDEX IF NOT EXISTS event_outbox_published_idx
  ON event_outbox (published_at)
  WHERE published_at IS NOT NULL;

COMMIT;
