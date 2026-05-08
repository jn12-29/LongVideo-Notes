# Core Module

`core/` 模块设计文档。本模块是 LongVideo-Notes 的框架层,提供跨模块共享的 schema、路径、缓存、配置、上下文、异常和产物访问接口。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`。

## 目录

- [1. Overview](#1-overview)
- [2. Design Considerations](#2-design-considerations)
- [3. Module Details](#3-module-details)
- [4. Stage Contract](#4-stage-contract)
- [5. Artifacts Contract](#5-artifacts-contract)
- [6. Path Contract](#6-path-contract)
- [7. Config Contract](#7-config-contract)
- [8. Module Layout](#8-module-layout)
- [9. Dependencies](#9-dependencies)
- [10. Implementation Order](#10-implementation-order)

---

## 1. Overview

`core/` 只提供基础设施,不写业务逻辑。

| 文件 / 目录 | 职责 |
|---|---|
| `schemas/` | 跨模块 dataclass / pydantic schema |
| `artifacts.py` | `AudioArtifacts` / `VisualArtifacts` 产物访问接口 |
| `paths.py` | 缓存路径和输出路径唯一入口 |
| `timestamps.py` | 时间戳格式化、解析、marker 处理唯一入口 |
| `slugs.py` | Markdown anchor / slug 生成唯一入口 |
| `pipeline.py` | stage 统一契约与 `StageOutput` |
| `cache.py` | 内容 hash、原子写入、prompt 模板 hash |
| `config.py` | 配置 schema、加载、校验、封闭任务名集合 |
| `context.py` | `PipelineContext` 运行上下文 |
| `exceptions.py` | 项目异常层次 |
| `constants.py` | 真正跨模块的常量 |
| `logging.py` | logging 初始化 |

`core/` 可以被 `audio_pipeline/`、`visual_pipeline/`、`merge/`、`media/`、`llm/`、`asr/`、`cli/` 依赖。`core/` 反过来不依赖任何业务模块。

---

## 2. Design Considerations

### 2.1 `core/` 不写业务

允许放入 `core/` 的代码必须同时满足:

- 被多个模块共同依赖
- 不包含 audio / visual / merge 的 stage 逻辑
- 不调用 ffmpeg、ASR、LLM
- 能作为项目唯一入口规则的一部分

禁止在 `core/` 中出现:

- segment / refine / unify / assemble 的业务算法
- prompt 组装逻辑
- `--mm` 模式判断
- `--debug` 的交互逻辑
- 对具体 stage 的特殊分支

### 2.2 唯一入口规则

`core/` 承载以下唯一入口:

| 资源 | 唯一入口 |
|---|---|
| 缓存路径 | `core/paths.py` |
| 时间戳格式化 / marker 解析 | `core/timestamps.py` |
| slug / anchor 生成 | `core/slugs.py` |
| 跨管线产物访问 | `core/artifacts.py` |
| 原子文件写入 | `core/cache.py` 的 `atomic_write_*` |
| prompt 模板 hash | `core/cache.py` 的 `hash_prompt_template` |
| 配置加载与校验 | `core/config.py` |
| 项目异常类型 | `core/exceptions.py` |

错误示例:

```python
path = Path("./cache") / input_hash / "segments.json"
```

正确示例:

```python
path = ctx.paths.segments_json
```

### 2.3 schema 只放内容

跨模块 schema 描述产物内容,不携带生成配置元信息。

禁止字段:

- `config_hash`
- `prompt_hash`
- `model`
- `target_count_hint`
- `cache_key`

这些信息放在 `StageOutput.metadata` 或缓存 sidecar 中。理由:缓存键已经表达配置事实,数据 schema 再存一份会制造双源事实。

### 2.4 stage 签名统一

所有 stage 暴露同一签名:

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

stage 不接收散落参数。输入路径、配置、缓存路径、artifact 访问器、运行参数都从 `PipelineContext` 读取。

### 2.5 `--debug` 不进入配置

`--debug` 是 CLI 运行参数,用于 refine 开发期人工审核第一段产物。它不写入配置文件,不参与配置 hash,不进入业务 schema。

配置文件只描述稳定生成语义。一次性调试行为由 CLI runtime options 表达,不写入 `AppConfig`。

---

## 3. Module Details

### 3.1 `core/schemas/`

所有跨模块共享 dataclass 集中在 `core/schemas/`:

```text
core/schemas/
├── __init__.py
├── audio.py
├── visual.py
└── merge.py
```

其他模块应从 `core.schemas` 引用共享类型,禁止在业务模块重新定义副本。

`audio.py` 定义:

- `AudioExtractResult`
- `WordTimestamp`
- `TranscriptSegment`
- `Transcript`
- `SegmentMarker`
- `SegmentList`
- `RefinedSegment`
- `RefinedTranscript`

`visual.py` 定义:

- `SampledFrame`
- `VisualSampleIndex`
- `VisualSegment`
- `VisualSegmentList`
- `VisualJudgement`
- `VisualSelection`
- `VisualDescription`

`merge.py` 定义:

- `VisualSlot`
- `ContentBlock`
- `Chapter`
- `Outline`

schema 规则:

- 所有公开字段有类型标注
- 跨模块字段不用 `Any`
- 默认 `frozen=True`
- 字段名与管线文档保持一致
- 时间戳一律 `float` 秒数,精度毫秒
- 内部 marker 可以出现在文本字段中,但渲染由 assemble 处理

### 3.2 `artifacts.py`

`artifacts.py` 提供跨管线产物访问接口,不生成产物。

`AudioArtifacts` 稳定接口见 `docs/audio-pipeline.md` §5,包括:

```python
class AudioArtifacts:
    def get_extract(self) -> AudioExtractResult: ...
    def get_transcript(self) -> Transcript: ...
    def get_segments(self) -> SegmentList: ...
    def get_refined(self) -> RefinedTranscript: ...
    def get_text_at(
        self,
        start: float,
        end: float,
        prefer_raw: bool = False,
        strict: bool = False,
        strip_refs: bool = True,
    ) -> str: ...
    def get_segment_at(self, timestamp: float) -> RefinedSegment | None: ...
    def get_duration(self) -> float: ...
    def get_language(self) -> str: ...
    def is_complete(self) -> bool: ...
