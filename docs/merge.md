# Merge Stage

合并阶段设计文档。本阶段消费两条管线的产物（`AudioArtifacts` + 可选的 `VisualArtifacts`），合成最终 Markdown 笔记。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/audio-pipeline.md`。

文档结构：Overview、Design Considerations、Stages、Schema、对外契约（产物形态）、Module Layout、Dependencies、Implementation Order。

---

## 1. Overview

合并阶段由 4 步顺序执行：

| 步骤 | 工具 | 主要产物 |
|---|---|---|
| unify | 纯逻辑 | `content_blocks.json`（`ContentBlock` 序列） |
| outline | LLM (经 `llm/`) | `outline.json`（章节结构） |
| section | LLM (经 `llm/`)，并发 | `sections/{chapter_id:03d}.md` × N（`chapter_id` 从 1 开始） |
| assemble | 纯逻辑 | `output_dir/<relative-dir>/<source-stem>.md` 与 `output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md`（最终用户产物） |

**对外产物**：`output_dir/<relative-dir>/<source-stem>.md`（latest 笔记）与 `output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md`（本次导出归档）以及调试用的中间产物（`outline.json` / `sections/*.md` / `content_blocks.json`）。单文件输入时 `<relative-dir>` 为空;目录输入时保留输入目录内的相对目录结构。合并阶段是管线终点，没有 downstream 模块，但产物形态构成**用户接口契约**，详见 §5。

**本阶段不知道两条管线的内部实现**——只通过 `ctx.artifacts.audio` / `ctx.artifacts.visual` 读取 `AudioArtifacts` / `VisualArtifacts`，禁止 `from audio_pipeline import ...` 或 `from visual_pipeline import ...`。`ctx.artifacts` 本身是 `ArtifactBundle`。这是 `docs/overview.md` §6 关键架构约定第 5、6 条以及 `coding-standards.md` §6.2 的强制结论。

纯音频模式下 `VisualArtifacts is None`。`unify` 负责把模式差异归一到 `ContentBlock.visuals`;`assemble` 只读取 `ctx.mode` 写入 frontmatter 并参与 assemble cache key。

---

## 2. Design Considerations

每条 1-3 行：

### 2.1 `ContentBlock` 以 audio segment 为骨架

每个 `RefinedSegment` 对应一个 `ContentBlock`，该段时间区间内相交的视觉片段被切割并挂入 `ContentBlock.visuals`。理由：笔记的小节感来自语义骨架（audio），视觉是补充。纯音频模式下 `visuals=[]`，下游逻辑统一。VisualSlot 挂入的具体规则见 §3.1。

### 2.2 outline 用"短输出"原则

与 audio segment stage 同套路。LLM 看所有 `ContentBlock` 的 summary，**只输出章节边界 + 标题 + 摘要**，不输出章节正文。章节正文由 section stage 按章独立生成。

### 2.3 模式区分

`unify` 是唯一把"纯音频 / 多模"差异合并进内容块的 stage。下游 outline / section 输入都是统一的 `ContentBlock` 序列。`assemble` 不改变内容块,但会把 `ctx.mode` 写入 frontmatter,并把 mode 纳入 assemble cache key。

### 2.4 cross_refs 与时间戳渲染都用内部 marker + 纯逻辑替换

refine 阶段把跨段引用产出为 `[[REF:N]]` marker，section LLM **不**处理引用链接，只把 marker **原样保留**在输出 markdown 中。section LLM 同样**不**输出人类可读时间戳，而是在每个 block 起点插入 `[[TS:<seconds>]]` marker（seconds 为浮点秒，3 位小数）。

assemble 阶段做纯逻辑替换：

- `[[REF:N]]` → `[§{N+1}](#chapter-anchor)`（查 `block_id → chapter_id → anchor`;显示编号使用 1-based）
- `[[TS:123.456]]` → 按 `merge.assemble.timestamp_format` 渲染为可读形态，再按 `merge.assemble.video_url_template` 决定是否包成跳转链接

用 `[[TAG:...]]` 而非让 LLM 直接输出 `[01:23](url?t=83)` 的好处：LLM 不擅长精确生成 URL，时间戳精度可以无损穿过 LLM，assemble 纯逻辑可单测、错误可定位，且 marker 不是 LLM 会自然生成的形态。

### 2.5 section 并发 + per-chapter cache + 断点续跑

每章独立 LLM 调用，并发数由 `merge.section.concurrent_calls` 控制。**缓存粒度是 per-chapter**（不是 stage 级）：改某章 prompt 或上游 ContentBlock，只该章失效。manifest 命中的章节跳过，`chapter_id` 从 1 开始，例如第一章为 `sections/001.md`。

这与 `audio-pipeline.md` §3.4 refine 的 stage 级缓存不同：refine 可能通过 `single_call`、`batched` 或 fallback `serial` 产出同一个 `RefinedTranscript`，因此以 stage 为缓存单元；section 章节之间相对独立（仅共享 outline 全局摘要作为 context），适合 per-chapter 缓存。

### 2.6 assemble 是纯逻辑

不调 LLM，确定性拼接。方便调试、复跑、单测覆盖。

---

## 3. Stages

每个 stage 用统一子结构：**职责 / Input / Output / 实现要点 / 配置项 / 缓存键 / 错误处理**。

每个 stage 的实现文件（`unify.py` / `outline.py` / `section.py` / `assemble.py`）暴露统一签名：

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

合并阶段不通过 `AudioArtifacts` 风格的接口对外暴露产物（因为没有 downstream 模块），stage 间通过 `ctx.paths` 按文件 IO 传递。

### 3.1 Stage 1: unify

**职责**：把音频和（可选的）多模产物合成 `ContentBlock` 序列。**纯逻辑，不调 LLM**。

**Input**：
- `ctx.artifacts.audio.get_refined()` → `RefinedTranscript`
- `ctx.artifacts.visual.get_descriptions()`（多模模式时；纯音频模式 `ctx.artifacts.visual is None`）

**Output**：`list[ContentBlock]`（schema 见 §4）。落盘 `cache/{input_hash}/content_blocks.json`。

**实现要点**：

*以 audio 为骨架*：每个 `RefinedSegment` 创建一个 `ContentBlock`，字段从 `RefinedSegment` 复制（`id`、`start`、`end`、`topic`、`cleaned_text`、`summary`、`cross_refs`），`visuals` 字段按下面规则填充。

*VisualSlot 挂入规则*（多模模式）：

`VisualDescription` 已经由 describe stage 从 `VisualAlignment` 和对应 refined segment 复制了图片、时间区间、medium 与语义过滤后的结果,并包含 OCR 调试字段 `visible_text` / `visible_evidence`。unify 只消费 `description` 生成 Markdown 视觉 slot，不读取 `alignments.json`。

对每对 `(audio_segment, visual_description)`：

1. 若 `[audio.start, audio.end]` 与 `[visual.start, visual.end]` 不相交 → 跳过
2. 否则在该 audio 段对应的 ContentBlock 上创建一个 `VisualSlot`：
   - `slot.start = max(audio.start, visual_description.start)`
   - `slot.end = min(audio.end, visual_description.end)`
   - `slot.image_source_path` / `slot.description` / `slot.medium` 从 `VisualDescription` 复制
   - `slot.visual_segment_id = visual_description.segment_id`

跨越多个 audio 段的 visual description 会在每个被跨越的 ContentBlock 内各挂一个 VisualSlot（指向同一张 frame 和同一段 description，但时间区间各自 clip）。一个 audio 段内的多个 visual description 全部挂上，按时间排序。

*纯音频模式*：直接走以 audio 为骨架那条路径，`visuals=[]` 即可。`unify` 按 `PipelineContext.mode` 判断本次运行模式,纯音频模式强制忽略 visual artifacts;多模模式要求 visual descriptions 可读取。

**配置项**：无。

**缓存键**：调用 `build_cache_key("unify", {"refined": hash_json(RefinedTranscript), "visual": visual_hash})`。`visual_hash` 在多模模式下总是 descriptions 的内容 hash,即使 descriptions 为空列表；纯音频模式下是固定标记 `"audio_only"`。

**错误处理**：
- ContentBlock 不变量校验（见 §4 不变量）违反 → `AssertionError`（属内部 bug，不 catch）
- VisualSlot.start/end clip 后 `start >= end`（数值精度边界 case）→ 跳过该 slot 不报错；记 `DEBUG` 日志

### 3.2 Stage 2: outline

**职责**：LLM 看所有 ContentBlock 的 summary，输出章节结构。

**Input**：`list[ContentBlock]`（从 `cache/{input_hash}/content_blocks.json` 读）。

**Output**：`Outline`（schema 见 §4）。落盘 `cache/{input_hash}/outline.json`。

**实现要点**：
- **单次 LLM 调用，不分块**。即使章节数 100+，summary 总量也远小于全文转录
- **短输出原则**：LLM 输出 `Outline` JSON object,形如 `{"chapters": [...]}`；每个 chapter 含 `id / title / summary / block_id_start / block_id_end`；不复述章节正文
- 用包内模板 `lvnotes/merge/prompts/outline.jinja` 渲染。模板内容：任务说明 + 目标章节数 hint + 所有 ContentBlock 的 `(id, topic, summary)` 列表（按 id 序）
- 通过 `client = for_task(ctx.config, "outline")` 获取 LLM client。LLM JSON 解析 + 1 次修复重试 + schema 校验走 `complete_json_with_raw(client, messages, schema, options, max_repair_retries=1)` helper,以便失败诊断文件记录原始 LLM 输出
- 输出 JSON 的解析与校验：
  1. 解析失败 → 重试 1 次；仍失败抛 `LLMError`
  2. 校验章节 id 从 1 开始严格递增：`chapters[i].id == i + 1`
  3. 校验范围递增不重叠：`chapters[i].block_id_end + 1 == chapters[i+1].block_id_start`
  4. 校验覆盖完整：`chapters[0].block_id_start == 0`、`chapters[-1].block_id_end == len(blocks) - 1`
  5. 校验单章范围合法：`block_id_start <= block_id_end` 且都在 `[0, len(blocks)-1]`
  6. 任一不变量校验失败时写入失败诊断文件,并自动发起 1 次 outline 修复重试；修复后仍失败则抛 `LLMError`

**配置项**（`merge.outline.*`）：
- `target_chapter_count_hint: str` —— 例如 `"5-12"`，作为 prompt 中的目标章节数提示，非硬约束

**缓存键**：调用 `build_cache_key("outline", {"blocks": hash_file(ctx.paths.content_blocks_json), "config": hash_json(outline 配置), "profile": hash_json(LLM profile), "prompt": hash_prompt_template("lvnotes/merge/prompts/outline.jinja")})`。模板 hash 经 `core/cache.py` 的 `hash_prompt_template()` 归一化后再计算，避免注释 / 缩进微调触发重跑。

**错误处理**：
- LLM JSON 解析失败：1 次重试后上抛 `LLMError`
- LLM 输出违反不变量：每次失败尝试写入 `cache/{input_hash}/debug/outline-failure-YYYYMMDD-HHMMSS-ffffffZ.json`;自动修复 1 次后仍失败则上抛 `LLMError` 含具体不变量名
- LLM endpoint 5xx / 限流：`tenacity` 自动重试，转 `TransportError` / `RateLimitError`

### 3.3 Stage 3: section

**职责**：每章独立 LLM 调用，把 ContentBlock 的转录文本 + 视觉描述编织成可读章节 Markdown。

**Input**：`Outline` + `list[ContentBlock]`。

**Output**：每章一个 markdown 文件，落盘 `cache/{input_hash}/sections/{chapter_id:03d}.md`。`chapter_id` 从 1 开始，第一章为 `cache/{input_hash}/sections/001.md`。

**实现要点**：

*单章 prompt 内容*：
1. 任务说明 + 风格指引（写作语气、详略要求）
2. 全 outline 章节摘要（所有章的 `id / title / summary`）—— 让 LLM 理解本章在全文中的位置
3. 本章涉及的 ContentBlock 列表（按 id 序），每个 block 含 `id / start / end / topic / cleaned_text / visuals`

通过 `client = for_task(ctx.config, "section")` 获取 LLM client。每章文本输出走 `complete_text(client, messages, options)`。

*并发*：可用 `asyncio.Semaphore(concurrent_calls)` 或等价机制控制并发上限。每章一个任务,但不改变 stage 对外的同步 `run(ctx) -> StageOutput` 签名。

*内部 marker 保留*：refine 阶段产出的 `[[REF:N]]` 跨段引用 marker **原样保留在输出 markdown 中**。prompt 中明确指示 LLM "见到形如 `[[REF:N]]` 的标记一律原样输出，不要替换、删除、改写"。链接化由 assemble 阶段做。

*时间戳标注*：每个 block 起点在章节 markdown 中插入 `[[TS:<seconds>]]` marker，seconds 为该 block 的 `start` 字段值（浮点秒，3 位小数）。prompt 中明确指示 LLM "见到 block 起点位置时输出 `[[TS:<原样的 start 值>]]` marker，不要替换、不要包链接、不要改格式；见到形如 `[[REF:N]]` 的标记同样原样输出"。

渲染形态（可读时间戳格式 / 是否生成跳转链接 / URL template）全部由 assemble 阶段按配置决定，本 stage 不感知。

*视觉内容渲染*：多模模式下,visuals 列表中每个 VisualSlot 在该 block 对应位置插入 markdown 图片引用。section prompt 必须要求 LLM 对每条 Visual 输入原样保留一条图片 Markdown,不得省略或改写路径：

```markdown
![visual description](relative/path/to/frame.png)
```

具体相对路径由 `core/paths.py` 提供（不在 section 阶段拼路径）。纯音频模式下 `visuals=[]`,不会插入图片。图片 markdown 中**不要**嵌入时间戳；时间戳由独立的 `[[TS:...]]` marker 表达。

*per-chapter 缓存与断点续跑*：每章 LLM 调用前先查该章 cache manifest，manifest 命中则跳过。每完成一章就落盘 `sections/{chapter_id:03d}.md` 并写入 `sections/{chapter_id:03d}.md.cache.json`，`chapter_id` 从 1 开始。某章失败时其他章已完成的不丢，整个 stage 重跑时只跳过 manifest 命中的章。只存在 markdown 文件但 manifest 缺失或 cache key 不匹配时必须重新生成该章。

`ctx.no_cache is True` 时跳过所有 per-chapter manifest 命中判断,重新生成所有章节并覆盖 `sections/{chapter_id:03d}.md`。用户手工编辑 sections 后只想重新合成最终笔记时应运行 `assemble --no-cache`,不要运行 `section --no-cache`。

**配置项**（`merge.section.*`）：
- `concurrent_calls: int` —— 默认 5

**缓存键**（per-chapter）：单章 cache 键调用 `build_cache_key(f"section_chapter_{chapter_id:03d}", {"chapter_blocks": hash_json(本章 ContentBlocks), "outline": hash_json(Outline), "config": hash_json(section 配置), "profile": hash_json(LLM profile), "prompt": hash_prompt_template("lvnotes/merge/prompts/section.jinja")})`。`stage_name` 是格式化后的字符串,例如 `section_chapter_001`。模板 hash 走 `core/cache.py` 的 `hash_prompt_template()`（归一化后再 hash），避免注释 / 缩进微调触发全部章重跑。每章 manifest 写入 `sections/{chapter_id:03d}.md.cache.json`。

注意：因为 prompt 含全 outline 摘要，所以 outline 变了所有章 cache 都失效。这是符合期望的——章节摘要变了每章正文都需要重写以保持上下文一致。

**错误处理**：
- 单章 LLM 失败：`tenacity` 重试，耗尽抛 `LLMError`，整个 stage 中止（已完成章保留落盘，下次重跑接续）
- LLM 输出包含编造的 `[[REF:N]]`（N 超出 ContentBlock id 范围）：不在本 stage 校验；assemble 阶段会发现并降级处理（见 §3.4）
- LLM endpoint 5xx / 限流：`tenacity` 自动重试

### 3.4 Stage 4: assemble

**职责**：把所有 sections 拼成最终 Markdown 笔记，处理 cross_refs 链接化与时间戳跳转链接。**纯逻辑，不调 LLM**。

**Input**：`Outline` + `list[ContentBlock]` + `sections/{chapter_id:03d}.md` × N，`chapter_id` 从 1 开始。

**Output**：`output_dir/<relative-dir>/<source-stem>.md` 作为 latest 用户产物，`output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md` 作为本次导出归档。单文件输入时 `<relative-dir>` 为空;目录输入时保留输入目录内的相对目录结构。`cache/{input_hash}/note.md` 作为 debug/cache copy，便于调试、inspect 与断点续跑。

**实现要点**：

*章节锚点生成*：每章 anchor 由 `core/slugs.py` 的 `make_chapter_anchor(chapter_id, title) -> str` 生成。函数语义：

- 对中文等 CJK 字符：`unicodedata.normalize("NFKC")` 后保留
- 仅替换空白字符与 markdown 不安全字符为 `-`
- 强制 `chapter-{id}-` 前缀，即使 title 为空或全部被替换掉也能产出唯一 anchor
- 多个连续 `-` collapse 为一个，首尾 strip

Anchor 不持久化在 `Chapter` schema 内（见 §4 注），由 assemble 在内存中按需生成。

*cross_refs 链接化*：

1. 构建 `block_id → chapter_id` 映射：扫描所有 chapter，按 `[block_id_start, block_id_end]` 闭区间填表
2. 构建 `chapter_id → anchor` 映射（调 `make_chapter_anchor`）
3. 对每个 sections markdown 内容，扫描所有 `[[REF:(\d+)]]` marker：
   - 解析 N（0-based block_id）→ 查 `block_id → chapter_id` → 查 `chapter_id → anchor` → 替换为 `[§{N+1}](#anchor)`
   - 找不到对应（理论上 refine 阶段已校验、不该发生）→ 替换为纯文本 `§{N+1}` + 记 `WARNING` 日志（`assemble: cross_ref §{N+1} not resolvable, rendered as plain text`），不阻塞

*时间戳跳转链接*：

assemble 阶段扫描所有 `[[TS:(\d+\.\d+)]]` marker，按以下两步替换：

1. **格式化为可读时间戳**：按 `merge.assemble.timestamp_format`（支持 `{hms}` / `{mmss}` / `{seconds}` / `{seconds_int}` 占位符）生成可读字符串，如 `[01:23:45]`。所有格式化经 `core/timestamps.py`。
2. **包跳转链接（可选）**：按 `merge.assemble.video_url_template` 生成 URL：
   - `video_url_template is None` → 保留可读字符串，不包链接
   - 否则 → 替换为 markdown 链接 `[<可读字符串>](<URL>)`

Template 支持的占位符：

| 占位符 | 含义 |
|---|---|
| `{seconds}` | 浮点秒数（含毫秒，3 位小数） |
| `{seconds_int}` | 整数秒（向下取整） |
| `{source_path}` | 解析后的绝对本地输入路径，可能是音频或视频 |
| `{source_filename}` | 输入文件名（不含路径） |
| `{hms}` | hh:mm:ss 格式 |

常见配置示例：

| 场景 | template | 生成结果 |
|---|---|---|
| 本地 file URL | `"file://{source_path}?t={seconds_int}"` | `file:///home/u/lec.mp4?t=120` |
| 笔记与输入文件同目录 | `"{source_filename}?t={seconds_int}"` | `lec.mp4?t=120` |
| 自有媒体服务器 | `"https://my-host/v/{source_filename}?t={seconds}"` | `https://my-host/v/lec.mp4?t=120.500` |

理由：本地路径、内网服务、外网 URL 三种场景需求差异大；不强制 file:// 默认值（避免在不同主机上路径失效），不做约定优于配置（避免要求笔记与视频共目录）。未配置就保留纯文本，是最安全的默认。

*文件结构*：

frontmatter 中的 `mode` 来自 `PipelineContext` 中由 CLI 构造的本次运行模式,只能是 `audio_only` 或 `multimodal`。它不来自配置文件,也不由 assemble 根据 visual 产物是否存在自行推断。

```markdown
---
source_path: <解析后的绝对本地输入路径>
duration: <hh:mm:ss>
generated_at: <UTC ISO-8601>
mode: audio_only | multimodal
llm_profiles:
  segment: <profile name>
  refine: <profile name>
  outline: <profile name>
  section: <profile name>
  slide_judge: <profile name or null>
  slide_describe: <profile name or null>
---

# <顶级标题，从输入文件名派生或可配置>

<可选目录>

## <chapter 1 标题>
<sections/001.md 内容>

## <chapter 2 标题>
<sections/002.md 内容>

...
```

YAML frontmatter 包含元信息，VSCode/Obsidian/Jekyll 等 markdown 工具通用。
`llm_profiles` 记录本次配置中各 LLM task 映射到的 profile name。纯音频模式下 `slide_judge` / `slide_describe` 写 `null`。

*目录*：

由 `merge.assemble.include_toc` 控制（默认 true）。简单 markdown 目录：每章一行 `- [章节标题](#anchor)`。

**配置项**（`merge.assemble.*`）：
- `timestamp_format: str` —— 默认 `"[{hms}]"`，支持的占位符：`{hms}`（hh:mm:ss）、`{mmss}`（mm:ss）、`{seconds}`（小数秒）、`{seconds_int}`（整数秒）
- `include_toc: bool` —— 默认 true
- `include_metadata: bool` —— 默认 true（YAML frontmatter）
- `video_url_template: str | None` —— 默认 `None`（时间戳保持纯文本）
- `top_title: str | None` —— 默认 `None`，未配置时从输入文件名派生

**缓存键**：调用 `build_cache_key("assemble", {"mode": ctx.mode, "outline": hash_json(Outline), "blocks": hash_file(ctx.paths.content_blocks_json), "sections": hash_json(所有 sections/*.md 的内容 hash 列表), "config": hash_json(assemble 配置)})`。

**错误处理**：
- cross_refs 引用不存在 → WARNING 日志 + 降级为 1-based 纯文本 `§{N+1}`，不阻塞（见上）
- section 输出含 `[[TS:abc]]` 等格式异常的 marker → assemble 不解析，保留原文 + WARNING 日志
- section 输出 `[[REF:N]]` 中 N 越界 → 见 cross_refs 链接化第 3 点，降级为 1-based 纯文本 `§{N+1}`
- video_url_template 含未支持的占位符 → 启动时配置校验失败（`ConfigError`），不进入运行
- 写入 note.md 失败（IO 错误）→ 自然抛 `OSError`

---

## 4. Schema

合并阶段引入的 dataclass 集中定义在 `core/schemas/merge.py`，通过 `core/schemas/__init__.py` re-export。所有 dataclass `frozen=True`。

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class VisualSlot:
    image_source_path: Path         # 语义过滤后帧文件路径，相对 cache/{input_hash}/visual/semantic_frames/
    description: str                # 强 VLM 输出的图文联合描述
    medium: str                     # semantic_filter 阶段输出，"ppt" / "blackboard" / "code" / "demo" / "chart" / "table" / "speaker" / "blank" / "ui" / "other"
    start: float                    # clip 到所属 ContentBlock 区间内的起点
    end: float                      # clip 到所属 ContentBlock 区间内的终点
    visual_segment_id: int          # 对应的 VisualDescription.segment_id，便于回溯

@dataclass(frozen=True)
class ContentBlock:
    id: int                         # 与 RefinedSegment.id 共享 namespace、同值
    start: float
    end: float
    topic: str
    cleaned_text: str               # 含 [[REF:N]] 内部 marker，由 refine 产出
    summary: str
    cross_refs: list[int]           # 严格 ref < self.id
    visuals: list[VisualSlot]       # 纯音频模式下空列表

    @classmethod
    def from_refined(
        cls,
        segment: "RefinedSegment",
        visuals: list[VisualSlot],
    ) -> "ContentBlock":
        """从 RefinedSegment + 已切割的 visuals 列表合成 ContentBlock。"""
        ...

@dataclass(frozen=True)
class Chapter:
    id: int                         # 1-based 严格递增,用于 sections/{chapter_id:03d}.md
    title: str                      # LLM 生成
    summary: str                    # LLM 生成，1-2 句话
    block_id_start: int             # 闭区间起点
    block_id_end: int               # 闭区间终点

@dataclass(frozen=True)
class Outline:
    chapters: list[Chapter]
    # 注:不含 config_hash / runtime_hint;由 StageOutput sidecar metadata 携带
```

注：`Chapter` 不含 `anchor` 字段。anchor 由 assemble 阶段从 `(id, title)` 生成（`make_chapter_anchor` 函数），不持久化在 schema 里。这样 schema 字段都是真"内容"，避免跨 stage 字段延迟填充导致的不可变性破例。

### 不变量

跨 stage 的统一约定：

1. `ContentBlock.id` 与 `RefinedSegment.id` 一一对应、同值
2. `ContentBlock` 列表按 `id` 严格递增，覆盖 `[0, len-1]`
3. `Chapter.id` 从 1 开始严格递增；`block_id_start ≤ block_id_end`；相邻 chapter 间 `chapters[i].block_id_end + 1 == chapters[i+1].block_id_start`
4. 第一个 chapter `block_id_start == 0`，最后一个 `block_id_end == len(blocks) - 1`（覆盖所有 block）
5. `cross_refs[i] < self.id` 且对应到存在的 `ContentBlock.id`
6. `VisualSlot.start ≥ ContentBlock.start` 且 `VisualSlot.end ≤ ContentBlock.end`（clip 已应用）
7. 所有 `start < end`、时间戳精度毫秒

不变量违反 → `AssertionError`，属 `coding-standards.md` §3.1 表中"不可预期的内部错误"，不 catch、不"自动修复"。

---

## 5. 对外契约（产物形态）

合并阶段是管线终点，**没有 downstream 模块**。但产物形态构成**用户接口**，需要明确稳定。

### 稳定的产物

- `cache/{input_hash}/note.md` —— Markdown 笔记 debug/cache copy（UTF-8），便于调试与断点续跑
- `output_dir/<relative-dir>/<source-stem>.md` —— latest 最终用户产物
- `output_dir/<relative-dir>/<source-stem>-YYYYMMDD-HHMMSS.md` —— 本次导出归档最终用户产物
- `cache/{input_hash}/outline.json` —— `Outline` 序列化，便于工具按章节切分笔记或重新生成单章
- `cache/{input_hash}/sections/{chapter_id:03d}.md` —— 各章独立 markdown，便于用户单独编辑后重新 assemble
- `cache/{input_hash}/content_blocks.json` —— `list[ContentBlock]` 序列化，调试用
- `cache/{input_hash}/debug/outline-failure-YYYYMMDD-HHMMSS-ffffffZ.json` —— outline 不变量失败诊断历史,不参与 cache manifest

`lvnotes output tidy` 可把已生成的 timestamped archive 移动到 `output_dir/_archive/<relative-dir>/` 做后处理整理。该命令不会改变 `run` / `assemble` 的产物契约:后续 assemble 仍会在 output 原位写入 latest 和新的 timestamped archive。整理时同名 `<source-stem>-YYYYMMDD-HHMMSS_assets/` 会随 Markdown 一起移动,确保归档笔记内的相对图片链接继续有效。

### CLI 访问入口

CLI 提供 `lvnotes inspect <namespace> <stage> <input-path>` 查看任意中间产物，通过顶层 stage 命令单独重跑某一 stage（如 `lvnotes outline <input-path>`、`lvnotes assemble <input-path>`），并通过 `lvnotes output tidy` 整理 output 下的 timestamped archives（实现时在 `cli/app.py` 中描述）。

### 用户编辑流程支持

用户编辑某章 `sections/{chapter_id:03d}.md`（如 `sections/001.md`）后想重新合成笔记：

```bash
lvnotes assemble <input-path> --no-cache  # 跳过 assemble 缓存，重读 sections，重新生成 latest 与带时间戳归档笔记
```

不需要重跑 section LLM。这种工作流由 per-chapter 缓存 + `assemble` 是纯逻辑共同支持。

---

## 6. Module Layout

```
lvnotes/merge/
├── __init__.py
├── unify.py                # Stage 1，纯逻辑
├── outline.py              # Stage 2，LLM
├── section.py              # Stage 3，LLM 并发
├── assemble.py             # Stage 4，纯逻辑
└── prompts/
    ├── outline.jinja
    └── section.jinja
```

每个 stage 固定暴露同步接口 `run(ctx) -> StageOutput`。`section.py` 可在该同步接口内部实现并发细节，但不改变 stage 对外签名。

### Import 规则速查

| 来源 | 允许？ |
|---|---|
| `core/`（schemas、artifacts、paths、timestamps、pipeline、cache、config、context、logging、exceptions、constants） | ✅ |
| `core/slugs.py` | ✅（仅 assemble） |
| `llm/` | ✅（仅 outline、section） |
| `audio_pipeline/` 内部 | ❌（必须经 `core/artifacts.AudioArtifacts`） |
| `visual_pipeline/` 内部 | ❌（必须经 `core/artifacts.VisualArtifacts`） |
| `merge/` 内的其他 stage 文件 | ❌（stage 间通过 `ctx.paths` 文件 IO 解耦） |
| `media/` / `asr/` | ❌（合并阶段不需要） |
| `openai` / `anthropic` 等 SDK 直接 import | ❌（必须经 `llm/`） |

---

## 7. Dependencies

### 项目内
- `core/`：`schemas`、`artifacts`、`paths`、`timestamps`、`pipeline`、`cache`、`config`、`context`、`logging`、`exceptions`
- `llm/`：outline、section 用

### 外部库
- `jinja2`：渲染 prompt 模板
- `pydantic`：配置加载（间接，经 `core/config.py`）
- `tenacity`：API 重试

具体依赖清单与版本以 `pyproject.toml` 为准。

不依赖 `media/`、`asr/` 及其外部库。

---

## 8. Stage Validation

合并阶段支持纯音频与多模输入。`unify` 负责把可选视觉信息挂入 `ContentBlock.visuals`，`section` 根据 `VisualSlot` 渲染图片引用，`assemble` 生成最终 Markdown。

### 单 stage 验收标准

按 `coding-standards.md` §19.2 的硬性清单：

1. 主路径用真实输入跑通端到端
2. 缓存机制工作（再跑一次能命中缓存）
3. 错误路径有测试覆盖
4. 类型检查通过
5. 独立 CLI 调用可用（如 `lvnotes outline <input-path>`、`lvnotes assemble <input-path> --no-cache`）
6. 至少一个其他模块的范例参照（除第一个 stage 外，参照 `audio_pipeline/refine.py` 等）

任意一条不满足不算完成，不要进下一个 stage。
