# LongVideo-Notes

从长视频 / 音频生成结构化 Markdown 笔记的本地 CLI 工具。项目已具备 Python 包结构、CLI、核心缓存/配置/artifact 合同、媒体/ASR/LLM 边界模块，以及音频、合并、多模管线实现。

## 目标

把 1-3 小时的视频或音频转成高质量、结构化、可读的 Markdown 笔记，优先服务网课、讲座、播客、板书课等长形式教学内容。

核心约束：

- 质量优先于速度
- 所有阶段产物落盘，支持断点续跑和人工检查
- 本地 CLI 工具，不做 Web UI、数据库、任务队列或用户系统
- 第一版主要验证中文输入，prompt 模板不追求多语种通用性

## 使用

安装开发依赖后，可通过模块入口或 console script 运行：

```bash
python -m lvnotes --help
lvnotes run <input-path>
lvnotes run <input-path> --mm
lvnotes run ./courses --mm
lvnotes run <input-path> --head-minutes 10
lvnotes inspect audio refined <input-path>
lvnotes inspect merge note <input-path> --paths
lvnotes assemble <input-path> --no-cache
```

### 命令说明

| 命令 | 含义 |
|---|---|
| `lvnotes run <input-path>` | 端到端生成 Markdown 笔记。默认走纯音频模式,会依次跑音频管线和合并阶段。 |
| `lvnotes run <input-path> --mm` | 端到端多模运行。视频文件会额外抽帧、筛图、对齐画面并结合视觉内容生成笔记;音频文件仍走纯音频模式。 |
| `lvnotes inspect <namespace> <stage> <input-path>` | 只读查看已有中间产物或最终产物信息,不重新计算、不创建文件、不加写锁。常用于确认某个 stage 是否已产出结果。 |
| `lvnotes extract <input-path>` | 从音频或视频中抽取标准 wav,生成音频抽取元信息。 |
| `lvnotes transcribe <input-path>` | 读取抽取后的 wav,调用 ASR 生成原始转录。 |
| `lvnotes segment <input-path>` | 基于原始转录生成语义分段。 |
| `lvnotes refine <input-path>` | 清洗、整理分段转录,生成后续合并阶段使用的 refined transcript。 |
| `lvnotes sample <input-path> --mm` | 从视频中按配置采样原始帧。 |
| `lvnotes filter <input-path> --mm` | 对采样帧做本地去重和基础过滤。 |
| `lvnotes semantic-filter <input-path> --mm` | 用 VLM 判断过滤后帧是否有笔记价值。 |
| `lvnotes align <input-path> --mm` | 将保留的关键帧按时间戳对齐到 refined transcript 段落。 |
| `lvnotes describe <input-path> --mm` | 结合对应时间段的讲解文本,对关键画面生成详细视觉描述。 |
| `lvnotes unify <input-path>` | 把音频内容和可选视觉描述合并成统一内容块。 |
| `lvnotes outline <input-path>` | 基于内容块生成章节大纲。 |
| `lvnotes section <input-path>` | 按章节生成 Markdown 正文片段。 |
| `lvnotes assemble <input-path>` | 将章节片段组装成 latest 笔记、带时间戳归档笔记、每份笔记的图片资源目录和 cache debug copy。 |

常用选项：

| 选项 | 含义 |
|---|---|
| `--mm` | 对视频输入启用多模管线;音频输入仍保持纯音频模式。 |
| `--head-minutes <minutes>` | 只处理每个媒体文件开头指定分钟数,用于快速试跑。 |
| `--config <path>` | 指定配置文件路径;默认读取当前目录的 `config.yaml`。 |
| `--no-cache` | 跳过当前命令涉及 stage 的缓存读取,强制重新计算。 |
| `--debug` | 启用 refine 开发期审核流程,主要用于检查第一段清洗效果。 |
| `--paths` | `inspect` 专用,只输出目标产物路径。 |
| `--json` | `inspect` 专用,输出原始产物内容;目录输入时输出聚合 JSON。 |

