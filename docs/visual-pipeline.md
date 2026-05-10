# Visual Pipeline

多模管线把视频画面转成可与音频语义段对齐的视觉描述，对外通过 `VisualArtifacts` 暴露产物。写代码前必读本文档以及 `docs/core.md`、`docs/cli.md`、`docs/merge.md`。

## Overview

多模管线由 5 个主 stage 组成，仅在多模模式下启用。`sample -> filter -> semantic_filter` 可与音频管线并行；`align -> describe` 必须等待音频 refine 完成。

| Stage | 名称 | 主要工具 | 主要产物 |
|---|---|---|---|
| 1 | sample | ffmpeg（经 `media/`） | `raw_frames/` + `sample.json` |
| 2 | filter | pHash + 直方图 | `filter_variants/` + `filter_frames/` + `filtered_sample.json` |
| 3 | semantic_filter | 弱 VLM（经 `llm/`） | `semantic_frames/` + `semantic_sample.json` + `semantic_judgements.json` |
| 4 | align | 纯逻辑 + `AudioArtifacts` | `alignments.json` |
| 5 | describe | 强 VLM + refined text，并发 | `descriptions.json` |

`sample` 保存完整原始抽帧，`filter` 默认不裁剪，使用完整帧做本地重复过滤。`semantic_filter` 用弱 VLM 筛掉无语义图，后续 `align` 和 `describe` 只使用 `semantic_frames/`。

## Stage Contract

每个 stage 的实现文件暴露统一签名：

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

Stage 之间不互相 import。需要读上游视觉产物时通过 `ctx.paths` 和本模块 `_common.py` 的 reader；需要读音频产物时通过 `ctx.artifacts.audio`。

### Stage 1: sample

**职责**：从输入视频按配置 fps 抽取采样帧。

**Input**：输入视频路径（`ctx.source_path`）。

**Output**：`cache/{input_hash}/visual/raw_frames/` 与 `cache/{input_hash}/visual/sample.json`。

**实现要点**：

- 走 `media/video.py` 的抽帧函数，禁止直接调用 ffmpeg。
- 帧文件命名稳定，重新抽帧时清理当前命名模式下的旧帧。
- `sample.json` 中 `SampledFrame.image_source_path` 相对 `visual/raw_frames/`。

**配置项**（`visual_pipeline.sample.*`）：

- `fps: float`，默认 `1`。

**缓存键**：`visual_sample` 使用输入文件 hash 与 sample 配置 hash。

### Stage 2: filter

**职责**：本地过滤连续重复帧和全局近重复帧，减少进入弱 VLM 的候选图。

**Input**：`sample.json` 与 `visual/raw_frames/`。

**Output**：`cache/{input_hash}/visual/filter_variants/`、`cache/{input_hash}/visual/filter_frames/` 与 `cache/{input_hash}/visual/filtered_sample.json`。

**实现要点**：

- 默认 `crop: null`，对完整帧计算 pHash 与灰度直方图差异。
- 如果配置了 `crop`，只在计算相似度时裁剪，保存到 `filter_frames/` 的仍是完整原图。
- 每个 filter variant 先生成 adjacent-only 结果，再用更严格的 pHash、灰度直方图、低分辨率像素差阈值过滤非相邻近重复帧。
- 仅在显式设置 `max_static_seconds` 为正数时，长时间静态内容按该间隔保底保留一张；保底帧仍会经过全局近重复过滤。
- 每个 variant 写入 `visual/filter_variants/{slug}/adjacent_frames/`、`adjacent_sample.json`、`frames/` 与 `filtered_sample.json`。
- `active_variant` 的全局去重结果同步到 `visual/filter_frames/` 与 `visual/filtered_sample.json`，后续 semantic filter 只消费稳定产物。
- `filtered_sample.json` 复用 `VisualSampleIndex`，保留原始 `SampledFrame.id` 和 timestamp，稳定产物中的 `image_source_path` 相对 `visual/filter_frames/`。

**配置项**（`visual_pipeline.filter.*`）：

- `phash_threshold: int`，默认 `8`。
- `histogram_threshold: float`，默认 `0.12`。
- `duplicate_phash_threshold: int`，默认 `2`。
- `duplicate_histogram_threshold: float`，默认 `0.03`。
- `duplicate_pixel_threshold: float`，默认 `0.02`。
- `max_static_seconds: float | null`，默认 `null`，表示不启用长静态保底。
- `crop: null | {left, top, right, bottom}`，默认 `null`。
- `active_variant: str`，默认 `default`。
- `variants_file: path | null`，相对主配置文件目录解析；未配置时使用顶层 filter 参数生成 `default` variant。

`filter_variants.yaml` 格式：

```yaml
variants:
  - name: default
    phash_threshold: 8
    histogram_threshold: 0.12
    duplicate_phash_threshold: 2
    duplicate_histogram_threshold: 0.03
    duplicate_pixel_threshold: 0.02
    max_static_seconds: null
    crop: null
```

**缓存键**：`visual_filter` 使用 `sample.json` hash、filter 配置 hash 与 variants 内容 hash；`variants_file` 路径本身不进入 filter 配置 hash。cache manifest 只记录稳定产物；`filter_variants/` 用于调试比较，不是下游稳定合同。`--no-cache` 会重建 variants 与稳定产物。

### Stage 3: semantic_filter

**职责**：用弱 VLM 判断每张过滤后图片是否有语义价值，并筛掉讲者、黑屏、UI、空白或无笔记价值的画面。

**Input**：`filtered_sample.json` 与 `visual/filter_frames/`。