```

`VisualArtifacts` 稳定接口见 `docs/visual-pipeline.md` §5,包括:

```python
class VisualArtifacts:
    def get_samples(self) -> VisualSampleIndex: ...
    def get_segments(self) -> VisualSegmentList: ...
    def get_judgements(self) -> list[VisualJudgement]: ...
    def get_selections(self) -> list[VisualSelection]: ...
    def get_descriptions(self) -> list[VisualDescription]: ...
    def is_complete(self) -> bool: ...
```

实现规则:

- getter 惰性加载并缓存到实例字段
- 路径全部经 `core/paths.py`
- 产物缺失时抛 `CacheError` 并说明可能未运行的 stage
- `is_complete()` 只做轻量存在性检查,不做 schema 校验
- 不 import `audio_pipeline/` 或 `visual_pipeline/`

### 3.3 `paths.py`

`paths.py` 是缓存路径和最终输出路径的唯一来源。建议提供不可变路径对象:

```python
@dataclass(frozen=True)
class PipelinePaths:
    source_path: Path
    cache_dir: Path
    output_dir: Path
    run_dir: Path
    audio_dir: Path
    visual_dir: Path
    visual_frames_dir: Path
    refined_dir: Path
    sections_dir: Path
    audio_wav: Path
    audio_extract_json: Path
    visual_sample_json: Path
    visual_segments_json: Path
    visual_judgements_json: Path
    visual_selections_json: Path
    visual_descriptions_json: Path
    transcript_raw_json: Path
    segments_json: Path
    refined_transcript_json: Path
    content_blocks_json: Path
    outline_json: Path
    cache_note_md: Path
    output_note_md: Path