输入路径可以是单个媒体文件或目录。目录输入会递归扫描支持的本地音视频文件,按相对路径排序后逐个处理；隐藏路径和已生成的 `*.head-<minutes>m.*` 裁剪文件会跳过。默认走纯音频模式；只有显式传 `--mm` 时视频文件才启用多模管线。目录输入加 `--mm` 时,视频文件走多模,音频文件自动走纯音频。
`lvnotes --help` 会展示推荐命令、模式规则、常用选项和 stage 调试入口。
各命令的 `--help` 会列出该命令会生成或读取的主要文件。
传 `--head-minutes <minutes>` 时，CLI 会先在每个媒体文件同目录生成或复用 `<stem>.head-<minutes>m<suffix>`，然后只处理该裁剪文件。
写入型命令会对每个媒体文件对应的 cache 目录获取独占锁；`inspect` 保持只读，不加锁也不创建目录。
运行时会显示 stage 级状态；ASR、refine、visual describe、merge section 等可计数长任务会显示进度条。`visual describe` 和 `merge section` 会按配置并发调用 LLM/VLM。
单文件输入的最终 Markdown 会写入 `output/<source-stem>.md`，并同时写入 `output/<source-stem>-YYYYMMDD-HHMMSS.md` 作为本次导出归档。每个 Markdown 文件旁会生成同名图片资源目录，例如 `output/<source-stem>_assets/000001.png` 和 `output/<source-stem>-YYYYMMDD-HHMMSS_assets/000001.png`；Markdown 图片链接只指向同目录下的 `_assets/`。目录输入会在 `output/` 下保留输入目录内的相对目录结构,避免同名文件互相覆盖。

CLI 默认查找当前目录下的 `config.yaml`，也可通过 `--config <path>` 指定配置文件。可从 `config.example.yaml` 复制并按本地 LLM / ASR 环境调整。
LLM profile 可配置 reasoning / thinking 默认参数；所有映射到该 profile 的任务都会继承。

运行真实管线需要：

- `ffmpeg` / `ffprobe`
- 有效的 `config.yaml`
- 对应 LLM profile 的 API key 环境变量，或本地 OpenAI-compatible endpoint
- 运行 ASR 时安装 `faster-whisper` 可选依赖

## 文档

- `docs/overview.md` —— 项目级入口：目标、流程、模块边界、配置示例、实现优先级
- `docs/cli.md` —— CLI 权威：命令、参数、模式规则、调度与缓存控制
- `docs/llm.md` —— LLM 权威：provider 抽象、profile、JSON helper、错误归一化
- `docs/core.md` —— 架构/实现约束权威之一：schema、artifacts、paths、cache、config、context
- `docs/coding-standards.md` —— 架构/实现约束权威之一：开发规范、模块边界、agent 写代码约束
- `docs/media.md` —— media 模块权威：ffmpeg / ffprobe 唯一入口
- `docs/asr.md` —— ASR 模块权威：ASR 抽象与 faster-whisper 本地实现
- `docs/audio-pipeline.md` —— 音频管线权威：extract / transcribe / segment / refine
- `docs/visual-pipeline.md` —— 多模管线权威：sample / filter / semantic_filter / align / describe
- `docs/merge.md` —— 合并阶段权威：unify / outline / section / assemble 与最终 Markdown 生成

## 当前状态

- 已创建 `lvnotes/` Python 包结构与 `pyproject.toml`
- 已实现 `core/` schema、序列化、路径、缓存、配置、context、artifacts、日志与异常
- 已实现 `media/`、`llm/`、`asr/` 边界模块
- 已实现 CLI、音频管线、合并阶段和多模管线
- 已添加 `config.example.yaml`、`.importlinter` 和核心单元测试
- 真实端到端质量仍依赖本地媒体 fixture、ASR 模型、LLM/VLM 配置与讲座场景 prompt 调优
