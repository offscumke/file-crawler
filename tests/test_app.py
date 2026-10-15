import asyncio
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app
import store

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"
PAGE_URL = "https://example.com/page/index.html"


def post_job(client, files):
    r = client.post("/api/jobs", json={"url": PAGE_URL, "files": files})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def wait_terminal(client, job_id, tries=60):
    for _ in range(tries):
        s = client.get(f"/api/jobs/{job_id}").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.05)
    return s


CREATED_CLIENTS = []


def make_client(handler=None):
    def default_handler(request):
        if str(request.url) == PAGE_URL:
            return httpx.Response(200, text=FIXTURE.read_text())
        return httpx.Response(200, content=b"file-bytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default_handler))
    CREATED_CLIENTS.append(client)  # fixture 统一 aclose
    return client


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNS_DIR", tmp_path)
    app.JOBS.clear()
    app.app.state.http_client = make_client()  # app 通过 app.state.http_client 取注入的 client
    try:
        with TestClient(app.app) as c:
            yield c
    finally:
        app.app.state.http_client = None
        for injected in CREATED_CLIENTS:  # 关闭注入的 client（MockTransport，aclose 很便宜）
            asyncio.run(injected.aclose())
        CREATED_CLIENTS.clear()


def test_parse_returns_entries(client):
    r = client.post("/api/parse", json={"url": PAGE_URL})
    assert r.status_code == 200
    files = r.json()["files"]
    assert len(files) >= 8
    assert all(f["suggested_filename"] for f in files)


def test_parse_empty_result(client):
    def handler(request):
        return httpx.Response(200, text="<html><body><p>nothing</p></body></html>")
    app.app.state.http_client = make_client(handler)
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

    app.app.state.http_client = make_client(slow_handler)
    files = client.post("/api/parse", json={"url": PAGE_URL}).json()["files"]
    r1 = client.post("/api/jobs", json={"url": PAGE_URL, "files": files})
    r2 = client.post("/api/jobs", json={"url": PAGE_URL, "files": files})
    assert r1.status_code == 200
    assert r2.status_code == 409


def test_run_job_marks_failed_when_manifest_write_raises(client, monkeypatch):
    """write_manifest 抛异常时任务必须进入终态，否则僵尸 running 会永久 409。"""
    def boom(run_dir, results):
        raise RuntimeError("manifest write exploded")

    monkeypatch.setattr(store, "write_manifest", boom)
    files = [{"url": "https://example.com/page/a.pdf", "suggested_filename": "a.pdf",
              "file_type": "document"}]
    job1 = wait_terminal(client, post_job(client, files))
    assert job1["status"] == "failed"  # 不是卡死的 running
    job2 = wait_terminal(client, post_job(client, files))  # 后续 POST 不再被永久 409
    assert job2["status"] == "failed"


def test_two_jobs_in_same_second_get_distinct_run_dirs(client):
    """时间戳只到秒：同一秒内的两个任务必须落在不同目录，否则 manifest 互相覆盖。"""
    files = [{"url": "https://example.com/page/a.pdf", "suggested_filename": "a.pdf",
              "file_type": "document"}]
    id1 = post_job(client, files)
    s1 = wait_terminal(client, id1)
    id2 = post_job(client, files)
    s2 = wait_terminal(client, id2)
    assert s1["status"] == s2["status"] == "done"
    assert s1["run_dir"] != s2["run_dir"]
    assert id1[:6] in Path(s1["run_dir"]).name  # 目录名带 job_id 短前缀，秒级时间戳才安全
    assert id2[:6] in Path(s2["run_dir"]).name


def test_job_rejects_empty_files(client):
    r = client.post("/api/jobs", json={"url": PAGE_URL, "files": []})
    assert r.status_code == 400


def test_parse_non_200_returns_502(client):
    def handler(request):
        return httpx.Response(503, text="unavailable")

    app.app.state.http_client = make_client(handler)
    r = client.post("/api/parse", json={"url": PAGE_URL})
    assert r.status_code == 502


def test_mixed_outcome_job(client):
    """一个 ok、一个 404 失败（约 3s 退避）、一个 unknown 且 content-type 无法归类（跳过）。"""
    def handler(request):
        url = str(request.url)
        if url.endswith("/missing.pdf"):
            return httpx.Response(404, text="gone")
        if url.endswith("/mystery"):
            return httpx.Response(200, content=b"x", headers={"content-type": "application/octet-stream"})
        return httpx.Response(200, content=b"file-bytes")

    app.app.state.http_client = make_client(handler)
    files = [{"url": "https://example.com/page/ok.pdf", "suggested_filename": "ok.pdf", "file_type": "document"},
             {"url": "https://example.com/page/missing.pdf", "suggested_filename": "missing.pdf", "file_type": "document"},
             {"url": "https://example.com/page/mystery", "suggested_filename": "mystery", "file_type": "unknown"}]
    job_id = post_job(client, files)
    status = client.get(f"/api/jobs/{job_id}").json()
    for _ in range(120):
        if status["summary"]["done"] == status["summary"]["total"]:
            break
        time.sleep(0.05)
        status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "done"
    assert status["summary"] == {"total": 3, "done": 3, "ok": 1, "failed": 1, "skipped": 1}
    by_name = {f["filename"]: f for f in status["files"]}
    assert by_name["ok.pdf"]["status"] == "ok" and by_name["ok.pdf"]["bytes"] == len(b"file-bytes")
    assert by_name["missing.pdf"]["status"] == "failed" and by_name["missing.pdf"]["error"]
    assert by_name["mystery"]["status"] == "skipped" and by_name["mystery"]["error"]
