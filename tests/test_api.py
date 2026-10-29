"""End-to-end tests against real Postgres + MinIO (start them with `docker compose up -d
postgres redis minio minio-init`). Skipped automatically when the services aren't reachable.

The queue is faked; jobs are run by calling the worker's process_file() directly.
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.config import get_settings
from app.db import SessionLocal, engine
from app.deps import get_queue, get_storage
from app.main import create_app
from app.models import Base
from app.scanning import NoopScanner, ScanResult
from app.storage import Storage
from app.worker import process_file
from tests.helpers import EICAR, make_image, make_pdf

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append((name, args))

    async def ping(self):
        return True


class AlwaysInfected:
    async def scan(self, data: bytes) -> ScanResult:
        return ScanResult(clean=False, signature="Eicar-Test-Signature")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def storage():
    settings = get_settings()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        storage = Storage(settings)
        await storage.ensure_bucket()
    except Exception as exc:
        pytest.skip(f"Postgres/MinIO not reachable: {exc!r}")
    yield storage
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def env(storage):
    queue = FakeQueue()
    app = create_app()
    app.state.arq = queue
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, queue=queue, storage=storage, app=app)


async def new_owner(client: httpx.AsyncClient) -> dict[str, str]:
    resp = await client.post(
        "/admin/owners",
        json={"name": f"test-{uuid.uuid4().hex[:8]}"},
        headers={"X-Admin-Token": get_settings().admin_token},
    )
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["api_key"]}


async def run_job(storage: Storage, file_id: str, scanner=None) -> str:
    ctx = {
        "settings": get_settings(),
        "storage": storage,
        "scanner": scanner or NoopScanner(),
        "sessionmaker": SessionLocal,
        "job_try": 1,
    }
    return await process_file(ctx, file_id)


async def test_image_upload_process_and_download(env):
    client, queue, storage = env.client, env.queue, env.storage
    auth = await new_owner(client)

    resp = await client.post(
        "/files", headers=auth, files={"file": ("photo.jpg", make_image(1024, 768), "image/jpeg")}
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    file_id = body["id"]
    assert body["status"] == "pending"
    assert resp.headers["location"] == f"/files/{file_id}"
    assert queue.jobs == [("process_file", (file_id,))]

    # Not downloadable until it has been scanned.
    assert (await client.get(f"/files/{file_id}/download", headers=auth)).status_code == 409

    assert await run_job(storage, file_id) == "ready"

    body = (await client.get(f"/files/{file_id}", headers=auth)).json()
    assert body["status"] == "ready"
    assert body["progress"] == 100
    assert (body["width"], body["height"]) == (1024, 768)
    assert [t["size"] for t in body["thumbnails"]] == [128, 512]

    thumb = (await client.get(f"/files/{file_id}/thumbnails/128", headers=auth)).json()
    original = (await client.get(f"/files/{file_id}/download?expires=60", headers=auth)).json()
    assert original["expires_in"] == 60
    async with httpx.AsyncClient() as http:
        thumb_resp = await http.get(thumb["url"])
        assert thumb_resp.status_code == 200
        assert thumb_resp.headers["content-type"] == "image/webp"
        assert (await http.get(original["url"])).status_code == 200

    assert (await client.get(f"/files/{file_id}/thumbnails/999", headers=auth)).status_code == 404


async def test_pdf_text_extraction(env):
    client, storage = env.client, env.storage
    auth = await new_owner(client)

    resp = await client.post(
        "/files", headers=auth, files={"file": ("doc.pdf", make_pdf(["Quarterly report"]))}
    )
    file_id = resp.json()["id"]
    assert resp.json()["kind"] == "pdf"
    assert await run_job(storage, file_id) == "ready"

    text = (await client.get(f"/files/{file_id}/text", headers=auth)).json()
    assert text["page_count"] == 1
    assert "Quarterly report" in text["text"]


async def test_infected_file_is_quarantined(env):
    client, storage = env.client, env.storage
    auth = await new_owner(client)

    # EICAR inside a PDF wrapper so it passes the type check and reaches the scanner.
    payload = make_pdf(["hi"]) + EICAR
    file_id = (
        await client.post("/files", headers=auth, files={"file": ("x.pdf", payload)})
    ).json()["id"]
    assert await run_job(storage, file_id, scanner=AlwaysInfected()) == "quarantined"

    body = (await client.get(f"/files/{file_id}", headers=auth)).json()
    assert body["status"] == "quarantined"
    assert "Eicar" in body["error"]
    assert (await client.get(f"/files/{file_id}/download", headers=auth)).status_code == 410


async def test_corrupt_image_fails_without_retry(env):
    client, storage = env.client, env.storage
    auth = await new_owner(client)
    data = make_image()
    file_id = (
        await client.post("/files", headers=auth, files={"file": ("bad.jpg", data[:400])})
    ).json()["id"]

    assert await run_job(storage, file_id) == "failed"
    body = (await client.get(f"/files/{file_id}", headers=auth)).json()
    assert body["status"] == "failed"
    assert body["error"]


async def test_rejects_disguised_file(env):
    client = env.client
    auth = await new_owner(client)
    resp = await client.post(
        "/files", headers=auth, files={"file": ("cat.jpg", b"MZ\x90\x00" + b"\0" * 500)}
    )
    assert resp.status_code == 415


async def test_rejects_oversized_file(env):
    client = env.client
    auth = await new_owner(client)
    small = get_settings().model_copy(update={"max_upload_bytes": 1000})
    env.app.dependency_overrides[get_settings] = lambda: small

    resp = await client.post("/files", headers=auth, files={"file": ("big.jpg", make_image())})
    assert resp.status_code == 413


async def test_requires_api_key(env):
    client = env.client
    assert (await client.get("/files")).status_code == 401
    assert (await client.get("/files", headers={"X-API-Key": "nope"})).status_code == 401


async def test_owners_cannot_see_each_others_files(env):
    client = env.client
    alice, bob = await new_owner(client), await new_owner(client)

    file_id = (
        await client.post("/files", headers=alice, files={"file": ("a.png", make_image(fmt="PNG"))})
    ).json()["id"]

    assert (await client.get(f"/files/{file_id}", headers=bob)).status_code == 404
    assert (await client.delete(f"/files/{file_id}", headers=bob)).status_code == 404
    assert (await client.get("/files", headers=bob)).json()["total"] == 0
    assert (await client.get("/files", headers=alice)).json()["total"] == 1


async def test_delete_removes_file(env):
    client, storage = env.client, env.storage
    auth = await new_owner(client)
    file_id = (
        await client.post("/files", headers=auth, files={"file": ("a.jpg", make_image())})
    ).json()["id"]
    await run_job(storage, file_id)

    assert (await client.delete(f"/files/{file_id}", headers=auth)).status_code == 204
    assert (await client.get(f"/files/{file_id}", headers=auth)).status_code == 404


async def test_list_filters_by_kind(env):
    client = env.client
    auth = await new_owner(client)
    await client.post("/files", headers=auth, files={"file": ("a.jpg", make_image())})
    await client.post("/files", headers=auth, files={"file": ("b.pdf", make_pdf(["x"]))})

    listing = (await client.get("/files?kind=pdf", headers=auth)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["filename"] == "b.pdf"
