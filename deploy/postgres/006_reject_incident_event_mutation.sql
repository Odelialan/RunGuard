DROP RULE IF EXISTS incident_events_append_only_update ON incident_events;
DROP RULE IF EXISTS incident_events_append_only_delete ON incident_events;

CREATE OR REPLACE FUNCTION reject_incident_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'incident_events is append-only; % is forbidden', TG_OP
    USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS incident_events_append_only ON incident_events;
CREATE TRIGGER incident_events_append_only
BEFORE UPDATE OR DELETE ON incident_events
FOR EACH ROW EXECUTE FUNCTION reject_incident_event_mutation();
