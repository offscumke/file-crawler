import asyncio
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"
PAGE_URL = "https://example.com/page/index.html"


def make_client():
    def handler(request):
        if str(request.url) == PAGE_URL:
            return httpx.Response(200, text=FIXTURE.read_text())
        return httpx.Response(200, content=b"file-bytes")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNS_DIR", tmp_path)
    app.JOBS.clear()
    app.app.state.http_client = make_client()  # app 通过 app.state.http_client 取注入的 client
    with TestClient(app.app) as c:
        yield c
    app.app.state.http_client = None


def test_parse_returns_entries(client):
    r = client.post("/api/parse", json={"url": PAGE_URL})
    assert r.status_code == 200
    files = r.json()["files"]
    assert len(files) >= 8
    assert all(f["suggested_filename"] for f in files)


def test_parse_empty_result(client):
    def handler(request):
        return httpx.Response(200, text="<html><body><p>nothing</p></body></html>")
    app.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = client.post("/api/parse", json={"url": "https://example.com/x/"})
    assert r.json()["files"] == []


def test_job_full_lifecycle(client, tmp_path):
    files = client.post("/api/parse", json={"url": PAGE_URL}).json()["files"]
    r = client.post("/api/jobs", json={"url": PAGE_URL, "files": files[:2]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(50):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["summary"]["total"] == status["summary"]["done"]:
            break
        time.sleep(0.1)
    assert status["summary"]["done"] == 2
    assert status["summary"]["ok"] == 2
    run_dir = Path(status["run_dir"])
    assert run_dir.parent == tmp_path
    assert (run_dir / "manifest.csv").exists()


def test_job_not_found(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_single_running_task(client):
    async def slow_handler(request):
        if str(request.url) == PAGE_URL:
            return httpx.Response(200, text=FIXTURE.read_text())
        await asyncio.sleep(0.3)  # 拖住第一个任务，保证第二个 POST 到达时仍在运行
        return httpx.Response(200, content=b"x")

    app.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    files = client.post("/api/parse", json={"url": PAGE_URL}).json()["files"]
    r1 = client.post("/api/jobs", json={"url": PAGE_URL, "files": files})
    r2 = client.post("/api/jobs", json={"url": PAGE_URL, "files": files})
    assert r1.status_code == 200
    assert r2.status_code == 409
