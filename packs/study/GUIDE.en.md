# Using the study desk pack

Two tables, mostly filled in by the server:

- `deadlines`: assignments and exams from confirmed course profiles sync in by themselves (`link` holds `study:<course>/<deadline id>`; don't edit it), and so do synced course sites (Canvas and the like). Tick `done` when handed in. For a deadline the course profile doesn't have, add it to the profile with `study_ctl.py ddl add` rather than writing the table directly.
- `study_log`: each ticked step of a study path adds a line (date, course, step, minutes). When the user says something like "studied S3 for two hours today", add a line with `rows add study_log` as well.

Board: deadlines, study time this week, study minutes by week. The study desk itself (review, pick up where you left off, progress per course) is the top section, drawn by the app.

Change a course's structure (add a session, move a date, swap a reading, file a document, undo) only with `study_ctl.py` (see the study skill); never move folders yourself.
