import csv
from dataclasses import dataclass
from pathlib import Path

import store  # downloader.py 在 Task 4 才存在，本测试用 duck-typing 传入等价对象


def make_result(**kw):
    defaults = dict(filename="a.jpg", source_url="https://e.com/a.jpg",
                    status="ok", bytes=10, error="")
    defaults.update(kw)
    return type("R", (), defaults)()


def test_create_run_dir(tmp_path):
    d = store.create_run_dir(tmp_path, "example.com")
    assert d.parent == tmp_path
    assert d.name.startswith("example.com-")
    assert d.is_dir()


def test_write_manifest(tmp_path):
    run_dir = store.create_run_dir(tmp_path, "example.com")
    results = [make_result(), make_result(filename="b.pdf", source_url="https://e.com/b.pdf",
                                           status="failed", bytes=0, error="HTTP 500")]
    path = store.write_manifest(run_dir, results)
    assert path == run_dir / "manifest.csv"
    rows = list(csv.reader(open(path, encoding="utf-8")))
    assert rows[0] == ["filename", "source_url", "status", "bytes", "error"]
    assert rows[1][0] == "a.jpg" and rows[1][2] == "ok"
    assert rows[2][4] == "HTTP 500"
