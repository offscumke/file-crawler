# File Crawler

简单的网页文件批量下载工具，支持从单个网页解析并下载图片、PDF、Office 文档等文件。

## 功能特性

- 🎯 **智能解析**：自动识别网页中的图片（`<img>`、`srcset`）和文件链接（`<a>`）
- 📦 **批量下载**：支持勾选文件并发下载（并发数 8，自动重试 3 次）
- 📊 **实时进度**：Web 界面实时显示下载状态（queued / downloading / ok / failed / skipped）
- 💾 **自动归档**：按域名和时间自动创建目录，生成 `manifest.csv` 清单
- 🔄 **智能重试**：失败自动重试，指数退避（1s / 2s / 4s）
- 🎨 **简洁界面**：原生 HTML/JS 单页应用，无需构建

## 支持的文件类型

- **图片**：jpg, jpeg, png, gif, bmp, webp, svg, ico
- **文档**：pdf, doc, docx, xls, xlsx, ppt, pptx
- **压缩包**：zip, rar, 7z, tar, gz
- **视频/音频**：mp4, avi, mov, mp3, wav, flac
- 其他：通过 Content-Type 动态识别

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装

```bash
# 克隆仓库
git clone https://github.com/offscumke/file-crawler.git
cd file-crawler

# 安装依赖
pip install -r requirements.txt
```

### 运行

```bash
# 启动服务
uvicorn app:app --port 8000

# 或使用 Python 虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8000
```

浏览器打开 http://127.0.0.1:8000

## 使用说明

1. **输入网址**：粘贴要解析的网页 URL
2. **解析文件**：点击「解析」按钮，等待文件列表加载
3. **勾选文件**：默认全选，可取消不需要的文件
4. **开始下载**：点击「开始下载」，实时查看进度
5. **查看结果**：下载完成后显示保存目录和统计信息

下载的文件保存在 `runs/<域名>-<任务ID>-<时间戳>/` 目录下，同时生成 `manifest.csv` 清单文件。

## 项目结构

```
file-crawler/
├── app.py              # FastAPI 应用入口
├── parser.py           # HTML 解析器（识别文件链接）
├── downloader.py       # 异步批量下载器
├── store.py            # 文件存储与清单生成
├── index.html          # Web 前端界面
├── requirements.txt    # 依赖清单
└── tests/              # 测试套件（34 个测试）
    ├── test_parser.py
    ├── test_downloader.py
    ├── test_app.py
    ├── test_store.py
    └── test_e2e.py
```

## 技术栈

- **后端**：FastAPI + httpx + BeautifulSoup4
- **前端**：原生 HTML/CSS/JavaScript
- **测试**：pytest（34/34 通过）

## 开发

```bash
# 运行测试
pytest -v

# 运行特定测试文件
pytest tests/test_parser.py -v

# 查看测试覆盖
pytest --cov=. --cov-report=html
```

## API 文档

启动服务后访问 http://127.0.0.1:8000/docs 查看交互式 API 文档。

### 主要接口

- `POST /api/parse` - 解析网页，返回文件列表
- `POST /api/jobs` - 创建下载任务
- `GET /api/jobs/{job_id}` - 查询任务状态和进度

## 限制说明

- 单用户设计，同一时刻只能运行一个下载任务
- 不支持需要登录的页面
- 不支持 JavaScript 渲染的动态内容
- 服务重启后任务状态丢失（内存存储）

## 许可证

MIT

## 贡献

欢迎提交 Issue 和 Pull Request！

## 更新日志

### v1.0.0 (2026-10-15)

- ✨ 初始版本发布
- ✅ 34 个测试全部通过
- 📝 完整的设计文档和实现计划
