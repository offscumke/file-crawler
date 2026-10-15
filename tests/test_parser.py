from pathlib import Path

import parser  # 项目内 parser.py（stdlib parser 模块已在 Python 3.10 移除，无冲突）

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"
PAGE_URL = "https://example.com/page/index.html"


def entries():
    return parser.parse_page(FIXTURE.read_text(), PAGE_URL)


def test_relative_urls_resolved():
    urls = {e.url for e in entries()}
    # 相对链接基于页面目录 /page/ 解析；/ 开头为站点绝对路径；../ 上跳一级
    assert "https://example.com/page/images/photo.jpg" in urls
    assert "https://example.com/files/report.pdf" in urls
    assert "https://example.com/docs/spec.docx" in urls
    assert "https://example.com/page/media/clip.mp4" in urls


def test_non_file_links_excluded():
    urls = {e.url for e in entries()}
    # 断言的是解析后的真实 URL（相对路径基于页面目录 /page/ 解析）
    assert "https://example.com/page/page.html" not in urls        # <a> 无扩展名 → 排除
    assert "https://example.com/page/index.html#top" not in urls   # 锚点排除


def test_img_extensionless_kept_as_unknown():
    e = next(e for e in entries() if e.url == "https://cdn.example.org/media/noext")
    assert e.file_type == "unknown"


def test_type_detection():
    by_url = {e.url: e for e in entries()}
    assert by_url["https://example.com/page/images/photo.jpg"].file_type == "image"
    assert by_url["https://example.com/files/report.pdf"].file_type == "document"
    assert by_url["https://files.example.com/archive.zip"].file_type == "asset"
    assert by_url["https://example.com/page/media/clip.mp4"].file_type == "asset"
    assert by_url["https://example.com/docs/spec.docx"].file_type == "document"


def test_url_dedup_keeps_first():
    urls = [e.url for e in entries()]
    assert urls.count("https://example.com/page/images/photo.jpg") == 1


def test_filename_sanitized():
    by_url = {e.url: e for e in entries()}
    assert by_url["https://example.com/page/files/we:ird|name.txt.zip"].suggested_filename == "weirdname.txt.zip"


def test_filename_dedup_suffix():
    names = [e.suggested_filename for e in entries()]
    assert "photo.jpg" in names
    assert "photo-1.jpg" in names  # other/photo.jpg 与 images/photo.jpg 重名
