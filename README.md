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
lvnotes inspect <stage>
lvnotes assemble --no-cache
```

默认走纯音频模式；只有显式传 `--mm` 时才启用多模管线。

当前命令尚未实现，具体行为以 `docs/overview.md` 和各管线文档为准。

## 文档

- `docs/overview.md` —— 项目级设计、模块边界、配置示例、实现优先级
- `docs/coding-standards.md` —— 开发规范与 agent 写代码约束
- `docs/core.md` —— core 框架层设计：schema、artifacts、paths、cache、config、context
- `docs/media.md` —— ffmpeg / ffprobe 唯一入口设计
- `docs/llm.md` —— LLM provider 抽象、profile、JSON helper、错误归一化
- `docs/asr.md` —— ASR 抽象与 faster-whisper 本地实现设计
- `docs/audio-pipeline.md` —— 音频管线详细设计
- `docs/visual-pipeline.md` —— 多模管线详细设计（第一版可仅占位实现）
- `docs/merge.md` —— 合并阶段与最终 Markdown 生成设计
- `docs/cli.md` —— CLI 命令、模式规则、调度与缓存控制设计

## 当前状态

- 已完成项目级设计文档
- 已完成 core / media / llm / asr / cli 模块设计文档
- 已完成音频管线设计文档
- 已完成合并阶段设计文档
- 已完成多模管线设计文档（第一版可仅占位实现）
- 尚未创建 Python 包结构、配置文件和测试
