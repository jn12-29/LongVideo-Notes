# LongVideo-Notes

从长视频 / 音频生成结构化 Markdown 笔记的本地 CLI 工具。项目当前处于**设计文档构建阶段**，实现尚未开始。

## 目标

把 1-3 小时的视频或音频转成高质量、结构化、可读的 Markdown 笔记，优先服务网课、讲座、播客、板书课等长形式教学内容。

核心约束：

- 质量优先于速度
- 所有阶段产物落盘，支持断点续跑和人工检查
- 本地 CLI 工具，不做 Web UI、数据库、任务队列或用户系统
- 第一版主要验证中文输入，prompt 模板不追求多语种通用性

## 预期形态

最终 CLI 入口计划为：

```bash
lvnotes run <input-file>
lvnotes run <input-file> --mm
lvnotes inspect audio refined <input-file>
lvnotes inspect merge note <input-file> --paths
lvnotes assemble <input-file> --no-cache
```

默认走纯音频模式；只有显式传 `--mm` 时才启用多模管线。

当前命令尚未实现；以上是 CLI 规格目标，不是当前可执行说明。CLI 命令、参数、模式规则与调度行为以 `docs/cli.md` 为权威。

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

- 已完成项目级设计文档
- 已完成 core / media / llm / asr / cli 模块设计文档
- 已完成音频管线设计文档
- 已完成合并阶段设计文档
- 已完成多模管线设计文档（第一版可仅占位实现）
- 尚未创建 Python 包结构、配置文件和测试
