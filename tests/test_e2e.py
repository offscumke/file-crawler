import csv
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


def test_full_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNS_DIR", tmp_path)
    app.JOBS.clear()
    with TestClient(app.app) as c:
        app.app.state.http_client = make_client()
        files = c.post("/api/parse", json={"url": PAGE_URL}).json()["files"]
        assert len(files) >= 8
        job = c.post("/api/jobs", json={"url": PAGE_URL, "files": files}).json()
        for _ in range(50):
            status = c.get(f"/api/jobs/{job['job_id']}").json()
            if status["summary"]["done"] == status["summary"]["total"]:
                break
            time.sleep(0.1)
        run_dir = Path(status["run_dir"])
        rows = list(csv.reader(open(run_dir / "manifest.csv", encoding="utf-8")))
        assert len(rows) == 1 + status["summary"]["total"]
        saved = [p for p in run_dir.iterdir() if p.name != "manifest.csv"]
        assert len(saved) == status["summary"]["ok"]
        assert all(p.stat().st_size == len(b"file-bytes") for p in saved)
