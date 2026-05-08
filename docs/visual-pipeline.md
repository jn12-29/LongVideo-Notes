# Visual Pipeline

多模管线设计占位文档。本管线把视频画面转成可与音频语义段对齐的视觉描述，对外通过 `VisualArtifacts` 暴露产物。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`、`docs/audio-pipeline.md`。

文档结构：Overview、Design Considerations、Stages、Schema、Downstream Interfaces、Module Layout、Dependencies、Implementation Order。

---

## 1. Overview

多模管线由 5 个 stage 顺序执行，仅在多模模式下启用。第一版可以先创建目录和占位接口，不要求实现完整视觉处理。

| Stage | 名称 | 主要工具 | 主要产物 |
|---|---|---|---|
| 1 | sample | ffmpeg（经 `media/`） | 1fps 采样帧 |
| 2 | cluster | pHash + 直方图 | 视觉段（连续渐变合并） |
| 3 | judge | 弱 VLM（经 `llm/`） | 每段 medium / is_meaningful / evolution / richest_frame |
| 4 | select | 拉普拉斯方差 | 每段 1 张代表帧（无意义段丢弃） |
| 5 | describe | 强 VLM + 转录（经 `llm/` + `AudioArtifacts`） | 每个代表帧的详细图文描述 |

**对外产物**集中在 `VisualArtifacts`（`core/artifacts.py`）。合并阶段只通过这个接口读取，不直接 import `visual_pipeline/` 内部，也不直接读缓存文件。

**本管线不依赖 `audio_pipeline/` 内部模块**。stage 5 需要音频管线 refine 产物时，只能通过 `AudioArtifacts.get_text_at(..., strip_refs=True)` 读取指定时间区间的讲解文本。

---

## 2. Design Considerations

### 2.1 第一版可暂不实现

多模能力不是音频端到端闭环的前置条件。第一阶段可以只实现音频管线和合并阶段，保留 `visual_pipeline/` 目录、配置项、`VisualArtifacts` 接口占位即可。

### 2.2 视觉段以画面稳定区间为单位

采样帧不直接进入 VLM。先用 pHash + 直方图把连续相似或渐变帧合并成视觉段，减少 VLM 调用次数，也避免对每秒画面重复描述。

### 2.3 judge 与 describe 分层

stage 3 使用弱 VLM 判断画面是否有意义、介质类型和最有信息量的帧；stage 5 使用强 VLM 生成详细描述。这样把便宜的过滤判断和贵的详细理解分开。

### 2.4 describe 依赖音频 refine 产物

画面理解需要结合该时间段讲师讲解内容。stage 5 启动前必须满足 `AudioArtifacts.is_complete() == True`，等待逻辑由 CLI 调度层处理，本管线只消费已经完成的 `AudioArtifacts`。

### 2.5 VLM 输入不包含内部 marker

`AudioArtifacts.get_text_at()` 默认 `strip_refs=True`，会剥离 `[[REF:N]]`。VLM 不理解项目内部 marker，describe 阶段应使用默认值，除非有明确调试需求。

---

## 3. Stages

每个 stage 用统一子结构：**职责 / Input / Output / 实现要点 / 配置项 / 缓存键 / 错误处理**。

每个 stage 的实现文件（`sample.py` / `cluster.py` / `judge.py` / `select.py` / `describe.py`）暴露统一签名：

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

### 3.1 Stage 1: sample

**职责**：从输入视频按配置 fps 抽取采样帧。

**Input**：输入视频路径（来自 `ctx.input_path`）。

**Output**：采样帧目录 + `VisualSampleIndex`。落盘到 `cache/{input_hash}/visual/frames/` 与 `cache/{input_hash}/visual/sample.json`。

**实现要点**：
- 走 `media/video.py` 的抽帧函数，禁止直接 `subprocess.run`
- 输入是音频文件或未显式传 `--mm` 时本 stage 不运行;视频输入显式传 `--mm` 时由 CLI 调度层启动多模管线
- 帧文件命名必须稳定，包含时间戳或帧序号，便于断点续跑与人工检查

**配置项**（`visual_pipeline.sample.*`）：
- `fps: float` —— 默认 1

**缓存键**：`hash(input_file_bytes) + hash(sample 配置) + "visual_sample"`。

**错误处理**：
- ffmpeg 调用失败：`media/` 包装为 `MediaError` 上抛
- 输入文件无视频流：抛 `MediaError`

### 3.2 Stage 2: cluster

**职责**：把相邻采样帧聚合成视觉段。

**Input**：`VisualSampleIndex`。

**Output**：`VisualSegmentList`，落盘 `cache/{input_hash}/visual/segments.json`。

**实现要点**：
- 使用 pHash 距离做主判断，直方图差异做辅助判断
- 使用双阈值 + 跟段首累积比对，避免缓慢渐变被切成大量碎段
- 输出只描述时间边界与候选帧，不调用 VLM

**配置项**（`visual_pipeline.cluster.*`）：
- `phash_low_threshold: int`
- `phash_high_threshold: int`

**缓存键**：`hash(VisualSampleIndex) + hash(cluster 配置) + "visual_cluster"`。

**错误处理**：不变量违反（时间倒序、空视觉段）→ `AssertionError`。

### 3.3 Stage 3: judge

**职责**：用弱 VLM 判断每个视觉段是否值得保留，并选择信息量最高的候选帧。

**Input**：`VisualSegmentList` + 每段首 / 中 / 末帧。

**Output**：`VisualJudgementList`，落盘 `cache/{input_hash}/visual/judgements.json`。

**实现要点**：
- 每段最多传首 / 中 / 末三帧给弱 VLM
- 输出 `medium`、`is_meaningful`、`evolution`、`richest_frame`
- LLM JSON 解析 + 1 次修复重试 + schema 校验走 `llm.complete_json()` helper

**配置项**：使用 `tasks.slide_judge` 映射到的 LLM profile。

**缓存键**：`hash(VisualSegmentList) + hash(judge 配置) + hash(LLM profile) + hash_prompt_template("prompts/judge.jinja") + "visual_judge"`。

**错误处理**：LLM 输出违反 schema 或业务不变量 → `LLMError`。

### 3.4 Stage 4: select

**职责**：为每个有意义视觉段选择 1 张代表帧。

**Input**：`VisualJudgementList` + 采样帧。

**Output**：`VisualSelectionList`，落盘 `cache/{input_hash}/visual/selections.json`。

**实现要点**：
- `is_meaningful=False` 的段不产出代表帧
- 优先使用 judge 给出的 `richest_frame`
- 必要时用拉普拉斯方差在候选帧中选择更清晰的一张

**配置项**：第一版可无。

**缓存键**：`hash(VisualJudgementList) + hash(select 配置) + "visual_select"`。

**错误处理**：代表帧路径不存在 → `CacheError`。

### 3.5 Stage 5: describe

**职责**：用强 VLM 为代表帧生成结合讲解文本的详细视觉描述。

**Input**：`VisualSelectionList` + `AudioArtifacts.get_text_at(start, end, strip_refs=True)`。

**Output**：`VisualDescriptionList`，落盘 `cache/{input_hash}/visual/descriptions.json`。

**实现要点**：
- 启动前由 CLI 调度层保证 `AudioArtifacts.is_complete() == True`
- 对每个代表帧，取视觉段时间区间对应的讲解文本作为 VLM 文本上下文
- 不直接读取 `refined_transcript.json`，只通过 `AudioArtifacts`
- LLM JSON 解析 + 1 次修复重试 + schema 校验走 `llm.complete_json()` helper

**配置项**：使用 `tasks.slide_describe` 映射到的 LLM profile。

**缓存键**：`hash(VisualSelectionList) + hash(相关 AudioArtifacts 文本内容) + hash(describe 配置) + hash(LLM profile) + hash_prompt_template("prompts/describe.jinja") + "visual_describe"`。

**错误处理**：音频产物未完成 → `CacheError`；VLM 失败 → `LLMError`。

---

## 4. Schema

多模管线相关 dataclass 集中定义在 `core/schemas/visual.py`，通过 `core/schemas/__init__.py` re-export。字段只放内容，不放配置元信息。

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class SampledFrame:
    id: int
    timestamp: float
    path: Path

@dataclass(frozen=True)
class VisualSampleIndex:
    frames: list[SampledFrame]
    duration: float

@dataclass(frozen=True)
class VisualSegment:
    id: int
    start: float
    end: float
    frame_ids: list[int]

@dataclass(frozen=True)
class VisualSegmentList:
    segments: list[VisualSegment]

@dataclass(frozen=True)
class VisualJudgement:
    segment_id: int
    medium: str
    is_meaningful: bool
    evolution: str
    richest_frame_id: int | None

@dataclass(frozen=True)
class VisualSelection:
    segment_id: int
    frame_id: int
    frame_path: Path
    start: float
    end: float
    medium: str

@dataclass(frozen=True)
class VisualDescription:
    segment_id: int
    frame_path: Path
    start: float
    end: float
    medium: str
    description: str
```

