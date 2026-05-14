# Visual VLM Improvement Plan

本文档是下一轮多模视觉质量改进的交接计划。当前问题不是单个 prompt wording 问题，而是 `semantic_filter`、`align`、`describe` 的合同、schema、prompt 和测试都需要同步收敛。

## Background

真实样本：`videos/aisafety/1.1概念.mp4`。

当前 cache：`cache/808ba627c65f5da158485d600683f9e86a7ece8c82d41a8407b89cc2cdcd52e8/`。

已观察到的问题：

- `visual/semantic_frames/` 保留了同语义但清晰度或动画状态不同的重复帧。
- `visual/descriptions.json` 和最终 `note.md` 的图片描述混入音频上下文，存在非图片可见事实。
- 图片描述没有足够偏向 OCR，PPT 可见文字没有被完整转写。

代表性重复帧：

- `000001.png` / `000002.png`：课程标题页重复。
- `000006.png` / `000007.png`：AI 安全概念框架重复。
- `000011.png` / `000012.png`：后门攻防示意重复。

当前根因：

- `docs/visual-pipeline.md` 与 `semantic_filter.jinja` 当前要求逐帧独立判断，没有语义分组和代表帧选择合同。
- `VisualSemanticJudgement` 只有 `frame_id`、`medium`、`is_meaningful`、`reason`，不能表达同语义组、OCR 文本、清晰度或内容完整度。
- `semantic_filter.py` 只复制所有 `is_meaningful=true` 的帧，没有跨帧 dedupe pass。
- `describe.jinja` 当前要求 “Describe the image using the audio context”，容易让 VLM 用音频补全画面不可见内容。
- `VisualDescription` 只保存最终 `description`，没有保存 OCR 文本或可见证据，无法调试或校验描述是否忠实于图片。
- `align.py` 对不在任何 refined segment 内的帧会绑定最近 segment，片头图可能吸收后续讲师自我介绍文本。

## Decisions

- 同语义重复帧保留内容更完整、文字更清晰、遮挡和模糊更少的一张。
- 允许扩展 `descriptions.json` schema。
- `describe` 阶段必须偏向 OCR：优先完整转写 PPT 上可见文字，再补充图表、布局和视觉结构说明。
- 音频文本只能辅助术语消歧，不能作为图片事实来源。
- 如果图片中文字不可读，描述必须说明不可读，不能从音频或常识猜测。

## Target Contract

### semantic_filter

`semantic_filter` 的职责改为：

- 判断每张 `filter_frames/` 是否有笔记价值。
- 为 meaningful 帧提取可见文字和内容摘要。
- 跨帧识别同一语义内容。
- 对同语义组选择 OCR 更完整、清晰度更高、内容更完整的代表帧。
- 只把代表帧复制到 `visual/semantic_frames/` 并写入 `semantic_sample.json`。
- `semantic_judgements.json` 保留所有输入帧的判断，用于 debug。

同语义组定义：

- 同一张 PPT、同一图表、同一代码页、同一 demo 状态、同一白板内容，且可见语义内容没有实质新增。
- 单纯清晰度、压缩、转场、讲者位置、摄像机裁切、动画微小位置变化不同，不应拆成不同语义组。
- 如果新增了标题、项目符号、公式、图表元素、代码行、关键标注或案例图片，应视为新的语义状态。

建议扩展 `VisualSemanticJudgement`：

```python
@dataclass(frozen=True)
class VisualSemanticJudgement:
    frame_id: int
    medium: str
    is_meaningful: bool
    reason: str
    semantic_key: str | None
    quality_score: int | None
    visible_text: str
    content_summary: str
```

字段规则：

- meaningful 帧必须有非空 `semantic_key`。
- meaningful 帧必须有 `quality_score`，范围固定为 `1..5`，实现和 prompt 必须一致。
- non-meaningful 帧的 `semantic_key` 和 `quality_score` 必须为 `null`。
- `speaker`、`blank`、`ui` 不允许 `is_meaningful=true`。
- `visible_text` 记录 OCR 到的可见文字，无法读取时写空字符串或明确不可读说明。
- `content_summary` 只概括图片可见内容，不引用音频。

