BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS evidence_embedding_hnsw
  ON evidence USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS postmortems (
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(id),
  run_id TEXT,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  impact TEXT NOT NULL,
  root_cause TEXT NOT NULL,
  contributing_factors JSONB NOT NULL DEFAULT '[]'::jsonb,
  timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
  remediation JSONB NOT NULL DEFAULT '[]'::jsonb,
  action_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  lessons JSONB NOT NULL DEFAULT '[]'::jsonb,
  generated_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS incident_events_incident_sequence_idx
  ON incident_events (incident_id, sequence);
CREATE INDEX IF NOT EXISTS evidence_incident_observed_idx
  ON evidence (incident_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS trace_spans_run_created_idx
  ON trace_spans (run_id, created_at);
CREATE INDEX IF NOT EXISTS tool_intents_incident_status_idx
  ON tool_intents (incident_id, status);

COMMIT;
