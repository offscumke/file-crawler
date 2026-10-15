import asyncio
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import downloader
import parser as parser_mod
import store

app = FastAPI()
RUNS_DIR = Path(__file__).parent / "runs"
JOBS: dict[str, dict] = {}  # job_id -> {status, files, run_dir, task}


class ParseRequest(BaseModel):
    url: str


class JobRequest(BaseModel):
    url: str
    files: list[dict]  # [{url, suggested_filename, file_type}]


async def fetch_page(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    if resp.status_code != 200:
        raise HTTPException(502, f"页面抓取失败: HTTP {resp.status_code}")
    return resp.text


def _client(request: Request) -> httpx.AsyncClient:
    return getattr(request.app.state, "http_client", None)


@app.post("/api/parse")
async def api_parse(req: ParseRequest, request: Request):
    own = None
    client = _client(request)
    if client is None:
        own = httpx.AsyncClient(timeout=30, follow_redirects=True)
        client = own
    try:
        html = await fetch_page(client, req.url)
    finally:
        if own is not None:
            await own.aclose()
    entries = parser_mod.parse_page(html, req.url)
    return {"files": [{"url": e.url, "suggested_filename": e.suggested_filename,
                       "file_type": e.file_type} for e in entries]}


@app.post("/api/jobs")
async def api_create_job(req: JobRequest, request: Request):
    if any(j["status"] == "running" for j in JOBS.values()):
        raise HTTPException(409, "已有任务在运行，请等待其完成")
    if not req.files:
        raise HTTPException(400, "没有勾选任何文件")
    job_id = uuid.uuid4().hex[:12]
    domain = httpx.URL(req.url).host.replace(":", "-")
    run_dir = store.create_run_dir(RUNS_DIR, f"{domain}-{job_id[:6]}")  # 带 job_id 短前缀：时间戳只到秒
    job = {"status": "running", "files": {}, "run_dir": str(run_dir), "task": None}
    for f in req.files:
        job["files"][f["url"]] = {"filename": f["suggested_filename"], "status": "queued",
                                  "bytes": 0, "error": ""}
    JOBS[job_id] = job

    def on_event(entry, status):
        job["files"][entry.url]["status"] = status  # 只更新状态；bytes/error 由 run_job 结束后按 results 回填

    async def run_job():
        try:
            entries = [parser_mod.FileEntry(f["url"], f["suggested_filename"], f["file_type"])
                       for f in req.files]
            client = _client(request)
            own = client is None
            if own:
                client = httpx.AsyncClient(timeout=30, follow_redirects=True)
            try:
                results = await downloader.download_files(entries, Path(run_dir), client, on_event)
            finally:
                if own:
                    await client.aclose()
            for r in results:
                job["files"][r.source_url].update(status=r.status, bytes=r.bytes, error=r.error)
            store.write_manifest(Path(run_dir), results)
            job["status"] = "done"
        except BaseException as exc:  # 含取消；僵尸任务会永久 409 锁死
            job["status"] = "failed"
            job["error"] = str(exc) or repr(exc)

    job["task"] = asyncio.create_task(run_job())
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):  # sync def → threadpool：与事件循环跨线程读 JOBS，靠 GIL + 外层 files 字典不再增删键保证安全
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能已重启）")
    files = list(job["files"].values())
    done = sum(1 for f in files if f["status"] in ("ok", "failed", "skipped"))
    summary = {"total": len(files), "done": done,
               "ok": sum(1 for f in files if f["status"] == "ok"),
               "failed": sum(1 for f in files if f["status"] == "failed"),
               "skipped": sum(1 for f in files if f["status"] == "skipped")}
    return {"job_id": job_id, "status": job["status"], "files": files,
            "summary": summary, "run_dir": job["run_dir"]}


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")