```

建议工厂函数:

```python
def build_paths(source_path: Path, cache_dir: Path, output_dir: Path, input_hash: str) -> PipelinePaths: ...
```

`paths.py` 只计算路径,不创建目录、不读写文件。

路径语义:

- `source_path` 是 CLI 输入的本地视频或音频路径。schema、frontmatter、URL 模板和日志中需要表达原始输入时统一使用这个名字。
- `run_dir == cache_dir / input_hash`,只存中间产物和 debug copy。
- `cache_note_md == cache/{input_hash}/note.md`,是 assemble 生成的调试副本或缓存副本,不是最终用户产物。
- `output_note_md == output_dir / "note.md"`,是最终用户产物。
- visual 帧文件存放在 `visual_frames_dir == cache/{input_hash}/visual/frames/`。
- visual schema 中的 `source_path` 推荐保存为相对 `visual_frames_dir` 的路径,例如 `000123.000.png` 或子目录下的 `segment-001/000123.000.png`。需要读文件时由 `paths.py` 提供的 resolver 与 `visual_frames_dir` 拼成绝对路径。
- Markdown 图片引用需要相对于最终 `output_note_md` 计算展示路径;业务 stage 不手写相对路径。

### 3.4 `timestamps.py`

内部时间戳统一为 `float` 秒数。人类可读格式只在 CLI 展示和 assemble 阶段生成。

建议接口:

```python
def normalize_seconds(seconds: float) -> float: ...
def format_hms(seconds: float) -> str: ...
def format_mmss(seconds: float) -> str: ...
def render_timestamp(seconds: float, template: str) -> str: ...
def parse_ts_marker(marker: str) -> float: ...
```

支持内部 marker:

```text
[[TS:123.456]]
```

业务模块禁止手写 `mm:ss` / `hh:mm:ss` 格式化。

### 3.5 `slugs.py`

`slugs.py` 是 Markdown anchor 生成唯一入口。

建议接口:

```python
def make_chapter_anchor(chapter_id: int, title: str) -> str: ...
```

语义与 `docs/merge.md` §3.4 保持一致:

- CJK 字符经 `unicodedata.normalize("NFKC")` 后保留
- 空白和 markdown 不安全字符替换为 `-`
- 强制 `chapter-{id}-` 前缀
- 连续 `-` collapse
- 首尾 strip

### 3.6 `pipeline.py`

`pipeline.py` 定义 stage 返回契约。

```python
@dataclass(frozen=True)
class StageOutput:
    stage_name: str
    output_paths: list[Path]
    cache_hit: bool
    content_hash: str
    metadata: dict[str, str | int | float | bool | None]
