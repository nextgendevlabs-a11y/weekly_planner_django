# Weekly Planner Django

Weekly team feedback and retrospective tool.

## Development

Requires Python 3.12 or newer.

Install dependencies:

```powershell
uv sync
```

Set up the local database:

```powershell
uv run python manage.py migrate
```

Run the local web server:

```powershell
uv run python manage.py runserver
```

Run tests:

```powershell
uv run pytest
```

## Database

Local development works without environment variables. When `DATABASE_URL` is
unset, Django uses the default SQLite database at `db.sqlite3`.

`DATABASE_URL` may optionally point at SQLite or PostgreSQL. Supported schemes
are `sqlite`, `postgres`, and `postgresql`.

## Environment

No environment variables are required for local setup. Optional local
configuration read by `weekly_planner/settings.py`:

- `DATABASE_URL` - optional SQLite or PostgreSQL database URL.
- `DJANGO_SECRET_KEY` - defaults to a development-only secret key.
- `DJANGO_DEBUG` - defaults to `true`; accepts `1`, `true`, `yes`, `on`, `0`,
  `false`, `no`, or `off`.
- `DJANGO_ALLOWED_HOSTS` - comma-separated host list; defaults to `localhost`
  and `127.0.0.1`.
- `DJANGO_TIME_ZONE` - defaults to `UTC`.
- `DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE` - positive integer; defaults to
  `2621440`.
- `DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE` - positive integer; defaults to
  `2621440`.
- `PROJECTS_TRANSCRIPTION_SERVICE` - optional meeting-material transcription
  service setting.
- `PROJECTS_EXTRACTION_SERVICE` - optional meeting-material extraction service
  setting.

## Background Processing

Redis and Celery are not currently required because they are not present in
`pyproject.toml`.

Process queued meeting materials locally:

```powershell
uv run python manage.py process_meeting_materials
```
