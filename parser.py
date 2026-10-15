from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, unquote

from bs4 import BeautifulSoup

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
DOC_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}
ASSET_EXTS = {".zip", ".rar", ".7z", ".tar.gz", ".mp4", ".mp3"}
EXT_TO_TYPE = {}
for _e in IMAGE_EXTS: EXT_TO_TYPE[_e] = "image"
for _e in DOC_EXTS: EXT_TO_TYPE[_e] = "document"
for _e in ASSET_EXTS: EXT_TO_TYPE[_e] = "asset"

INVALID_CHARS = set('/\\:*?"<>|')
CONTROL_CHARS = set(chr(c) for c in range(32))


@dataclass
class FileEntry:
    url: str
    suggested_filename: str
    file_type: str  # image | document | asset | unknown（仅来自 img/source 的无扩展名链接）


def detect_type(url: str) -> str | None:
    path = urlsplit(url).path.lower()
    for ext in sorted(EXT_TO_TYPE, key=len, reverse=True):  # 防御性排序：当前映射中没有任何扩展名是另一个的后缀，今天实为空操作
        if path.endswith(ext):
            return EXT_TO_TYPE[ext]
    return None


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c not in INVALID_CHARS and c not in CONTROL_CHARS).strip(". ")


def parse_page(html: str, page_url: str) -> list[FileEntry]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []  # (raw_ref, from_media_tag)
    for tag in soup.find_all("img"):
        for ref in (tag.get("src", "") + " " + tag.get("srcset", "")).split(","):
            ref = ref.split()[0] if ref.split() else ""
            if ref:
                candidates.append((ref, True))
    for tag in soup.find_all("source"):
        for ref in (tag.get("src", "") + " " + tag.get("srcset", "")).split(","):
            ref = ref.split()[0] if ref.split() else ""
            if ref:
                candidates.append((ref, True))
    for tag in soup.find_all("a"):
        if tag.get("href"):
            candidates.append((tag["href"], False))

    seen_urls = set()
    entries: list[FileEntry] = []
    for raw, from_media in candidates:
        url = urljoin(page_url, raw)
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        ftype = detect_type(url)
        if ftype is None and not from_media:
            continue  # <a> 的无扩展名链接视为页面链接，排除
        if ftype is None:
            ftype = "unknown"
        entries.append(FileEntry(url=url, suggested_filename="", file_type=ftype))

    # 文件名：URL 末段（去查询串）→ 清洗 → 空则兜底 file-<序号> → 重名在扩展名前加 -1/-2
    used_names = set()
    for i, e in enumerate(entries):
        raw_name = sanitize_filename(unquote(urlsplit(e.url).path.rsplit("/", 1)[-1]))
        base, dot, ext = raw_name.rpartition(".")
        if not base:
            base, dot, ext = (raw_name or f"file-{i + 1}"), "", ""
        name = f"{base}{dot}{ext}"
        final, n = name, 1
        while final.lower() in used_names:  # macOS/Windows 文件系统大小写不敏感，判重按小写；选定名保留原大小写
            final = f"{base}-{n}{dot}{ext}"
            n += 1
        used_names.add(final.lower())
        e.suggested_filename = final
    return entries
