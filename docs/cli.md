# CLI Module

CLI 设计文档。`lvnotes/cli/app.py` 是项目唯一命令行入口,负责命令解析、配置加载、管线调度、缓存控制、错误处理、进度显示与最终结果输出。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`、`docs/audio-pipeline.md`、`docs/visual-pipeline.md`、`docs/merge.md`。

文档结构:Overview、Design Considerations、Commands、Mode Rules、Scheduling、Cache Control、Inspect、Progress & Logging、Error Handling、Module Layout、Dependencies、Implementation Order。

---

## 1. Overview

CLI 提供四类入口:

| 类型 | 命令 | 用途 |
|---|---|---|
| 端到端运行 | `lvnotes run <input-file>` | 默认纯音频模式,跑音频管线 + 合并阶段 |
| 端到端多模 | `lvnotes run <input-file> --mm` | 显式启用多模 |
| 查看产物 | `lvnotes inspect <namespace> <stage> <input-file>` | 查看中间产物摘要或路径 |
| 单 stage 重跑 | `lvnotes extract` / `transcribe` / `segment` / `refine` / `sample` / `cluster` / `judge` / `select` / `describe` / `unify` / `outline` / `section` / `assemble` | 调试、断点续跑、人工编辑后局部重跑 |

CLI 的职责:

- 解析参数
- 加载并校验配置
- 创建 `PipelineContext`
- 按模式调度 stage
- 控制缓存开关
- 处理顶层异常
- 展示进度与最终输出路径

CLI 的非职责:

- 不写 ffmpeg / ASR / LLM / VLM 业务逻辑
- 不拼接缓存路径
- 不实现 stage 内部算法
- 不绕开 `core/`、`media/`、`asr/`、`llm/` 的唯一入口

---

## 2. Design Considerations

### 2.1 `--mm` 是多模唯一启用来源

多模模式只由 CLI 参数 `--mm` 决定。

配置文件不提供 `multimodal.enabled`、`visual_pipeline.enabled`、`mode` 之类开关。避免 CLI 与配置形成双源事实。

规则:

- 输入是音频文件:强制纯音频模式
- 输入是视频文件且未传 `--mm`:纯音频模式
- 输入是视频文件且显式传 `--mm`:多模模式
- 配置只提供各 stage 参数,不提供模式启停

### 2.2 `--debug` 只属于 refine 开发期调试

`--debug` 用于 refine stage 第一段后的人工审核。它不进入配置文件,不参与缓存键,不影响其他 stage。

规则:

- 默认关闭
- 只在 `lvnotes refine <input-file> --debug` 或端到端调试 refine 时生效
- 不写入 `core/config.py`
- 不进入业务 schema

### 2.3 CLI 不是业务层

CLI 可以 import stage 模块并调用 `run(ctx)`,但不能复制 stage 逻辑。

允许:

```python
result = refine.run(ctx)
```

不允许:

```python
prompt = build_refine_prompt(...)
response = llm_client.complete(prompt)
write_refined_segment(response)
```

### 2.4 单 stage 命令不是第二套管线

顶层 stage 命令必须与 `run` 内调度到同一 stage 时使用同一实现。每个 stage 命令最终都收敛到:

```python
stage_module.run(ctx)
```

### 2.5 进度显示归 CLI,日志归 logging

模块内部使用 logging,不使用 `print`。CLI 可以 `print` 面向用户的进度、摘要和最终路径。

---

## 3. Commands

### 3.1 `lvnotes run`

端到端入口:

```bash
lvnotes run <input-file>
lvnotes run <input-file> --mm
lvnotes run <input-file> --head-minutes 10
lvnotes run <input-file> --config config.yaml
lvnotes run <input-file> --no-cache
lvnotes run <input-file> --debug
```

默认纯音频行为:

```text
extract → transcribe → segment → refine → unify → outline → section → assemble
```

多模行为:

```text
audio:  extract → transcribe → segment → refine ┐
                                                     ├─ describe → unify → outline → section → assemble