```

`StageOutput` 不是业务产物。业务产物落盘后由 artifacts 或后续 stage 读取。

`metadata` 可记录:

- `cache_key`
- `config_hash`
- `prompt_hash`
- `profile_name`
- `duration_seconds`
- `item_count`

这些字段不进入 schema。

### 3.7 `cache.py`

`cache.py` 提供缓存相关基础函数:

```python
def atomic_write_json(path: Path, payload: object) -> None: ...
def atomic_write_text(path: Path, text: str) -> None: ...
def atomic_write_bytes(path: Path, content: bytes) -> None: ...
def hash_file(path: Path) -> str: ...
def hash_json(payload: object) -> str: ...
def hash_prompt_template(path: Path) -> str: ...
```

`atomic_write_*` 实现要求:

1. 写同目录临时文件
2. flush
3. fsync
4. `os.replace` 原子替换
5. 必要时 fsync 父目录

JSON 写入要求:

- UTF-8
- `ensure_ascii=False`
- key 排序
- 固定缩进
- 结尾换行

`hash_prompt_template()` 做最小归一化:

- 去除 Jinja 注释 `{# ... #}`
- collapse 连续空白为单空格
- strip 首尾空白
- 对归一化文本做 SHA256

### 3.8 `config.py`

`config.py` 使用 pydantic 定义完整配置 schema,启动时一次性加载并校验。

LLM 任务名是封闭字符串集合:

```python
LLM_TASK_NAMES = {
    "segment",
    "refine",
    "outline",
    "section",
    "slide_judge",
    "slide_describe",
}
```

配置中的 `tasks.*` key 必须属于 `LLM_TASK_NAMES`,value 必须指向存在的 LLM profile。未知 task 直接抛 `ConfigError`。

配置不包含一次性运行状态或模式启停字段。多模模式由 CLI `--mm` 决定;refine 调试由 CLI `--debug` 决定。

### 3.9 `context.py`

`PipelineContext` 是 stage 的唯一运行时入口。

建议结构:

```python
@dataclass(frozen=True)
class ArtifactBundle:
    audio: AudioArtifacts
    visual: VisualArtifacts | None = None

@dataclass(frozen=True)
class PipelineContext:
    source_path: Path
    input_hash: str
    config: AppConfig
    paths: PipelinePaths
    artifacts: ArtifactBundle
    debug: bool = False
    no_cache: bool = False
```

字段语义:

- `source_path` 是本次运行的本地视频或音频输入路径,与 `PipelinePaths.source_path` 相同。
- `artifacts` 的类型必须是 `ArtifactBundle`,不是裸 `AudioArtifacts`、裸 `VisualArtifacts` 或任意 dict。
- 音频产物一律通过 `ctx.artifacts.audio` 访问。
- 视觉产物一律通过 `ctx.artifacts.visual` 访问;纯音频模式下该字段为 `None`。
- 合并阶段读取 `ctx.artifacts.audio` 和可选的 `ctx.artifacts.visual`,不直接读取上游管线内部模块。

`debug` 来自 CLI `--debug`,不是配置字段。

`PipelineContext` 不持有 LLM client、ASR model、ffmpeg process 等重资源。

### 3.10 `exceptions.py`

项目异常集中定义:

```python
class LVNotesError(Exception): ...
class ConfigError(LVNotesError): ...
class CacheError(LVNotesError): ...
class LLMError(LVNotesError): ...
class AuthError(LLMError): ...
class RateLimitError(LLMError): ...
class ContextLengthError(LLMError): ...
class TransportError(LLMError): ...
class ASRError(LVNotesError): ...
class MediaError(LVNotesError): ...
```

第三方异常在模块边界包装为项目异常。内部不变量违反可直接 `AssertionError`。

### 3.11 `logging.py`

建议接口:

```python
def configure_logging(debug: bool, log_file: Path | None = None) -> None: ...
```

规则:

- 非 CLI 模块不用 `print`
- 模块内使用 `logging.getLogger(__name__)`
- 不记录 API key、完整 token、长 prompt、长转录正文到控制台
- 日志包含 stage 名和 input hash,便于 grep

---

## 4. Stage Contract

stage 推荐流程:

1. 从 `ctx` 和 artifacts 读取输入
2. 计算缓存键
3. 缓存命中则返回 `StageOutput(cache_hit=True, ...)`
4. 执行业务逻辑
5. 用 `atomic_write_*` 写出产物
6. 返回 `StageOutput(cache_hit=False, ...)`

示意:

```python
def run(ctx: PipelineContext) -> StageOutput:
    transcript = ctx.artifacts.audio.get_transcript()
    output_path = ctx.paths.segments_json

    if is_cache_hit(output_path):
        return StageOutput(
            stage_name="segment",
            output_paths=[output_path],
            cache_hit=True,
            content_hash=hash_file(output_path),
            metadata={},
        )

    segments = build_segments(transcript, ctx.config.audio_pipeline.segment)
    atomic_write_json(output_path, to_jsonable(segments))

    return StageOutput(
        stage_name="segment",
        output_paths=[output_path],
        cache_hit=False,
        content_hash=hash_file(output_path),
        metadata={"segment_count": len(segments.markers)},
    )
```

示例中的 `is_cache_hit` / `to_jsonable` 只是说明流程,不是要求新增抽象。

---

## 5. Artifacts Contract

允许跨管线依赖:

```text
visual_pipeline/describe.py -> core.artifacts.AudioArtifacts
merge/unify.py              -> core.artifacts.AudioArtifacts
merge/unify.py              -> core.artifacts.VisualArtifacts
```

禁止依赖:

```text
visual_pipeline/describe.py -> audio_pipeline/refine.py
merge/unify.py              -> audio_pipeline/*
merge/unify.py              -> visual_pipeline/*
```

Artifacts 方法表达业务含义,不是文件名。

正确:

```python
refined = ctx.artifacts.audio.get_refined()
```

错误:

```python
refined = read_json(ctx.paths.refined_transcript_json)
```

例外:同一模块内部 stage 间可以按该模块文档通过 `ctx.paths` 读写自己的中间产物,但跨管线消费上游必须通过 Artifacts。

---

## 6. Path Contract

路径命名和归属是稳定规格:

| 名称 | 语义 |
|---|---|
| `source_path` | 本地视频或音频输入路径 |
| `cache_dir` | 缓存根目录 |
| `run_dir` | `cache/{input_hash}` |
| `output_dir` | 最终用户产物目录 |
| `cache_note_md` | `cache/{input_hash}/note.md`,调试或缓存副本 |
| `output_note_md` | `output_dir/note.md`,最终用户产物 |
| `visual_frames_dir` | `cache/{input_hash}/visual/frames/` |

目录布局:

```text
cache/{input_hash}/
├── audio/
│   ├── audio.wav
│   └── extract.json
├── visual/
│   ├── frames/
│   ├── sample.json
│   ├── segments.json
│   ├── judgements.json
│   ├── selections.json
│   └── descriptions.json
├── transcript_raw.json
├── segments.json
├── refined/
│   └── {seg_id}.json
├── refined_transcript.json
├── content_blocks.json
├── outline.json
├── sections/
│   └── {chapter_id:03d}.md
└── note.md

output_dir/
└── note.md
```

规则:

- `output_dir/note.md` 是唯一最终用户产物。
- `cache/{input_hash}/note.md` 只作为中间产物或 debug copy 存在。
- `source_path` 统一表示原始输入,不区分视频和音频。配置、schema 和上下文中不要使用带媒体类型假设的字段名。
- `image_source_path` 在 visual 与 merge schema 中推荐保存为相对 `cache/{input_hash}/visual/frames/` 的路径。
- `paths.py` 负责把视觉产物中的相对 `image_source_path` 解析到真实帧文件,以及把帧文件转换为最终 Markdown 可用的相对路径。

---

## 7. Config Contract

配置文件只表达稳定生成语义:

- cache / output 目录
- LLM profiles
- task → profile 映射
- ASR 参数
- audio pipeline 参数
- visual pipeline 参数
- merge 参数

模式由 CLI 决定:

- `--mm` 是多模唯一启用来源
- 输入是音频文件时强制纯音频模式
- 配置不提供 audio / multimodal enabled 开关

调试由 CLI 决定:

- `--debug` 可传入 `PipelineContext.debug`
- `--debug` 不进入配置
- `--debug` 不参与 cache key

---

## 8. Module Layout

```text
lvnotes/core/
├── schemas/
│   ├── __init__.py
│   ├── audio.py
│   ├── visual.py
│   └── merge.py
├── artifacts.py
├── paths.py
├── timestamps.py
├── slugs.py
├── pipeline.py
├── cache.py
├── config.py
├── context.py
├── exceptions.py
├── constants.py
└── logging.py
```

`constants.py` 只放真正跨模块常量。业务阈值和默认模型不放 constants,应进入配置。

---

## 9. Dependencies

`core/` 允许依赖:

- Python 标准库
- `pydantic` / `pydantic-settings`
- `core.*` 子模块

`core/` 禁止依赖:

- `audio_pipeline/`
- `visual_pipeline/`
- `merge/`
- `cli/`
- `llm/`
- `asr/`
- `media/`
- `openai`
- `anthropic`
- `faster_whisper`

---

## 10. Implementation Order

建议顺序:

1. `exceptions.py`
2. `schemas/`
3. `paths.py`
4. `cache.py`
5. `timestamps.py`
6. `slugs.py`
7. `config.py`
8. `artifacts.py`
9. `context.py`
10. `pipeline.py`
11. `logging.py`

每完成一个模块立即 smoke-test import:

```bash
python -c "import lvnotes.core.cache"
```

验收标准:

1. `core/` 不 import 业务模块
2. 公开函数和 dataclass 字段有类型标注
3. 所有跨模块 schema 在 `core/schemas/`
4. Artifacts 是跨管线产物访问唯一入口
5. 原子写入、prompt hash、路径、时间戳、slug 都有唯一入口
6. 未知任务名会触发 `ConfigError`
7. `--debug` 不进入配置、不参与缓存键
