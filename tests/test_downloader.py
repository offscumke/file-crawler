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
