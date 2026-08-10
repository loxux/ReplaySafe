INSERT INTO analytics.events
SELECT *
FROM staged_events
WHERE event_date = CURRENT_DATE;

