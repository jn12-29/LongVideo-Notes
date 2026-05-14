# Visual Pipeline

多模管线把视频画面转成可与音频语义段对齐的视觉描述，对外通过 `VisualArtifacts` 暴露产物。写代码前必读本文档以及 `docs/core.md`、`docs/cli.md`、`docs/merge.md`。

## Overview

多模管线由 4 个主 stage 组成，仅在多模模式下启用。`filter -> semantic_filter` 可与音频管线并行；`align -> describe` 必须等待音频 refine 完成。

| Stage | 名称 | 主要工具 | 主要产物 |
|---|---|---|---|
| 1 | filter | PySceneDetect ContentDetector + OpenCV | `filter_frames/` + `filtered_sample.json` |
| 2 | semantic_filter | 弱 VLM（经 `llm/`） | `semantic_frames/` + `semantic_sample.json` + `semantic_judgements.json` |
| 3 | align | 纯逻辑 + `AudioArtifacts` | `alignments.json` |
| 4 | describe | 强 VLM + refined text，并发 | `descriptions.json` |

`filter` 用 PySceneDetect 检测场景边界，并直接从原视频临时抽取候选帧，只把最终代表帧写入稳定产物。`semantic_filter` 用弱 VLM 筛掉无语义图，后续 `align` 和 `describe` 只使用 `semantic_frames/`。

## Stage Contract

每个 stage 的实现文件暴露统一签名：

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

Stage 之间不互相 import。需要读上游视觉产物时通过 `ctx.paths` 和本模块 `_common.py` 的 reader；需要读音频产物时通过 `ctx.artifacts.audio`。

### Stage 1: filter

**职责**：用 PySceneDetect 检测视频 scene，直接从原视频抽取候选帧并选择代表帧，减少进入弱 VLM 的候选图。

**Input**：输入视频路径（`ctx.source_path`）。

**Output**：`cache/{input_hash}/visual/filter_frames/` 与 `cache/{input_hash}/visual/filtered_sample.json`。

**实现要点**：

- 当前只支持 `ContentDetector`；实现内部通过 detector factory 预留后续扩展入口。
- scene detection 直接分析 `ctx.source_path`。代表帧选择也直接读取原视频，并只写最终 filter 稳定产物。
- `threshold: auto` 会按 `auto_threshold_candidates` 逐个试跑 PySceneDetect，并选择 scene 数最接近 `target_frames_per_minute` 的阈值。
- 每个有效 scene 最多贡献一张候选代表帧，后续低信息过滤和全局近重复过滤仍可能删除候选。
- `representative: content` 表示在每个 scene 内按 `candidate_fps` 临时抽候选帧并计算图片信息量，选择边缘、纹理、对比度和前景密度更高的一张作为该 scene 的候选代表帧。如果整段视频都低于信息量阈值，会保留评分最高的一张作为非空 fallback。
- `representative: last` 表示选择 scene 结束前的帧。
- `representative: middle` 表示选择最接近 scene 中点的帧。
- 如果 PySceneDetect 没有返回有效 scene，把整个视频视为一个 scene。
- `filtered_sample.json` 复用 `VisualSampleIndex`，`SampledFrame.id` 是最终代表帧按 timestamp 排序后的稳定编号，`image_source_path` 相对 `visual/filter_frames/`。

**配置项**（`visual_pipeline.filter.*`）：

- `detector: str`，默认 `content`，当前只支持 `content`。
- `threshold: "auto" | float`，默认 `auto`。数值会直接传给 PySceneDetect `ContentDetector.threshold`；`auto` 会从候选阈值中自动选择。
- `auto_threshold_candidates: list[float]`，默认 `[10.0, 15.0, 20.0, 25.0, 27.0, 30.0, 35.0, 40.0]`，仅在 `threshold: auto` 时使用。
- `target_frames_per_minute: float`，默认 `1.5`，`threshold: auto` 选择阈值时的目标 scene 密度。
- `min_scene_len_seconds: float`，默认 `1.0`，按视频 FPS 换算后传给 PySceneDetect `ContentDetector.min_scene_len`。
- `representative: str`，默认 `content`，可选 `content`、`last` 或 `middle`。
- `candidate_fps: float`，默认 `3.0`，仅用于 filter 阶段临时抽候选帧，不生成稳定 raw frame 产物。
- `min_content_score: float`，默认 `0.5`，`representative: content` 时低信息候选帧的保留阈值。
- `duplicate_pixel_mean_threshold: float`，默认 `0.025`，全局近重复过滤的平均像素差阈值，数值越大去重越激进。

配置示例：

```yaml
visual_pipeline:
  filter:
    detector: content
    threshold: auto
    auto_threshold_candidates: [10.0, 15.0, 20.0, 25.0, 27.0, 30.0, 35.0, 40.0]
    target_frames_per_minute: 1.5
    min_scene_len_seconds: 1.0
    representative: content
    candidate_fps: 3.0
    min_content_score: 0.5
    duplicate_pixel_mean_threshold: 0.025
```

**缓存键**：`visual_filter` 使用算法标记、输入文件 hash 与 filter 配置 hash。算法标记用于隔离不兼容的 filter 缓存版本；cache manifest 只记录稳定产物。`--no-cache` 会重建稳定产物。

### Stage 2: semantic_filter

