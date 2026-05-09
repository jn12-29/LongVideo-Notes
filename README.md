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
lvnotes run <input-file>
lvnotes run <input-file> --mm
lvnotes inspect audio refined <input-file>
lvnotes inspect merge note <input-file> --paths
lvnotes assemble <input-file> --no-cache
```

默认走纯音频模式；只有显式传 `--mm` 时才启用多模管线。

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
- `docs/visual-pipeline.md` —— 多模管线权威：sample / cluster / judge / select / describe
- `docs/merge.md` —— 合并阶段权威：unify / outline / section / assemble 与最终 Markdown 生成

## 当前状态

- 已创建 `lvnotes/` Python 包结构与 `pyproject.toml`
- 已实现 `core/` schema、序列化、路径、缓存、配置、context、artifacts、日志与异常
- 已实现 `media/`、`llm/`、`asr/` 边界模块
- 已实现 CLI、音频管线、合并阶段和多模管线
- 已添加 `config.example.yaml`、`.importlinter` 和核心单元测试
- 真实端到端质量仍依赖本地媒体 fixture、ASR 模型、LLM/VLM 配置与讲座场景 prompt 调优