### 不变量

1. 所有 `id` 字段在各自 namespace 内 0-based 严格递增
2. 所有 `start < end`
3. 所有时间戳使用 `float` 秒数、精度毫秒
4. `VisualJudgement.segment_id`、`VisualSelection.segment_id`、`VisualDescription.segment_id` 必须对应存在的 `VisualSegment.id`
5. `VisualSelection.frame_id` 必须对应存在的 `SampledFrame.id`

不变量违反 → `AssertionError`，不 catch、不自动修复。

---

## 5. Downstream Interfaces — `VisualArtifacts`

`VisualArtifacts` 是多模管线对合并阶段的唯一稳定 API。合并阶段不允许 import `visual_pipeline/` 内部模块。

```python
class VisualArtifacts:
    def __init__(self, input_hash: str, paths: Paths) -> None: ...

    def get_samples(self) -> VisualSampleIndex: ...
    def get_segments(self) -> VisualSegmentList: ...
    def get_judgements(self) -> list[VisualJudgement]: ...
    def get_selections(self) -> list[VisualSelection]: ...
    def get_descriptions(self) -> list[VisualDescription]: ...
    def is_complete(self) -> bool: ...
```

### 实现要点

- getter 惰性加载并缓存到实例字段
- 路径全部经 `core/paths.py`
- 产物文件不存在时抛 `CacheError("VisualArtifacts.get_xxx: <path> not found, stage 'xxx' may not have run")`
- `is_complete()` 仅检查 `descriptions.json` 是否存在，不做 schema 校验