**职责**：用弱 VLM 判断每张过滤后图片是否有语义价值，并筛掉讲者、黑屏、UI、空白或无笔记价值的画面。

**Input**：`filtered_sample.json` 与 `visual/filter_frames/`。

**Output**：`cache/{input_hash}/visual/semantic_frames/`、`cache/{input_hash}/visual/semantic_sample.json` 与 `cache/{input_hash}/visual/semantic_judgements.json`。

**实现要点**：

- 对每张 `filtered_sample.json` 中的图片独立判断。
- `is_meaningful=true` 仅用于 PPT 正文页、章节标题页、公式、图表、表格、流程图、代码、demo、黑板/白板或其他有报告语义的画面。
- `is_meaningful=false` 用于纯讲者镜头、黑屏、大面积空白、会议 UI、播放器 UI、转场、水印或无笔记价值画面。
- meaningful 图片复制到 `visual/semantic_frames/`。
- `semantic_sample.json` 复用 `VisualSampleIndex`，`image_source_path` 相对 `visual/semantic_frames/`。

**缓存键**：`visual_semantic_filter` 使用 `filtered_sample.json` hash、`visual/filter_frames/` 图片内容 hash、弱 VLM profile hash 与 prompt hash。

### Stage 3: align

**职责**：把有语义图片按 timestamp 对齐到 refined text segments。文本 segment 是多模笔记结构的权威边界。

**Input**：`semantic_sample.json`、`semantic_judgements.json` 与 `ctx.artifacts.audio.get_refined()`。

**Output**：`cache/{input_hash}/visual/alignments.json`。

**实现要点**：

- `segment.start <= frame.timestamp < segment.end` 时分配到该文本 segment。
- 不在任何 segment 内时分配到最近的 refined segment。
- 一个文本 segment 内可以保留多张图，按 timestamp 排序。

**缓存键**：`visual_align` 使用 semantic sample hash、semantic judgements hash 与 refined transcript hash。

### Stage 4: describe

**职责**：用强 VLM 为每张 aligned semantic frame 生成结合对应 refined text segment 的详细视觉描述。

**Input**：`alignments.json`、`visual/semantic_frames/`、`ctx.artifacts.audio.get_refined()` 与 `ctx.artifacts.audio.get_text_at(...)`。

**Output**：`cache/{input_hash}/visual/descriptions.json`。

**实现要点**：

- CLI 调度层保证启动前 `AudioArtifacts.is_complete() == True`，且已运行 `align`。
- 图片从 `visual/semantic_frames/` 解析。
- 传给 VLM 的 `audio_text` 通过 `AudioArtifacts.get_text_at(segment.start, segment.end, strip_refs=True)` 获取，避免把 `[[REF:N]]` 内部 marker 泄漏给 VLM。
- `VisualDescription.frame_id`、`image_source_path`、`medium` 来自 `VisualAlignment`。
- `VisualDescription.start/end` 使用对应 refined text segment 的 `start/end`。
- LLM 调用通过 `for_task(ctx.config, "slide_describe")` 和 `complete_json(...)`。
- 每张 aligned semantic frame 独立调用强 VLM，并发数由 `visual_pipeline.describe.concurrent_calls` 控制。

**缓存键**：`visual_describe` 使用 `alignments.json` hash、`visual/semantic_frames/` 图片内容 hash、去除引用标记后的 audio text hash、强 VLM profile hash 与 prompt hash。

**配置项**（`visual_pipeline.describe.*`）：

- `concurrent_calls: int`，默认 `5`。

## Schema

多模 schema 定义在 `core/schemas/visual.py`，通过 `core/schemas/__init__.py` re-export。

`VisualSampleIndex` 同时用于 `filtered_sample.json` 与 `semantic_sample.json`：

- `filtered_sample.json` 的 `image_source_path` 相对 `visual/filter_frames/`。
- `semantic_sample.json` 的 `image_source_path` 相对 `visual/semantic_frames/`。

`VisualAlignment` 与 `VisualDescription` 的 `image_source_path` 相对 `visual/semantic_frames/`。

关键不变量：

1. `VisualSemanticJudgement.frame_id`、`VisualAlignment.frame_id`、`VisualDescription.frame_id` 使用同一个 `SampledFrame.id` namespace。
2. `filtered_sample.json` 中的 `SampledFrame.id` 按最终 filter 输出的 timestamp 顺序从 `0` 重新编号；`semantic_sample.json` 保留该 namespace。
3. 所有 `image_source_path` 必须是相对路径，不得是绝对路径或通过 `..` 逃逸对应帧目录。
4. `descriptions.json` 是 merge 阶段消费视觉信息的完整产物。

## VisualArtifacts

`VisualArtifacts` 是多模管线对合并阶段的稳定 API。合并阶段不允许 import `visual_pipeline/` 内部模块。

```python
class VisualArtifacts:
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

- `lvnotes filter <video> --mm` 生成 `filter_frames/` 与 `filtered_sample.json`。
- `lvnotes semantic-filter <video> --mm` 生成 `semantic_frames/`、`semantic_sample.json` 与 `semantic_judgements.json`。
- `lvnotes align <video> --mm` 在 refined audio 完成后生成 `alignments.json`。
- `lvnotes run <video> --mm` 端到端生成带视觉图片和描述的 Markdown。
