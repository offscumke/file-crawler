import asyncio
from dataclasses import dataclass

import httpx

CONCURRENCY = 8
TIMEOUT = 30.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [1, 2, 4]

CONTENT_TYPE_EXTS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/zip": ".zip",
    "video/mp4": ".mp4", "audio/mpeg": ".mp3",
}


@dataclass
class DownloadResult:
    filename: str
    source_url: str
    status: str  # ok | failed | skipped
    bytes: int
    error: str


async def download_files(entries, dest_dir, client=None, on_event=None):
    """并发下载。on_event(entry, status) 在状态变化时回调（downloading/ok/failed/skipped）。

    on_event 必须是同步回调（async 回调会被静默丢弃）；downloading 每次尝试都会触发一次。
    """
    entries = list(entries)  # 生成器只能迭代一次：先物化，gather 与 zip 共用同一份
    semaphore = asyncio.Semaphore(CONCURRENCY)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        results = await asyncio.gather(
            *[_download_one(entry, dest_dir, client, semaphore, on_event) for entry in entries],
            return_exceptions=True,
        )
        # 安全网：任何漏网异常都转成失败结果，绝不取消兄弟下载
        out = []
        for entry, res in zip(entries, results):
            if isinstance(res, BaseException):
                out.append(DownloadResult(entry.suggested_filename, entry.url, "failed", 0, str(res)))
            else:
                out.append(res)
        return out
    finally:
        if own_client:
            await client.aclose()


async def _download_one(entry, dest_dir, client, semaphore, on_event) -> DownloadResult:
    def emit(status):
        if on_event:
            on_event(entry, status)

    emit("queued")
    filename = entry.suggested_filename  # 循环前初始化：兜底 tail 不再潜在 NameError
    for attempt in range(MAX_ATTEMPTS):
        filename = entry.suggested_filename  # 每次尝试重置，重试不累加扩展名
        backoff = None
        async with semaphore:  # 信号量覆盖「网络 + 写盘」整次尝试
            emit("downloading")
            try:
                async with client.stream("GET", entry.url) as resp:
                    if resp.status_code not in (200, 201, 204):
                        raise RuntimeError(f"HTTP {resp.status_code}")
                    if entry.file_type == "unknown":
                        ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        ext = CONTENT_TYPE_EXTS.get(ct)
                        if ext is None:
                            emit("skipped")
                            return DownloadResult(filename, entry.url, "skipped", 0, "unclassifiable content-type")
                        filename = filename + ext
                    chunks = []
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                data = b"".join(chunks)
                # 先缓冲全部字节再一次性写盘：失败时不会留下半截文件
                (dest_dir / filename).write_bytes(data)
                emit("ok")
                return DownloadResult(filename, entry.url, "ok", len(data), "")
            except (httpx.HTTPError, RuntimeError, OSError, ValueError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    emit("failed")
                    return DownloadResult(filename, entry.url, "failed", 0, str(exc))
                backoff = BACKOFF_SECONDS[attempt]
        if backoff is not None:  # 退避睡眠在信号量之外，不占着槽位干等
            await asyncio.sleep(backoff)
    emit("failed")
    return DownloadResult(filename, entry.url, "failed", 0, "retries exhausted")
