# File Processing Service

An async file upload and processing API built with **FastAPI**, **PostgreSQL**, **S3/MinIO**, **Redis/ARQ** and **Pillow**.

Upload an image or PDF and you get a `202 Accepted` back right away. A background worker then:

1. virus-scans the file,
2. generates WebP thumbnails (for images) or extracts the text (for PDFs),
3. reports progress as it goes.

Files are downloaded through short-lived **signed S3 URLs**, so the bytes never pass through the API.

```
           ┌──────────────┐  put/presign  ┌─────────────────┐
 client ──►│  FastAPI api │──────────────►│  MinIO / S3     │◄─── client downloads via
           └──┬────────┬──┘               │  originals/     │     signed URL
              │        │ enqueue          │  thumbnails/    │
              │        ▼                  └────────▲────────┘
              │   ┌─────────┐   job   ┌────────────┴──┐   ┌────────┐
              │   │  Redis  │────────►│  ARQ worker   │──►│ ClamAV │ (optional)
              │   └─────────┘         │ scan → thumbs │   └────────┘
              ▼                       │ / pdf text    │
         ┌──────────┐◄────────────────┴───────────────┘
         │ Postgres │  owners, files (status, progress, metadata), thumbnails
         └──────────┘
```

## Features

- **Uploads with validation.** The file type is detected from its **magic bytes**, not from the filename or Content-Type.
  - Allowed types: JPEG, PNG, WebP, GIF and PDF.
  - The size limit is checked twice: from `Content-Length` before the body is read, and again on the actual bytes.
  - Each upload gets a SHA-256 checksum, and filenames are sanitized.
- **Thumbnails.** Each image gets 128 px and 512 px WebP thumbnails.
  - Thumbnails are rotated to match the photo's EXIF orientation.
  - Metadata such as GPS coordinates is removed.
  - Images are never scaled up.
  - Decompression-bomb images are rejected.
- **PDF text extraction.** Text is extracted page by page with pypdf.
- **Virus-scan hook.** Scanning sits behind a `Scanner` interface.
  - `noop` is the default. `clamav` streams the file to clamd using `INSTREAM`.
  - Infected files are deleted from storage and marked `quarantined`.
- **Progress tracking.** Each file moves through `pending → scanning → processing → ready | failed | quarantined`, with a progress value from 0 to 100.
  - You can poll `GET /files/{id}`, or follow a **Server-Sent Events** stream.
- **Signed download URLs.** These are only issued after a file passes scanning. The expiry is capped (5 minutes by default, 1 hour maximum).
- **Retries.** Transient failures (S3, database, clamd) back off and retry up to 3 times. Bad input (a corrupt image or PDF) fails immediately.
- **API-key auth per owner.** Only a SHA-256 hash of each key is stored. Every query is scoped to the calling owner, and other owners' files return 404.

## Run it

You need Docker Desktop.

```bash
docker compose up --build
```

This starts the following services:

| Service | Address |
|---|---|
| API and Swagger UI | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 (`minioadmin` / `minioadmin`) |
| Postgres | `localhost:5432` |
| Redis | `localhost:6379` |

Migrations run automatically when the API starts.

To turn on real virus scanning (the first start downloads signatures, which takes a minute or two):

```bash
SCANNER=clamav docker compose --profile scan up --build
```

## Try it

```bash
# 1. Create an owner (the API key is shown once)
curl -s -X POST localhost:8000/admin/owners \
  -H "X-Admin-Token: change-me" -H "Content-Type: application/json" \
  -d '{"name": "me"}'
export KEY=fps_...   # from the response

# 2. Upload
curl -s -H "X-API-Key: $KEY" -F file=@photo.jpg localhost:8000/files
# → 202 {"id": "…", "status": "pending", "progress": 0, …}

# 3. Watch progress live (Server-Sent Events)
curl -N -H "X-API-Key: $KEY" localhost:8000/files/<id>/events
# event: progress  data: {"status": "scanning", "progress": 10, …}
# event: progress  data: {"status": "ready", "progress": 100, …}

# 4. Get signed URLs
curl -s -H "X-API-Key: $KEY" "localhost:8000/files/<id>/thumbnails/512"
curl -s -H "X-API-Key: $KEY" "localhost:8000/files/<id>/download?expires=60"

# PDFs
curl -s -H "X-API-Key: $KEY" -F file=@report.pdf localhost:8000/files
curl -s -H "X-API-Key: $KEY" localhost:8000/files/<id>/text
```

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/admin/owners` | Create an owner (needs the `X-Admin-Token` header) |
| `POST` | `/files` | Upload a file (multipart field `file`); returns 202 |
| `GET` | `/files` | List your files: `?status=`, `?kind=`, `?limit=`, `?offset=` |
| `GET` | `/files/{id}` | Metadata, status, progress and thumbnails |
| `GET` | `/files/{id}/events` | SSE progress stream |
| `GET` | `/files/{id}/download` | Signed URL for the original |
| `GET` | `/files/{id}/thumbnails/{size}` | Signed URL for a thumbnail |
| `GET` | `/files/{id}/text` | Extracted PDF text |
| `DELETE` | `/files/{id}` | Delete the file, its thumbnails and its metadata |
| `GET` | `/health` | Checks Postgres, Redis and S3 |

## Development

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -e ".[dev]"

# Unit tests: no services needed
pytest

# Integration tests need Postgres + MinIO. Without them, they are skipped.
docker compose up -d postgres redis minio minio-init
pytest

# Run the API and worker on the host against the compose services
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
arq app.worker.WorkerSettings

ruff check . && ruff format .
```

## Project layout

```
app/
  main.py            app factory, lifespan, upload-size middleware
  config.py          settings (environment variables / .env)
  models.py          Owner, File, Thumbnail (SQLAlchemy 2.0, async)
  auth.py            API-key and admin-token dependencies
  validation.py      streaming size limit, magic-byte sniffing, filename sanitizing
  storage.py         S3 wrapper: put/get/delete/presign
  scanning.py        Scanner protocol, NoopScanner, ClamAVScanner
  processing/        pure functions: images.py (thumbnails), pdf.py (text)
  worker.py          ARQ task: scan → process → status/progress, with retries
  routers/           files, admin, health
alembic/             migrations
tests/               unit tests and integration tests (tests/test_api.py)
```

## Design notes

- **Why a job queue instead of `BackgroundTasks`?** Pillow work is CPU-bound and would slow down the API's event loop. A queue also survives restarts and scales by adding more worker processes.
- **Why two S3 clients?** Inside Docker, the API reaches MinIO at `minio:9000`, but a browser needs `localhost:9000`. A presigned URL is tied to the host it was signed for, so URLs are signed with `S3_PUBLIC_ENDPOINT`.
- **Why only download after scanning?** The original isn't handed out until it has passed the virus scan. This is what makes the scan hook more than decoration.
- **Why SHA-256 for API keys rather than bcrypt?** Each key carries 256 bits of randomness, so there is nothing to brute-force. A fast, deterministic hash also allows an indexed lookup.