代表帧选择规则：

1. `quality_score` 高者优先。
2. `visible_text` 更完整者优先。
3. 本地图像清晰度评分更高者优先，可用 OpenCV Laplacian variance 或其他稳定清晰度指标。
4. 仍并列时使用更小 `frame_id`，保证稳定输出。

### describe

`describe` 的职责改为：

- 对每张 aligned semantic frame 生成图片忠实描述。
- OCR 优先，尽量按 PPT 原顺序转写标题、项目符号、公式、图表标签、代码和表格文字。
- 描述图表、结构、布局、箭头关系、案例图片和视觉重点。
- 使用音频只做术语消歧和上下文命名，不能把音频里但图片不可见的内容写成视觉事实。

建议扩展 `VisualDescription`：

```python
@dataclass(frozen=True)
class VisualDescription:
    segment_id: int
    frame_id: int
    start: float
    end: float
    image_source_path: Path
    medium: str
    description: str
    visible_text: str
    visible_evidence: list[str]
```

字段规则：

- `visible_text` 是图片可见文字的 OCR 结果，尽量保留换行和列表结构。
- `visible_evidence` 是支持 `description` 的可见证据列表，例如标题、关键词、图表标签、图中对象或布局关系。
- `description` 必须基于 `visible_text` 和 `visible_evidence`。
- `description` 不得出现“音频中”“结合音频”“相呼应”“配合音频”“标志着本节收尾”等把音频当成图片事实的表达。

### align and audio context

当前 `align.py` 会把不在任何 refined segment 内的帧绑定到最近 segment。需要避免远距离音频污染图片描述。

建议合同：

- `segment.start <= frame.timestamp < segment.end` 时正常对齐并传该 segment 音频。
- 如果 frame 不在任何 segment 内，但距离最近 segment 边界小于容差，可以对齐并传音频。
- 如果超过容差，仍可保留图片，但 describe 阶段应传空 audio context 或标记为无可靠音频上下文。
- 容差建议配置化，例如 `visual_pipeline.align.max_context_gap_seconds`，默认可以先设 `3.0`。

## Implementation Plan

建议按以下顺序开发，避免合同和实现脱节：

1. 更新合同文档。
   - 修改 `docs/visual-pipeline.md`。
   - 同步 `docs/core.md` 中 visual schema 示例。
   - 如新增配置，同步 `config.yaml`、`config.example.yaml`、`docs/overview.md`。

2. 扩展 schema。
   - 修改 `lvnotes/core/schemas/visual.py`。
   - 同步 `lvnotes/core/schemas/__init__.py` 如需要。
   - 保持 dataclass 字段顺序稳定。

3. 改 `semantic_filter` prompt。
   - 修改 `lvnotes/visual_pipeline/prompts/semantic_filter.jinja`。
   - 删除 “Judge each frame independently”。
   - 要求跨帧比较、同语义 `semantic_key`、OCR、质量评分和代表帧选择依据。

4. 改 `semantic_filter.py`。
   - 校验新 judgement 字段。
   - 按 `semantic_key` 分组。
   - 选最佳帧并只复制代表帧。
   - `semantic_judgements.json` 仍写全量判断。
   - `semantic_sample.json` 只写代表帧。
   - 更新 cache algorithm 或 prompt hash 足以失效旧结果；若 schema 变更明显，建议显式升级 stage cache key 组成或算法标记。

5. 改 `describe` prompt。
   - 修改 `lvnotes/visual_pipeline/prompts/describe.jinja`。
   - 要求返回 `visible_text`、`visible_evidence`、`description`。
   - 明确图片是事实来源，音频只辅助消歧。
   - 强调 OCR 完整性和不可读时不得猜测。

6. 改 `describe.py`。
   - 接收扩展 JSON。
   - 校验 `visible_evidence` 和 `description` 非空。
   - 对 `ppt`、`chart`、`table`、`code`，要求 `visible_text` 非空，除非模型明确写不可读。
   - 写出扩展 `VisualDescription`。

