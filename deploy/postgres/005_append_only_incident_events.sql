CREATE OR REPLACE RULE incident_events_append_only_update AS
ON UPDATE TO incident_events DO INSTEAD NOTHING;

CREATE OR REPLACE RULE incident_events_append_only_delete AS
ON DELETE TO incident_events DO INSTEAD NOTHING;
