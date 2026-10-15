import asyncio

import httpx

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
    # I1：写盘失败也必须走正常重试路径（3 次尝试）并触发终止事件
    nb_events = [s for (u, s) in events if u == "https://e.com/nullbyte"]
    assert nb_events[-1] == "failed"
    assert nb_events.count("downloading") == 3


# --- C1：生成器输入不得静默丢失结果 ---

def gen_entries():
    for e in make_entries():
        yield e


def test_generator_input_returns_results(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    events = []
    results = asyncio.run(downloader.download_files(
        gen_entries(), tmp_path, make_client(), on_event=lambda e, s: events.append((e.url, s))))
    by_url = {r.source_url: r for r in results}
    assert len(results) == 5
    assert by_url["https://e.com/a.jpg"].status == "ok"
    assert by_url["https://e.com/fail-twice.jpg"].status == "ok"
    assert by_url["https://e.com/always-500.jpg"].status == "failed"
    assert by_url["https://e.com/noext"].filename == "noext.png"
    assert by_url["https://e.com/noext"].status == "ok"
    assert by_url["https://e.com/noext-unknown"].status == "skipped"
    assert (tmp_path / "a.jpg").read_bytes() == CONTENT


# --- I6：headline features ---

def test_concurrency_cap(tmp_path):
    state = {"inflight": 0, "max": 0}

    async def handler(request):
        state["inflight"] += 1
        state["max"] = max(state["max"], state["inflight"])
        await asyncio.sleep(0.01)  # 人为延迟，制造可观测的并发窗口
        state["inflight"] -= 1
        return httpx.Response(200, content=CONTENT)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entries = [FileEntry(f"https://e.com/c{i}.jpg", f"c{i}.jpg", "image") for i in range(20)]
    results = asyncio.run(downloader.download_files(entries, tmp_path, client))
    assert state["max"] == 8  # == downloader.CONCURRENCY
    assert state["max"] > 1   # 测试本身有效：确实观测到了并发
    assert len(results) == 20
    assert all(r.status == "ok" for r in results)


def test_backoff_schedule(tmp_path, monkeypatch):
    sleeps = []

    async def rec(s):
        sleeps.append(s)

    monkeypatch.setattr(downloader.asyncio, "sleep", rec)
    results = asyncio.run(downloader.download_files(
        [FileEntry("https://e.com/always-500.jpg", "always-500.jpg", "image")], tmp_path, make_client()))
    assert sleeps == [1, 2]  # 第三次失败直接终止，不再睡眠
    assert results[0].status == "failed"


def test_result_bytes_matches_content_length(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader.asyncio, "sleep", nosleep)
    results = asyncio.run(downloader.download_files(
        [FileEntry("https://e.com/a.jpg", "a.jpg", "image")], tmp_path, make_client()))
    assert results[0].status == "ok"
    assert results[0].bytes == len(CONTENT)  # 喂给 manifest 的字节数