---

## 6. Module Layout

```text
lvnotes/visual_pipeline/
├── __init__.py
├── sample.py
├── cluster.py
├── judge.py
├── select.py
├── describe.py
└── prompts/
    ├── judge.jinja
    └── describe.jinja
```

### Import 规则速查

| 来源 | 允许？ |
|---|---|
| `core/`（schemas、artifacts、paths、timestamps、pipeline、cache、config、context、logging、exceptions、constants） | ✅ |
| `media/` | ✅（仅 sample） |
| `llm/` | ✅（仅 judge、describe） |
| `audio_pipeline/` 内部 | ❌（必须经 `core/artifacts.AudioArtifacts`） |
| `merge/` 内部 | ❌ |
| `visual_pipeline/` 内的其他 stage 文件 | ❌（stage 间通过 `ctx.paths` 文件 IO 解耦） |
| `openai` / `anthropic` 等 SDK 直接 import | ❌（必须经 `llm/`） |
| `subprocess` 调 ffmpeg | ❌（必须经 `media/`） |

---

## 7. Dependencies

### 项目内

- `core/`：`schemas`、`artifacts`、`paths`、`timestamps`、`pipeline`、`cache`、`config`、`context`、`logging`、`exceptions`
- `media/`：sample 用
- `llm/`：judge、describe 用

### 预计涉及的外部库

- `imagehash`：pHash 计算
- `opencv-python`：直方图、清晰度评分
- `Pillow`：图像处理
- `jinja2`：渲染 prompt 模板

具体依赖清单与版本以后续 `pyproject.toml` 为准。

---

## 8. Implementation Order

按 `docs/overview.md` §7 的实现优先级，多模管线在音频端到端闭环完成后再实现。

1. 创建 `visual_pipeline/` 目录、prompt 占位、`VisualArtifacts` 接口骨架
2. 实现 sample
3. 实现 cluster
4. 实现 judge
5. 实现 select
6. 实现 describe
7. 升级 merge/unify 与 merge/section 支持视觉信息

每个 stage 独立验收：真实视频输入跑通、缓存命中、错误路径覆盖、类型检查通过、独立 CLI 调用可用。