visual: sample  → cluster    → judge   → select ────┘
```

`describe` 必须等 `AudioArtifacts.is_complete() == True`,并通过 `AudioArtifacts.get_text_at(start, end, strip_refs=True)` 读取讲解文本。

`--head-minutes <minutes>` 会先在输入文件同目录生成或复用开头片段文件,再将该片段作为本次运行的输入。片段文件名为 `<source-stem>.head-<minutes>m<source-suffix>`,例如 `lecture.mp4 --head-minutes 10` 对应 `lecture.head-10m.mp4`。后续 `PipelineContext.source_path`、输入 hash、缓存目录、输出 Markdown 名称和所有 stage 输入都基于该片段文件。

`run --help` 必须说明端到端主要产物:音频中间产物、合并中间产物、最终 latest Markdown、带时间戳归档 Markdown 与 cache debug copy。`run --help` 的多模说明还必须列出视觉帧目录与视觉 JSON 产物。

### 3.2 `lvnotes inspect`

查看中间产物,不触发计算。

建议命令:

```bash
lvnotes inspect audio refined <input-file>
lvnotes inspect visual describe <input-file>
lvnotes inspect merge outline <input-file>
lvnotes inspect merge note <input-file> --paths
lvnotes inspect merge note <input-file> --head-minutes 10 --paths
```

支持选项:

- `--json`:输出原始产物内容
- `--paths`:只输出对应产物路径
- `--head-minutes <minutes>`:查看已存在开头片段对应的产物;不会创建片段文件

默认输出摘要,不打印长正文。

`inspect --help` 必须明确 `inspect` 不生成文件,只读取已有 artifact,并列出可读取的 audio / visual / merge artifact 名称。

### 3.3 音频 stage 子命令

```bash
lvnotes extract <input-file>
lvnotes transcribe <input-file>
lvnotes segment <input-file>
lvnotes refine <input-file>
lvnotes refine <input-file> --head-minutes 10
lvnotes refine <input-file> --debug
lvnotes refine <input-file> --no-cache
```

| 命令 | 调用 stage | 依赖 |
|---|---|---|
| `extract` | `audio_pipeline.extract.run(ctx)` | 输入文件 |
| `transcribe` | `audio_pipeline.transcribe.run(ctx)` | extract 产物 |
| `segment` | `audio_pipeline.segment.run(ctx)` | transcript 产物 |
| `refine` | `audio_pipeline.refine.run(ctx)` | transcript + segments |

音频 stage help 必须列出主要产物:

| 命令 | 主要产物 |
|---|---|
| `extract` | `cache/{input_hash}/audio/audio.wav`, `cache/{input_hash}/audio/extract.json` |
| `transcribe` | `cache/{input_hash}/transcript_raw.json` |
| `segment` | `cache/{input_hash}/segments.json` |
| `refine` | `cache/{input_hash}/refined_transcript.json`, `cache/{input_hash}/refined/{seg_id:04d}.json` |

`refine --debug` 行为:

1. 运行第一段 refine
2. 打印第一段产物摘要
3. 允许用户审核或按约定编辑落盘 JSON
4. 重新加载第一段产物
5. 继续后续段

`run --debug` 只影响端到端运行中的 refine stage,语义与 `lvnotes refine <input-file> --debug` 相同。其他 stage 忽略该开关。

### 3.4 多模 stage 子命令

```bash
lvnotes sample <input-file> --mm
lvnotes sample <input-file> --mm --head-minutes 10
lvnotes cluster <input-file> --mm
lvnotes judge <input-file> --mm
lvnotes select <input-file> --mm
lvnotes describe <input-file> --mm
```

| 命令 | 调用 stage | 依赖 |
|---|---|---|
| `sample` | `visual_pipeline.sample.run(ctx)` | 视频输入 + `--mm` |
| `cluster` | `visual_pipeline.cluster.run(ctx)` | sample 产物 |
| `judge` | `visual_pipeline.judge.run(ctx)` | cluster 产物 |
| `select` | `visual_pipeline.select.run(ctx)` | judge 产物 |
| `describe` | `visual_pipeline.describe.run(ctx)` | select 产物 + audio refined |

多模 stage help 必须列出主要产物:

| 命令 | 主要产物 |
|---|---|
| `sample` | `cache/{input_hash}/visual/frames/`, `cache/{input_hash}/visual/sample.json` |
| `cluster` | `cache/{input_hash}/visual/segments.json` |
| `judge` | `cache/{input_hash}/visual/judgements.json` |
| `select` | `cache/{input_hash}/visual/selections.json` |
| `describe` | `cache/{input_hash}/visual/descriptions.json` |

多模 stage 命令必须要求 `--mm`,未传时明确拒绝执行。不能因为用户调用了 `describe` 就隐式启用多模。

### 3.5 合并 stage 子命令

```bash
lvnotes unify <input-file>
lvnotes unify <input-file> --mm
lvnotes unify <input-file> --head-minutes 10
lvnotes outline <input-file>
lvnotes outline <input-file> --mm
lvnotes section <input-file>
lvnotes section <input-file> --mm
lvnotes section <input-file> --no-cache
lvnotes assemble <input-file>
lvnotes assemble <input-file> --mm
lvnotes assemble <input-file> --no-cache
```

| 命令 | 调用 stage | 依赖 |
|---|---|---|
| `unify` | `merge.unify.run(ctx)` | audio refined + 可选 visual descriptions |
| `outline` | `merge.outline.run(ctx)` | content blocks |
| `section` | `merge.section.run(ctx)` | outline + content blocks |
| `assemble` | `merge.assemble.run(ctx)` | outline + content blocks + sections |

合并 stage help 必须列出主要产物:

| 命令 | 主要产物 |
|---|---|
| `unify` | `cache/{input_hash}/content_blocks.json` |
| `outline` | `cache/{input_hash}/outline.json` |
| `section` | `cache/{input_hash}/sections/{chapter_id:03d}.md` |
| `assemble` | `output_dir/<source-stem>.md`, `output_dir/<source-stem>-YYYYMMDD-HHMMSS.md`, `cache/{input_hash}/note.md` |

合并 stage 命令的 `--mm` 语义与 `run --mm` 一致:视频输入且显式传 `--mm` 时创建带 `VisualArtifacts` 的 context;未传 `--mm` 时按纯音频模式创建 context。音频输入传 `--mm` 仍报错。这样 assemble frontmatter 的 `mode` 始终来自本次 CLI 模式。

`assemble --no-cache` 用于用户编辑 `sections/*.md` 后,跳过 assemble 缓存,重读 sections 并重新生成 latest 与带时间戳归档笔记。不应重跑 section LLM。

---

## 4. Mode Rules

### 4.1 输入类型判断

CLI 通过 `media/probe.py` 识别输入是音频还是视频,不直接调用 ffprobe。

| 输入 | `--mm` | 模式 |
|---|---:|---|
| 音频文件 | 否 | 纯音频 |
| 音频文件 | 是 | 报错退出 |
| 视频文件 | 否 | 纯音频 |
| 视频文件 | 是 | 多模 |

推荐对“音频文件 + `--mm`”直接报错:

```text
--mm requires a video input; audio files always run in audio-only mode.
```

### 4.2 配置不参与模式启停

配置文件可以有 `visual_pipeline.*` 参数,但这不代表启用多模。只有 `--mm` 启用多模。

### 4.3 模式写入最终元信息

`assemble` 生成最终 Markdown frontmatter 时写入:

```yaml
mode: audio_only
```

或:

```yaml
mode: multimodal
```

该值来自 CLI 本次运行模式,不来自配置文件。

### 4.4 `--head-minutes` 输入裁剪

`--head-minutes <minutes>` 支持视频和音频输入。CLI 在创建 `PipelineContext` 前处理该参数:

1. 解析原始输入路径
2. 在原始输入同目录定位 `<source-stem>.head-<minutes>m<source-suffix>`
3. `run` 和 stage 命令在片段不存在时创建片段,片段已存在时默认复用
4. `inspect` 只使用已存在片段,片段不存在时报错
5. 对片段执行媒体探测、输入 hash、路径构建和模式判断

复用片段时必须确认文件非空、可被 `ffprobe` 读取且包含 audio stream。多模模式仍遵循 `--mm` 规则:片段必须包含 video stream。

`--no-cache` 不强制重建片段文件,只控制 stage 缓存读取。
CLI 创建 `PipelineContext.mode`,值只能是 `"audio_only"` 或 `"multimodal"`;assemble frontmatter 直接使用该字段。

---

## 5. Scheduling

### 5.1 纯音频模式

```text
extract
  ↓
transcribe
  ↓
segment
  ↓
refine
  ↓
unify
  ↓
outline
  ↓
section
  ↓
assemble
```

说明:

- 音频管线 stage 1-4 严格顺序
- 合并阶段 stage 1-4 严格顺序
- `section` 内部可并发,对外仍是同步 `run(ctx) -> StageOutput`
- 多模管线整体跳过

### 5.2 多模模式

约束:

- 音频管线 stage 1-4 顺序执行
- 多模管线 stage 1-4 顺序执行
- 音频管线和多模管线 stage 1-4 可并行
- `visual describe` 必须等待 `audio refined`
- 合并阶段必须等待所有上游完成

调度层可用 `asyncio`、线程池或同步轮询实现,但 stage 对外接口保持同步。

### 5.3 `visual describe` 等待 audio refined

启动条件:

```python
audio_artifacts.is_complete() is True
```

等待逻辑属于 CLI 调度层,不属于 `AudioArtifacts`。`AudioArtifacts.is_complete()` 只做轻量存在性检查。

### 5.4 并行边界

允许并行:

- `run --mm` 下音频管线与多模管线前 4 个 stage 并行
- `merge.section` 内每章 LLM 调用并发

不允许并行:

- `audio refine` 段间并行
- 同一条管线 stage 间乱序执行
- `visual describe` 早于 `audio refined`
- 合并阶段早于上游完成

---

## 6. Cache Control

### 6.1 默认使用缓存

默认所有命令使用 stage 缓存。缓存命中时:

- 不重复执行昂贵计算
- 打 INFO 日志
- CLI 显示 cache hit
- 返回对应 `StageOutput`

### 6.2 `--no-cache`

`--no-cache` 表示本次命令跳过目标 stage 的缓存读取,强制重新计算目标 stage。

示例:

```bash
lvnotes run lecture.mp4 --no-cache
lvnotes refine lecture.mp4 --no-cache
lvnotes assemble lecture.mp4 --no-cache
```

语义:

| 命令 | `--no-cache` 影响 |
|---|---|
| `run --no-cache` | 对本次端到端涉及的 stage 跳过缓存读取 |
| `refine --no-cache` | 删除本次 refine cache manifest 对应的命中资格,清空 `refined/*.json` 后重跑全部段 |
| `assemble --no-cache` | 跳过 assemble 缓存,重读 outline / blocks / sections |
| `inspect --no-cache` | 不支持 |

`--no-cache` 不等于删除整个 cache 目录。CLI 不自己计算业务缓存键。

refine 的断点续跑只在 cache key 未变且没有 `--no-cache` 时使用已完成的 `refined/{seg_id:04d}.json`。cache key 变化或显式 `refine --no-cache` 时,旧分段产物不再可信,stage 必须清空 `refined/` 后从第一段重跑。

section 的 `--no-cache` 语义与 per-chapter cache 一致:跳过每章 manifest 命中判断,重新生成所有章节并覆盖 `sections/{chapter_id:03d}.md`;用户只想基于手工编辑的 sections 重新合成时应运行 `assemble --no-cache`,不要运行 `section --no-cache`。

---

## 7. Inspect

`inspect` 用于人工检查:

- 哪些 stage 已经跑完
- 产物在哪里
- 产物大致内容是否合理
- 失败是否因为上游缺产物

`inspect` 不触发计算。产物缺失时提示先运行对应 stage。

默认输出示例:

```text
stage: audio refined
input_hash: abc123
path: cache/abc123/refined_transcript.json
size_bytes: 12345
```

默认不打印完整 raw transcript、完整 refined text、完整 prompt、完整 section markdown 或图片内容。

---

## 8. Progress & Logging

CLI 控制台输出面向用户,简洁可读:

```text
Input: lecture.mp4
Mode: multimodal
Cache: enabled

audio.extract: running
visual.sample: running
audio.extract: done
visual.sample: done
audio.transcribe: running
audio.transcribe:  42%|████▏     | 1520/3600s
audio.transcribe: done
Output:
output/lecture.md
output/lecture-20260509-085423.md
cache/abc123/note.md
```

CLI 始终显示 stage 级 `running` / `done` / `cache hit`。可计数的长耗时任务使用 `tqdm` 进度条:ASR 按音频秒数近似推进,refine 按 segment 推进,visual describe 按 selection 推进,merge section 按 chapter job 推进。非 TTY 输出禁用动态进度条,只保留普通状态行和日志。

日志遵循 `coding-standards.md`:

| 级别 | 用途 |
|---|---|
| DEBUG | 详细内部状态,仅文件日志 |
| INFO | stage 开始、完成、缓存命中 |
| WARNING | 可继续的降级、重试、marker 异常保留 |
| ERROR | 命令失败前记录 |

禁止记录 API key、Authorization header、完整 prompt、大段转录正文、图片 base64。

---

## 9. Error Handling

CLI 顶层 catch 项目定义的可预期异常:

- `ConfigError`
- `CacheError`
- `MediaError`
- `ASRError`
- `LLMError`
- `AuthError`
- `RateLimitError`
- `ContextLengthError`
- `TransportError`

行为:

1. 记录 ERROR 日志
2. 控制台输出简明错误
3. 返回非 0 exit code

常见错误:

| 场景 | 处理 |
|---|---|
| 输入文件不存在 | 清晰错误,非 0 退出 |
| 配置缺失或未知字段 | `ConfigError` |
| `--mm` 用在音频文件 | CLI 参数错误 |
| 未传 `--mm` 调用 visual stage | CLI 参数错误 |
| `visual describe` 缺 audio refined | `CacheError`,提示先运行 refine |
| LLM 限流 | 底层重试后仍失败则 `RateLimitError` |
| section 缺失 | `CacheError`,提示先运行 section |

不可预期错误不吞掉,让顶层 logging 记录 traceback 并退出非 0。

---

## 10. Module Layout

```text
lvnotes/cli/
└── app.py
```

`app.py` 负责注册命令。可用 Typer 或 Click。

Import 规则:

| 来源 | 允许? |
|---|---|
| `core/` | ✅ |
| `audio_pipeline/*` stage 模块 | ✅ |
| `visual_pipeline/*` stage 模块 | ✅ |
| `merge/*` stage 模块 | ✅ |
| `media/probe.py` | ✅,用于输入类型判断 |
| `media/trim.py` | ✅,用于 `--head-minutes` 输入裁剪 |
| `llm/` 直接调用 client | ❌ |
| `asr/` 直接调用 transcriber | ❌ |
| `subprocess` 调 ffmpeg | ❌ |
| 直接拼接 cache 路径 | ❌ |

---

## 11. Dependencies

项目内:

- `core/config.py`
- `core/context.py`
- `core/paths.py`
- `core/artifacts.py`
- `core/logging.py`
- `core/progress.py`
- `core/exceptions.py`
- `audio_pipeline/`
- `visual_pipeline/`
- `merge/`
- `media/probe.py`
- `media/trim.py`

预计外部库:

- `typer` 或 `click`
- `tqdm`

具体依赖清单以 `pyproject.toml` 为准。

---

## 12. CLI Validation

CLI 验收覆盖：

- `python -m lvnotes --help` 与 `lvnotes --help` 入口一致。
- 顶层 help 输出推荐工作流、模式规则、常用示例和常用选项。
- 顶层 help 的 Commands 区按使用顺序展示 `run`、`inspect`、音频 stage、多模 stage、合并 stage,并为每个命令展示简短用途。
- `run --help`、`inspect --help` 与每个 stage command 的 help 展示对应命令会生成或读取的主要产物。
- `lvnotes run <input-file>` 走纯音频路径。
- `lvnotes run <video> --mm` 走多模路径。
- 每个 stage 命令调用同名 stage 的 `run(ctx)`。
- `inspect` 只读取产物，不触发计算。
- `--no-cache`、`--debug`、`--mm` 的语义符合本文规则。

顶层 help 指导内容：

```text
Recommended workflow:
  lvnotes run <input-file>
  lvnotes run <input-file> --mm
  lvnotes run <input-file> --head-minutes 10
  lvnotes inspect audio refined <input-file>
  lvnotes inspect merge note <input-file> --paths
  lvnotes inspect merge note <input-file> --head-minutes 10 --paths
  lvnotes assemble <input-file> --no-cache

Modes:
  Audio files and video files without --mm run in audio-only mode.
  Video files with --mm run in multimodal mode.

Useful options:
  --head-minutes N  Process only the first N minutes; inspect reads an existing trim.
  --config PATH     Load a specific config file.
  --no-cache        Recompute stages run by commands that support it.
  --debug           Enable refine review flow during refine.
  --paths           Print only the artifact path in inspect.
  --json            Print raw artifact content in inspect.
```

---

## 13. Command Reference

```bash
lvnotes run lecture.mp4
lvnotes run lecture.mp4 --mm
lvnotes run lecture.mp4 --head-minutes 10

lvnotes extract lecture.mp4
lvnotes transcribe lecture.mp4
lvnotes segment lecture.mp4
lvnotes refine lecture.mp4
lvnotes refine lecture.mp4 --head-minutes 10
lvnotes refine lecture.mp4 --debug
lvnotes refine lecture.mp4 --no-cache

lvnotes sample lecture.mp4 --mm
lvnotes sample lecture.mp4 --mm --head-minutes 10
lvnotes cluster lecture.mp4 --mm
lvnotes judge lecture.mp4 --mm
lvnotes select lecture.mp4 --mm
lvnotes describe lecture.mp4 --mm

lvnotes unify lecture.mp4
lvnotes outline lecture.mp4
lvnotes section lecture.mp4
lvnotes section lecture.mp4 --no-cache
lvnotes assemble lecture.mp4
lvnotes assemble lecture.mp4 --no-cache

lvnotes inspect audio refined lecture.mp4
lvnotes inspect visual describe lecture.mp4
lvnotes inspect merge outline lecture.mp4
lvnotes inspect merge note lecture.mp4 --paths
lvnotes inspect merge note lecture.mp4 --head-minutes 10 --paths
```

核心不变量:

1. `--mm` 是多模唯一启用来源
2. 配置不提供 enabled 开关
3. `--debug` 只属于 refine 开发期调试,不进入配置
4. CLI 不写业务逻辑
5. `visual describe` 必须等待 audio refined
6. `assemble --no-cache` 用于人工编辑 sections 后重新合成笔记
7. 所有 stage 单独可调用,且与 `run` 使用同一套 stage 实现
8. `--head-minutes` 让本次命令处理同目录开头片段文件,不改变 stage 实现
