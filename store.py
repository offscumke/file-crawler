import csv
from datetime import datetime
from pathlib import Path

MANIFEST_COLUMNS = ["filename", "source_url", "status", "bytes", "error"]


def create_run_dir(base_dir: Path, domain: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = base_dir / f"{domain}-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_manifest(run_dir: Path, results) -> Path:
    path = run_dir / "manifest.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MANIFEST_COLUMNS)
        for r in results:
            w.writerow([r.filename, r.source_url, r.status, r.bytes, r.error])
    return path