7. 改 `align.py`。
   - 避免远距离 nearest segment 音频污染。
   - 如新增配置，更新 `VisualPipelineConfig` 和 docs。
   - 下游 describe 应能处理空 audio context。

8. 更新 merge 相关逻辑。
   - `merge/unify.py` 当前只消费 `description`，可以保持不变。
   - 如果希望最终 Markdown 展示 OCR 正文，需要同步 `ContentBlock` / `VisualSlot` / assemble 或 section prompt；这不是第一步必须项。

## Prompt Requirements

### semantic_filter.jinja

必须包含以下意图：

- Compare all frames together; do not judge duplicates independently.
- Assign the same `semantic_key` to frames that show the same slide or same visual state.
- Extract visible OCR text for each meaningful frame.
- Score quality by OCR legibility, completeness, sharpness, lack of blur, lack of obstruction, and whether the frame contains the most complete visible content in its semantic group.
- Keep semantic states separate when visible content adds substantial text, formulas, labels, bullets, code, diagrams, examples, or chart elements.
- Do not preserve multiple frames just because one is slightly sharper if their semantic content is the same; downstream code will keep the best one.

### describe.jinja

必须包含以下意图：

- The image is the source of truth.
- OCR first: transcribe all readable slide text as completely as possible.
- Preserve original English technical terms, formulas, labels, code identifiers, chart labels, and proper nouns.
- Audio context is only for disambiguating terms; do not infer unseen content from audio.
- If text is unreadable, say it is unreadable instead of guessing.
- Do not mention the audio context in the final description.
- Return structured JSON with `visible_text`, `visible_evidence`, and `description`.

## Tests To Add

Unit tests should be added before or together with implementation changes.

`semantic_filter` tests：

- Two meaningful frames with the same `semantic_key` keep only the higher `quality_score` frame.
- Same score but longer `visible_text` keeps the more complete OCR frame.
- Different `semantic_key` values both remain.
- meaningful frame without `semantic_key` raises `LLMError`.
- meaningful frame without `quality_score` raises `LLMError`.
- `quality_score` outside the agreed range raises `LLMError`.
- `speaker`、`blank`、`ui` with `is_meaningful=true` raises `LLMError`.
- Prompt test asserts it no longer contains “Judge each frame independently”。

`describe` tests：

- Prompt contains source-of-truth and OCR-first constraints.
- Valid VLM response writes `visible_text` and `visible_evidence` into `descriptions.json`.
- Empty `description` retries or fails.
- Empty `visible_evidence` retries or fails.
- PPT response with empty `visible_text` fails unless it explicitly says text is unreadable.
- Description containing banned audio-context phrases fails or retries.

`align` tests：

- Frame inside segment still aligns normally.
- Frame just outside segment but within tolerance can use nearby audio context.
- Frame far outside any segment does not receive unrelated audio context.

## Real Video Acceptance Criteria

After implementation, run from clean cache or with `--no-cache` on:

```bash
uv run lvnotes run "videos/aisafety/1.1概念.mp4" --mm --no-cache
```

Expected checks：

- Full pipeline completes.
- `pytest -q` passes.
- `ruff check .` passes.
- `semantic_frames/` no longer contains obvious same-semantics duplicates.
- For the current sample, `000006.png` / `000007.png` should not both be kept unless the prompt and judgement prove substantial new visible content.
- For the current sample, `000011.png` / `000012.png` should not both be kept unless one contains substantial new visible content not present in the other.
- `descriptions.json` includes `visible_text` and `visible_evidence` for every described image.
- Final `note.md` image alt text does not contain “音频中”“结合音频”“相呼应”“配合音频”“标志着本节收尾”。
- PPT descriptions include substantially more visible slide text than the current output.

## Review Requirements

This work changes public visual artifacts, schema, prompts, cache behavior and downstream merge inputs. Use an explicit review-fix loop:

- Review round 1 after implementation: schema and contract consistency.
- Review round 2 after prompt/code fixes: artifact quality on the real sample.
- Re-run targeted searches for stale terms such as `Judge each frame independently` and banned audio-context phrases.
- Re-run real video after every prompt/schema change that affects cached visual outputs.