**Output**：`cache/{input_hash}/visual/semantic_frames/`、`cache/{input_hash}/visual/semantic_sample.json` 与 `cache/{input_hash}/visual/semantic_judgements.json`。

**实现要点**：

- 对每张 `filtered_sample.json` 中的图片独立判断。
- `is_meaningful=true` 仅用于 PPT 正文页、章节标题页、公式、图表、表格、流程图、代码、demo、黑板/白板或其他有报告语义的画面。
- `is_meaningful=false` 用于纯讲者镜头、黑屏、大面积空白、会议 UI、播放器 UI、转场、水印或无笔记价值画面。
- meaningful 图片复制到 `visual/semantic_frames/`。
- `semantic_sample.json` 复用 `VisualSampleIndex`，`image_source_path` 相对 `visual/semantic_frames/`。

**缓存键**：`visual_semantic_filter` 使用 `filtered_sample.json` hash、弱 VLM profile hash 与 prompt hash。

### Stage 4: align

**职责**：把有语义图片按 timestamp 对齐到 refined text segments。文本 segment 是多模笔记结构的权威边界。

**Input**：`semantic_sample.json`、`semantic_judgements.json` 与 `ctx.artifacts.audio.get_refined()`。

**Output**：`cache/{input_hash}/visual/alignments.json`。

**实现要点**：

- `segment.start <= frame.timestamp < segment.end` 时分配到该文本 segment。
- 不在任何 segment 内时分配到最近的 refined segment。
- 一个文本 segment 内可以保留多张图，按 timestamp 排序。

**缓存键**：`visual_align` 使用 semantic sample hash、semantic judgements hash 与 refined transcript hash。

### Stage 5: describe

**职责**：用强 VLM 为每张 aligned semantic frame 生成结合对应 refined text segment 的详细视觉描述。

**Input**：`alignments.json`、`visual/semantic_frames/` 与 `ctx.artifacts.audio.get_refined()`。

**Output**：`cache/{input_hash}/visual/descriptions.json`。

**实现要点**：

- CLI 调度层保证启动前 `AudioArtifacts.is_complete() == True`，且已运行 `align`。
- 图片从 `visual/semantic_frames/` 解析。
- `VisualDescription.frame_id`、`image_source_path`、`medium` 来自 `VisualAlignment`。
- `VisualDescription.start/end` 使用对应 refined text segment 的 `start/end`。
- LLM 调用通过 `for_task(ctx.config, "slide_describe")` 和 `complete_json(...)`。
- 每张 aligned semantic frame 独立调用强 VLM，并发数由 `visual_pipeline.describe.concurrent_calls` 控制。

**配置项**（`visual_pipeline.describe.*`）：

- `concurrent_calls: int`，默认 `5`。

## Schema

多模 schema 定义在 `core/schemas/visual.py`，通过 `core/schemas/__init__.py` re-export。

`VisualSampleIndex` 同时用于 `sample.json`、`filtered_sample.json` 与 `semantic_sample.json`：

- `sample.json` 的 `image_source_path` 相对 `visual/raw_frames/`。
- `filtered_sample.json` 的 `image_source_path` 相对 `visual/filter_frames/`。
- `semantic_sample.json` 的 `image_source_path` 相对 `visual/semantic_frames/`。

`VisualAlignment` 与 `VisualDescription` 的 `image_source_path` 相对 `visual/semantic_frames/`。

关键不变量：

1. `VisualSemanticJudgement.frame_id`、`VisualAlignment.frame_id`、`VisualDescription.frame_id` 使用同一个 `SampledFrame.id` namespace。
2. `filtered_sample.json` 与 `semantic_sample.json` 保留 raw sample 的原始 frame id，不重新编号。
3. 所有 `image_source_path` 必须是相对路径，不得是绝对路径或通过 `..` 逃逸对应帧目录。
4. `descriptions.json` 是 merge 阶段消费视觉信息的完整产物。

## VisualArtifacts

`VisualArtifacts` 是多模管线对合并阶段的稳定 API。合并阶段不允许 import `visual_pipeline/` 内部模块。

```python
class VisualArtifacts:
    def get_samples(self) -> VisualSampleIndex: ...
    def get_filtered_samples(self) -> VisualSampleIndex: ...
    def get_semantic_samples(self) -> VisualSampleIndex: ...
    def get_semantic_judgements(self) -> VisualSemanticJudgementList: ...
    def get_alignments(self) -> list[VisualAlignment]: ...
    def get_descriptions(self) -> VisualDescriptionList: ...
    def is_complete(self) -> bool: ...
```

`is_complete()` 仅检查 `descriptions.json` 是否存在。

## Module Layout

```text
lvnotes/visual_pipeline/
├── __init__.py
├── sample.py
├── filter.py
├── semantic_filter.py
├── align.py
├── describe.py
└── prompts/
    ├── semantic_filter.jinja
    └── describe.jinja
```

## Validation

多模管线验收：

- `lvnotes sample <video> --mm` 生成 `raw_frames/` 与 `sample.json`。
- `lvnotes filter <video> --mm` 生成 `filter_variants/`、`filter_frames/` 与 `filtered_sample.json`。
- `lvnotes semantic-filter <video> --mm` 生成 `semantic_frames/`、`semantic_sample.json` 与 `semantic_judgements.json`。
- `lvnotes align <video> --mm` 在 refined audio 完成后生成 `alignments.json`。
- `lvnotes run <video> --mm` 端到端生成带视觉图片和描述的 Markdown。
