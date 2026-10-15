import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

import downloader
from parser import FileEntry

CONTENT = b"hello-bytes"


async def nosleep(s):  # 替换 asyncio.sleep，消除退避等待（lambda 调被 patch 的 sleep 会自递归）
    return None


def make_client():
    calls = {"https://e.com/fail-twice.jpg": 0}

    def handler(request):
        url = str(request.url)
        if url == "https://e.com/a.jpg":
            return httpx.Response(200, content=CONTENT)
        if url == "https://e.com/fail-twice.jpg":
            calls[url] += 1
            if calls[url] < 3:
                return httpx.Response(500)
            return httpx.Response(200, content=CONTENT)
        if url == "https://e.com/always-500.jpg":
            return httpx.Response(500)
        if url == "https://e.com/noext":
            return httpx.Response(200, content=CONTENT, headers={"content-type": "image/png"})
        if url == "https://e.com/noext-unknown":
            return httpx.Response(200, content=CONTENT, headers={"content-type": "application/octet-stream"})
        if url in ("https://e.com/ok1.jpg", "https://e.com/nullbyte"):
            return httpx.Response(200, content=CONTENT)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run_download(entries, tmp_path):
    events = []
    asyncio.run(downloader.download_files(entries, tmp_path, make_client(),
                                          on_event=lambda e, s: events.append((e.url, s))))
    results = {p.name: p for p in tmp_path.iterdir() if p.name != "manifest.csv"}
    return results, events


def make_entries():
    return [
        FileEntry("https://e.com/a.jpg", "a.jpg", "image"),
        FileEntry("https://e.com/fail-twice.jpg", "fail-twice.jpg", "image"),
        FileEntry("https://e.com/always-500.jpg", "always-500.jpg", "image"),
        FileEntry("https://e.com/noext", "noext", "unknown"),
        FileEntry("https://e.com/noext-unknown", "noext-unknown", "unknown"),
    ]


def test_normal_download_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    results, events = run_download(make_entries(), tmp_path)
    assert (tmp_path / "a.jpg").read_bytes() == CONTENT


def test_retry_then_success(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    run_download(make_entries(), tmp_path)
    assert (tmp_path / "fail-twice.jpg").read_bytes() == CONTENT


def test_retry_exhausted_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    results, events = run_download(make_entries(), tmp_path)
    assert not (tmp_path / "always-500.jpg").exists()
    assert ("https://e.com/always-500.jpg", "failed") in events


def test_extensionless_resolved_by_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    run_download(make_entries(), tmp_path)
    assert (tmp_path / "noext.png").read_bytes() == CONTENT


def test_unclassifiable_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    _, events = run_download(make_entries(), tmp_path)
    assert ("https://e.com/noext-unknown", "skipped") in events
    assert not (tmp_path / "noext-unknown").exists()


# --- 缺陷 1：重试后扩展名累加（y.png.png） ---

class ReadErrorStream(httpx.AsyncByteStream):
    """流式响应：迭代中途抛 ReadError，模拟传输在 body 阶段断开。"""

    async def __aiter__(self):
        raise httpx.ReadError("connection reset")
        yield  # pragma: no cover


def make_midbody_fail_then_ok_client():
    calls = {"n": 0}

    def handler(request):
        url = str(request.url)
        if url == "https://e.com/y":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, headers={"content-type": "image/png"},
                                      stream=ReadErrorStream())
            return httpx.Response(200, content=CONTENT, headers={"content-type": "image/png"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_retry_after_midbody_failure_keeps_single_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    events = []
    asyncio.run(downloader.download_files(
        [FileEntry("https://e.com/y", "y", "unknown")], tmp_path,
        make_midbody_fail_then_ok_client(), on_event=lambda e, s: events.append((e.url, s))))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["y.png"]
    assert (tmp_path / "y.png").read_bytes() == CONTENT


# --- 缺陷 2：写盘异常（嵌入 null 的文件名）不得取消兄弟下载 ---

def test_write_error_does_not_cancel_siblings(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    entries = [
        FileEntry("https://e.com/ok1.jpg", "ok1.jpg", "image"),
        FileEntry("https://e.com/nullbyte", "\x00bad", "image"),
    ]
    events = []
    results = asyncio.run(downloader.download_files(
        entries, tmp_path, make_client(), on_event=lambda e, s: events.append((e.url, s))))
    by_url = {r.source_url: r for r in results}
    assert by_url["https://e.com/ok1.jpg"].status == "ok"
    assert (tmp_path / "ok1.jpg").read_bytes() == CONTENT
    assert by_url["https://e.com/nullbyte"].status == "failed"
