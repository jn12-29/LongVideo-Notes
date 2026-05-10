# Audio Pipeline

音频管线设计文档。本管线把输入文件转成结构化的 `RefinedTranscript`,对外通过 `AudioArtifacts` 暴露产物。**写代码前必读**本文档以及 `coding-standards.md` 与 `README.md`。

文档结构:Overview、Design Considerations、Stages、Schema、Downstream Interfaces、Module Layout、Dependencies、Implementation Order。

---

## 1. Overview

音频管线由 4 个 stage 顺序执行:

| Stage | 名称 | 主要工具 | 主要产物 |
|---|---|---|---|
| 1 | extract | ffmpeg (经 `media/`) | `audio.wav` (16kHz mono) + 元信息 |
| 2 | transcribe | faster-whisper (经 `asr/`) | `transcript_raw.json`(带 word-level 时间戳) |
| 3 | segment | LLM (经 `llm/`) | `segments.json`(语义切分点) |
| 4 | refine | LLM (经 `llm/`) | `refined/{seg_id}.json` × N,最终汇为 `refined_transcript.json` |

**对外产物**集中在 `AudioArtifacts`(`core/artifacts.py`)。下游模块(多模管线、合并阶段)只通过这个接口读取,不直接 import `audio_pipeline/` 内部,也不直接读缓存文件。详见 §5。

**本管线不知道下游存在**——既不知道有多模管线,也不知道有合并阶段。这是 `docs/overview.md` §6 关键架构约定第 5、6 条以及 `coding-standards.md` §6.2(单向依赖)的强制结论。这条约束直接决定了产物面向"通用下游"而非具体下游:见 §2 第 3 条。

纯音频模式与多模模式下,本管线行为完全相同。区别仅在于多模模式下 `RefinedTranscript` 还会被多模管线 stage 5 (describe) 消费一次。

---

## 2. Design Considerations

不展开成长篇论述,每条点出关键决策。

### 2.1 Stage 3 segment 用"短输出"原则

LLM 一次看全文转录,**只输出切分点**(每个段的 start/end + topic_hint),**不输出整段文本**。本管线把"切分"和"整理"拆开:前者短输出,后者默认用 adaptive refine 优先一次生成完整 `RefinedTranscript`,失败时再分批或逐段兜底。

### 2.2 Stage 4 refine 用 adaptive 策略

默认先尝试一次 LLM 调用生成完整 `RefinedTranscript`。如果完整输出解析、schema 或业务校验失败,按 `batch_size` 分批生成;若某个 batch 失败,仅该 batch 退回逐段 serial。这样短/中等输入优先少调用,长输出或局部困难段仍有可靠兜底。

所有 refine prompt 都要求 `cleaned_text` 是带完整中文标点的书面表达,不得输出无标点的 ASR 逐字稿。

### 2.3 中间产物全部保留,面向通用下游

即使本管线"自己"只需要 `RefinedTranscript`,仍然必须保留 `Transcript`(raw whisper 输出)和 `SegmentList`:

- `Transcript` 给多模 describe 做图文联合理解的兜底(refined 时间戳精度可能因段合并损失,遇到此情形可退回 raw)
- `SegmentList` 给多模管线对齐参考(如果视觉聚类的段边界与语义段边界严重不一致,下游可记录警告)
- `Transcript` 还给调试和重跑时的人工核对用

"自己用不上就丢"会让下游没法接入。这是接口预留的工程结论。

### 2.4 跨段术语呼应

refine 阶段在整理第 K 段时,识别当前段引用的前文概念(前 K-1 段已讲过的术语 / 主题),记录到 `RefinedSegment.cross_refs`(前置段 id 列表),并在 `cleaned_text` 中嵌入 **`[[REF:N]]`** 形式的内嵌引用 marker(N 为被引用段的 id)。

用 `[[REF:N]]` 而非"§N"等人类形态是为了让"机器可识别且 LLM 不会自然生成"——下游 section LLM 看到该 marker 时被 prompt 明确要求**原样保留**,assemble 阶段做纯逻辑替换为人类可读形态(如 `[§{N+1}](#chapter-anchor)`)。

**本管线只产出引用 marker 与关系,不做任何渲染**——人类可读形态由 `merge/assemble` 决定。

### 2.5 时间戳精度

全管线时间戳一律 `float` 秒数、精度毫秒(3 位小数)。跨 stage、跨管线传递时不允许精度损失。所有格式化经 `core/timestamps.py`,本文不重复 `docs/overview.md` §5.1 的硬约定。

### 2.6 缓存键

每个 stage 的缓存键由 `core/cache.py` 按以下三元组合成:**输入产物的内容 hash + 该 stage 相关配置的 hash + stage 名**。配置 hash 含 LLM profile、prompt 模板、stage 自身的阈值参数。改任意一项会让该 stage 缓存失效,无需手动 invalidate。具体每个 stage 的缓存键组成见 §3 各小节。

Prompt 模板的 hash 走 `core/cache.py` 的 `hash_prompt_template(path)` 唯一入口,内部做最小归一化:去除 `{# ... #}` Jinja 注释、collapse 连续空白为单空格、strip 首尾空白。模板内的注释调整、缩进美化不会触发整 stage 重跑。

## 3. Stages

每个 stage 用统一子结构:**职责 / Input / Output / 实现要点 / 配置项 / 缓存键 / 错误处理**。

每个 stage 的实现文件 (`extract.py` / `transcribe.py` / `segment.py` / `refine.py`) 暴露统一签名:

```python
def run(ctx: PipelineContext) -> StageOutput: ...
```

Stage 之间不互相 import。需要读上游产物时,通过 `ctx.artifacts.audio`(`AudioArtifacts` 实例)访问,与外部消费者走同一接口。`ctx.artifacts` 本身是 `ArtifactBundle`。

所有跨段累积型产物的写入(`refined/{seg_id:04d}.json` 等)必须经 `core/cache.py` 的 `atomic_write_json` 入口,见 `coding-standards.md` §6.1。

### 3.1 Stage 1: extract

**职责**:把输入视频/音频文件抽取成 16kHz mono `audio.wav`,附带元信息。

**Input**:输入媒体文件路径(来自 `ctx.source_path`,可为本地视频或音频)。

**Output**:`AudioExtractResult`(schema 见 §4)。落盘 `cache/{input_hash}/audio/audio.wav` + `cache/{input_hash}/audio/extract.json`。

**实现要点**:
- 走 `media/audio.py` 的 `extract_wav(input_path, output_path, sample_rate, channels)` 调用,**禁止直接 `subprocess.run`**
- 重采样目标参数(`sample_rate`、`channels`)来自配置(见下"配置项")。当前 ASR 后端 faster-whisper 的推荐输入是 16kHz mono,故默认值如此;将来若加入要求不同输入参数的 ASR 后端,此处可调
- 读取 duration / 原始 sample_rate / 原始 channels / codec 等元信息走 `media/probe.py`
- 元信息序列化为 JSON 一并落盘(经 `atomic_write_json`),下游读元信息不需要再 probe 一次

**配置项**(`audio_pipeline.extract.*`):
- `sample_rate: int` —— 默认 `16000`。重采样目标采样率。validator 限制为正整数且在 `{8000, 16000, 22050, 32000, 44100, 48000}` 之内
- `channels: int` —— 默认 `1`。重采样目标通道数。validator 限制为 `1` 或 `2`

**缓存键**:调用 `build_cache_key("extract", {"input": hash_file(ctx.source_path), "config": hash_json(extract_config)})`。

**错误处理**:
- 输入文件不存在:不在 stage 内做额外预检,由 `media/` 调 ffmpeg / ffprobe 后包装为 `MediaError`
- ffmpeg 调用失败:`media/` 内部包装为 `MediaError` 上抛
- 输出 wav 必须可被 `ffprobe` 读取;`AudioExtractResult.duration` 记录抽取后 wav 的 probe duration

### 3.2 Stage 2: transcribe

**职责**:把 `audio.wav` 转成带 word-level 时间戳的 `Transcript`。

**Input**:`ctx.artifacts.audio.get_extract()` 拿 `AudioExtractResult`。

**Output**:`Transcript`(schema 见 §4)。落盘 `cache/{input_hash}/transcript_raw.json`(经 `atomic_write_json`)。

**实现要点**:
- 走 `asr/` 的 `Transcriber.transcribe(audio_path, asr_config) -> Transcript`,**禁止直接 import `faster_whisper`**
- 第一版只实现 `faster_whisper_local` backend
- `BatchedInferencePipeline` **仅在 GPU 上有 3-5x 收益**,CPU 上无收益甚至更慢;`asr/faster_whisper_local.py` 内根据 `device` 自动选择是否启用 batched
- `condition_on_previous_text=False` 必须显式关闭,长音频中一次幻觉会通过该参数传染后续段落(faster-whisper 已知问题)
- VAD **默认开**。faster-whisper 在静音段(非语音区间)会大量幻觉,VAD 是硬刚需而非可选优化
- `word_timestamps=True`,用于按语义段时间范围切出当前段文本;一个 ASR segment 可以被分入多个语义段
- 不传 `initial_prompt`,避免 prompt 文案被模型幻觉进转录正文
- 输出归一化为 `Transcript` dataclass。`asr/` 不暴露 faster-whisper 原生类型给上层

**配置项**(来自 `asr.*`,见 `docs/overview.md` §8):
- `asr.backend`: `faster_whisper_local`(第一版唯一)
- `asr.model`: `large-v3` 等
- `asr.device`: `auto` / `cuda` / `cpu`
- `asr.compute_type`: `auto` / `float16` / `int8` 等
- `asr.use_batched`: bool,仅在 device 为 GPU 时生效
- `asr.batch_size`: int
- `asr.vad`: bool,默认 true
- `asr.language`: ISO 639-1 字符串

**缓存键**:调用 `build_cache_key("transcribe", {"audio": hash_file(ctx.paths.audio_wav), "config": hash_json(ctx.config.asr), "backend_version": hash_json(asr_backend_version_payload)})`。`asr_backend_version_payload` 必须包含 ASR backend 名称和转录行为版本;改变输出 schema、是否请求 word-level 识别、是否传 `initial_prompt` 等会影响转录文本或 JSON 形态的行为时,必须 bump 行为版本以让旧缓存失效。

**错误处理**:
- 模型加载失败:`asr/` 包装为 `ASRError`
- 推理过程中 RuntimeError:`asr/` 包装为 `ASRError(...) from e`
- 输出空 segments(音频纯静音或全被 VAD 滤掉):抛 `ASRError("no speech detected")`,不返回空 Transcript(空产物会让下游 stage 报更难定位的错)

### 3.3 Stage 3: segment

**职责**:LLM 看全文转录,输出语义切分点(不重写文本)。

**Input**:`ctx.artifacts.audio.get_transcript()` 拿 `Transcript`。

**Output**:`SegmentList`(schema 见 §4)。落盘 `cache/{input_hash}/segments.json`(经 `atomic_write_json`)。

**实现要点**:
- **单次 LLM 调用,不分块**。对 1-3 小时的转录,主流大模型的 context window 足够装下全文(中文转录约 1 token/字,1 小时 ≈ 20-30k token)
- **短输出原则**(见 §2.1):LLM 输出 `SegmentList` JSON object,形如 `{"markers": [...]}`;每个 marker 含 `id / start / end / topic_hint / boundary_reason`,不复述段内文本
- 用包内模板 `lvnotes/audio_pipeline/prompts/segment.jinja` 渲染 prompt。模板里给 LLM 看的内容:语义切分任务说明 + 全文转录。带 word-level 时间戳的 ASR 段会按每个词的时间戳展开进入 prompt,因此语义切分边界可以落在 ASR segment 内部的任意词级时间点。LLM 必须把每段 `start` 设为本段第一个词的 `start`,把 `end` 设为本段最后一个词的 `end`;没有 word-level 时间戳时才退回使用承载该语义段的 ASR segment `start/end`。
- 通过 `client = for_task(ctx.config, "segment")` 获取 LLM client。LLM JSON 解析 + 1 次修复重试 + schema 校验全部走 `complete_json(client, messages, schema, options, max_repair_retries=1)` helper,不在本 stage 自己写解析重试逻辑
- 业务级不变量校验在 helper 之上额外做:
  1. 将 LLM 输出的每个 `start` / `end` 独立吸附到最近的 transcript 时间戳候选。候选优先来自 `WordTimestamp.start/end`;没有 words 的 ASR segment 使用 `TranscriptSegment.start/end`。若原始边界距离最近候选 `> 0.2s` 记录 warning,`> 2.0s` 抛 `LLMError`。
  2. 校验吸附后的时间戳:`start < end`、相邻 markers 不重叠、所有边界都在 `[0.0, transcript.duration]` 范围内。段间允许存在静音 gap,因为 `end` 表示上一段最后一个词的结束时间,`start` 表示下一段第一个词的开始时间。任一校验失败抛 `LLMError` 含具体不变量名,触发上层重试。

**配置项**(`audio_pipeline.segment.*`):
无。segment stage 只按转录内容和 prompt 做语义切分,不配置目标段数或段时长限制。

**缓存键**:调用 `build_cache_key("segment", {"transcript": hash_json(Transcript), "config": hash_json(segment 配置), "profile": hash_json(LLM profile), "prompt": hash_prompt_template("lvnotes/audio_pipeline/prompts/segment.jinja"), "render": transcript_render_version})`。模板 hash 经归一化(见 §2.6)。改变 transcript 渲染粒度时必须 bump `transcript_render_version`,避免旧 segments 缓存命中。

**错误处理**:
- LLM JSON 解析 / schema 失败:由 `complete_json` helper 内 1 次修复重试覆盖;耗尽抛 `LLMError`
- 业务不变量违反:抛 `LLMError`,不自动"修复"(自动修复会掩盖 prompt 缺陷)
- LLM endpoint 5xx / 限流:`llm/` 内部 `tenacity` 装饰器自动重试,重试耗尽转 `TransportError` / `RateLimitError`

### 3.4 Stage 4: refine

**职责**:整理转录文本,产出带中文标点的清洗内容、摘要、跨段术语呼应。Prompt 模板见 `lvnotes/audio_pipeline/prompts/refine_single.jinja`、`refine_batch.jinja`、`refine.jinja`。

**Input**:`ctx.artifacts.audio.get_transcript()` + `ctx.artifacts.audio.get_segments()`。

**Output**:每段 `RefinedSegment` 落盘到 `cache/{input_hash}/refined/{seg_id:04d}.json`(经 `atomic_write_json`),全部完成后汇总为 `RefinedTranscript`,落盘 `cache/{input_hash}/refined_transcript.json`(同样经 `atomic_write_json`)。

**实现要点**:

*执行模式*:

- `single_call`:一次 LLM 调用返回完整 `RefinedTranscript`
- `batched`:按 `batch_size` 分批,每次返回 `RefinedSegmentList`
- `serial`:逐段返回 `RefinedSegment`,作为最小兜底路径
- `adaptive`:默认模式。先 `single_call`,失败后 `batched`;若某个 batch 失败,只对该 batch 退回 `serial`

*LLM 调用*:通过 `client = for_task(ctx.config, "refine")` 获取 LLM client。JSON 输出解析、修复重试与 schema 校验统一走 `complete_json(client, messages, schema, options, max_repair_retries=1)`。

*当前段文本*:通过 `core.transcript.slice_transcript_text(transcript, start, end)` 按 `SegmentMarker` 时间范围切出 raw transcript。该 helper 优先使用 `TranscriptSegment.words` 的 word-level 时间戳,因此一个 ASR segment 可以被拆分到多个语义段;没有 words 时才退回整段文本。

*标点与书面化*:所有 refine prompt 必须要求 `cleaned_text` 补全中文标点,恢复句界,并使用中文逗号、句号、冒号、括号和列表标记,不得编造原文没有的信息。

*跨段术语呼应*(见 §2.4):prompt 中明确指示 LLM "如果当前段引用了之前段的概念,把对应 segment id 列入 `cross_refs`,并在 `cleaned_text` 中使用 `[[REF:N]]` 形式标注内嵌引用 marker"。

LLM 输出的 `cross_refs` 必须满足:
- `all(ref < current_id for ref in cross_refs)`(只能引用之前的段)
- `cleaned_text` 内出现的所有 `[[REF:N]]` 标记的 N 必须出现在 `cross_refs` 中(双向一致)

校验失败触发该段重试。

*debug*:这是开发期调试能力,不进入配置文件。CLI 实现 refine stage 时应预留 `--debug` 参数:第一段 refine 后暂停,把产物打印给用户审核 + 可手工编辑,再重新加载到累积文本继续后续段。默认关闭,仅显式传 CLI 参数时启用。

*落盘*:无论 `single_call`、`batched` 还是 `serial`,成功产物都逐段落盘到 `refined/{seg_id:04d}.json`,最后汇总写 `refined_transcript.json`。stage 级缓存失效或 `ctx.no_cache is True` 时清空 `refined/*.json` 后重跑。

**配置项**(`audio_pipeline.refine.*`):
- `mode: str` —— 默认 `adaptive`;允许 `adaptive`、`single_call`、`batched`、`serial`
- `batch_size: int` —— 默认 `8`;`batched` / `adaptive` 中每批包含的语义段数

**缓存键**:stage 级,调用 `build_cache_key("refine", {"transcript": hash_json(Transcript), "segments": hash_json(SegmentList), "config": hash_json(refine 配置), "profile": hash_json(LLM profile), "prompt": hash_json(三个 refine prompt hash)})`。命中时直接返回 `RefinedTranscript`,不进入 stage。未命中时按配置模式运行。

**错误处理**:
- `single_call` 校验失败:在 `adaptive` 下退回 `batched`;在 `single_call` 模式下抛 `LLMError`
- batch 校验失败:在 `adaptive` 下该 batch 退回 `serial`;在 `batched` 模式下抛 `LLMError`
- 单段 LLM JSON / schema 失败:`complete_json` 内 1 次修复重试覆盖;耗尽抛 `LLMError`,整个 refine 中止
- LLM 网络/限流失败:`llm/` 内部 `tenacity` 重试,耗尽抛 `LLMError`
- LLM 输出 `cross_refs` 含未来段 id 或不存在的 id,或 `[[REF:N]]` marker 与 `cross_refs` 不一致:抛 `LLMError`,触发该段重试(提示 LLM 不要编造段号)

---

## 4. Schema

本管线相关的所有 dataclass 集中定义在 `core/schemas/audio.py`,通过 `core/schemas/__init__.py` re-export。任何模块**只能从 `core.schemas` 引用、不能在自己模块内重新定义副本**(`coding-standards.md` §2.2)。

所有 dataclass `frozen=True`。`RefinedTranscript` 也保持 `frozen=True`;stage 4 累积构建期间使用局部 `list[RefinedSegment]`,全部完成后一次性构造 `RefinedTranscript`。

Schema 字段不携带配置元信息(见 `coding-standards.md` §2.4)。生成时的配置和模板 hash 等由 `StageOutput.metadata` 携带。

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class AudioExtractResult:
    audio_path: Path                # 抽取后的 wav 路径
    duration: float                 # 秒
    sample_rate: int                # 重采样后的采样率,与 audio_pipeline.extract.sample_rate 一致
    channels: int                   # 重采样后的通道数,与 audio_pipeline.extract.channels 一致
    source_path: Path               # 解析后的绝对本地输入路径(可能是视频也可能是音频)
    source_codec: str               # 原始编码格式标识
    source_sample_rate: int         # 原始采样率
    source_channels: int            # 原始通道数

@dataclass(frozen=True)
class WordTimestamp:
    word: str
    start: float
    end: float
    probability: float              # whisper 给出的置信度,0-1

@dataclass(frozen=True)
class TranscriptSegment:
    id: int                         # 0-based 严格递增
    start: float
    end: float
    text: str
    words: list[WordTimestamp]      # 可能为空(某些后端不输出 word-level)

@dataclass(frozen=True)
class Transcript:
    segments: list[TranscriptSegment]
    language: str                   # ISO 639-1,如 "zh" / "en"
    duration: float                 # ASR 后端报告或 transcribe stage 规范化后的秒数

@dataclass(frozen=True)
class SegmentMarker:
    id: int                         # 0-based 严格递增
    start: float
    end: float
    topic_hint: str                 # LLM 给出的主题提示,refine 阶段可能重写
    boundary_reason: str            # 切分依据简述,调试用,可空字符串

@dataclass(frozen=True)
class SegmentList:
    markers: list[SegmentMarker]

@dataclass(frozen=True)
class RefinedSegment:
    id: int                         # 与 SegmentMarker.id 同 namespace、同值
    start: float
    end: float
    topic: str                      # 整理后的最终主题,可能与 topic_hint 不同
    cleaned_text: str               # 去口水词、补标点、合并断句的整理结果,可含 [[REF:N]] 内部 marker
    summary: str                    # 1-3 句话摘要
    cross_refs: list[int]           # 引用的前置段 id,严格满足 all(ref < self.id)

@dataclass(frozen=True)
class RefinedTranscript:
    segments: list[RefinedSegment]
    language: str
    duration: float
```

### 不变量

跨 stage 的统一约定,所有产物必须满足:

1. 所有 `start < end`
2. 所有 `id` 字段严格递增(0-based)
3. 所有时间戳 `float` 秒、精度毫秒
4. 同一管线内 `SegmentMarker.id` 与 `RefinedSegment.id` 共享 namespace(同值对应同段)
5. `RefinedSegment.cross_refs` 中每个值都 `< self.id` 且对应到存在的 `RefinedSegment.id`;`cleaned_text` 内出现的所有 `[[REF:N]]` 标记 N 必须出现在 `cross_refs` 中(双向一致)
6. `duration` 字段记录对应阶段产物报告的秒数;下游不依赖跨 stage duration 完全一致

LLM 输出导致的业务级不变量违反抛 `LLMError`,触发上层重试或中止。内部构造出的不可能状态才直接抛 `AssertionError`。

---

## 5. Downstream Interfaces — `AudioArtifacts`

**这是音频管线对下游的唯一稳定 API。** 多模管线和合并阶段不允许 `from audio_pipeline import ...`,只能通过 `ctx.artifacts.audio` 取得 `AudioArtifacts`。本管线内部 stage 之间也通过 `ctx.artifacts.audio` 访问上游产物,与外部消费者走同一接口。`ctx.artifacts` 是 `ArtifactBundle`。

```python
class AudioArtifacts:
    def __init__(self, input_hash: str, paths: PipelinePaths) -> None: ...

    # ---- 主产物访问 ----
    def get_extract(self) -> AudioExtractResult: ...
    def get_transcript(self) -> Transcript: ...           # raw whisper 输出
    def get_segments(self) -> SegmentList: ...
    def get_refined(self) -> RefinedTranscript: ...

    # ---- 时间区间反查(多模 describe 高频用)----
    def get_text_at(
        self,
        start: float,
        end: float,
        prefer_raw: bool = False,    # False=用 refined,True=用 raw
        strict: bool = False,        # False=含相交句,True=仅完全包含
        strip_refs: bool = True,     # True 时剥离 [[REF:N]] 内部 marker
    ) -> str: ...
    """返回指定时间区间内的转录文字。

    主要被多模 describe 用于按代表帧时间区间反查讲解内容。

    Args:
        start: 起始时间(秒)
        end: 结束时间(秒)
        prefer_raw: True 时返回原始转录,False 时返回 refined 文本(默认)。
            如需保证最高时间戳精度(refined 因合并段可能损失精度)传 True。
        strict: True 时仅返回完全包含在 [start, end] 内的句子;
            False 时包含与区间相交的所有句子(默认,宁多勿少)。
        strip_refs: True 时从 refined 文本中剥离 [[REF:N]] 内部 marker(默认),
            专为多模 describe 等 VLM 消费场景:VLM 不理解内部 marker。
            设为 False 时保留原 marker(给 merge/assemble 等需要看到 marker 的消费者)。
            prefer_raw=True 时本参数无效(raw transcript 不含 marker)。

    Returns:
        指定区间内的文本,跨多句时用空格连接。

    Raises:
        ValueError: start >= end 或区间超出音频时长。
    """

    def get_segment_at(self, timestamp: float) -> RefinedSegment | None: ...
    """返回包含指定时间点的语义段。落在段间隙时返回 None。"""

    # ---- 元信息 ----
    def get_duration(self) -> float: ...
    def get_language(self) -> str: ...

    # ---- 完成状态(调度层用)----
    def is_complete(self) -> bool: ...
    """音频管线是否已经全部跑完。

    实现:仅检查 refined_transcript.json 的存在性;不做 schema 校验
    (避免被 visual 调度循环 poll 时反复反序列化)。schema 校验在
    get_refined() 首次调用时做并缓存到实例字段。

    多模管线 stage 5 (describe) 启动前需要 audio_artifacts.is_complete() == True。
    等待逻辑由 CLI 调度层(asyncio.Event 等)实现,本类不提供 async 接口。
    """
```

### 实现要点

- **惰性加载**:getter 第一次调用时从磁盘反序列化,结果缓存在实例字段;后续调用直接返回缓存
- **路径走 `core/paths.py`**:本类内部不拼接缓存路径,全部经 `paths` 实例
- **失败时给清晰错误**:getter 在产物文件不存在时抛 `CacheError("AudioArtifacts.get_xxx: <path> not found, stage 'xxx' may not have run")`,让调用方明确是哪一步没跑
- **不暴露 async**:等待逻辑是调度层的事,本类是纯数据访问类。`is_complete()` 只是同步谓词

### 稳定 vs 内部细节

下游可依赖的**稳定契约**:
- `RefinedTranscript` 及其字段
- `SegmentList` 及其字段
- `Transcript`(含 word-level 时间戳)
- 元信息:`duration`、`language`
- 时间戳精度毫秒
- `AudioArtifacts` 的方法签名与语义
- `[[REF:N]]` marker 形态(由 refine 产出,merge 消费;`get_text_at` 默认剥离)

**内部实现细节,下游不应依赖**:
- raw transcript 中 whisper 的内部段切分边界(不同模型版本可能不同)
- 缓存文件具体名字、目录结构
- `audio_pipeline/` 内任何模块的存在
- stage 间中间产物的文件结构(除上面列出的"主产物"外)

---

## 6. Module Layout

```
lvnotes/audio_pipeline/
├── __init__.py
├── extract.py              # Stage 1
├── transcribe.py           # Stage 2
├── segment.py              # Stage 3
├── refine.py               # Stage 4
└── prompts/
    ├── segment.jinja
    └── refine.jinja
```

### 每个 stage 的接口

```python
# extract.py / transcribe.py / segment.py / refine.py
def run(ctx: PipelineContext) -> StageOutput: ...
```

`PipelineContext` 定义在 `core/context.py`,至少包含:
- `ctx.source_path: Path`
- `ctx.config: AppConfig`
- `ctx.paths: PipelinePaths`
- `ctx.artifacts: ArtifactBundle`
- `ctx.input_hash: str`

`StageOutput` 定义在 `core/pipeline.py`,是带缓存元数据的 stage 返回类型。具体 schema 在 `core/pipeline.py` 文档中描述,本文不重复。

### Import 规则速查

| 来源 | 允许? |
|---|---|
| `core/`(schemas / paths / timestamps / slugs / pipeline / cache / config / context / logging / exceptions / artifacts / constants) | ✅ |
| `llm/`(含 `complete_json` / `complete_text` helper) | ✅(仅 stage 3、4) |
| `asr/` | ✅(仅 stage 2) |
| `media/` | ✅(仅 stage 1) |
| `visual_pipeline/` | ❌ |
| `merge/` | ❌ |
| `audio_pipeline/` 内的其他 stage 文件 | ❌(stage 间通过 `ctx.artifacts.audio` 解耦) |
| `openai` / `anthropic` 等 SDK 直接 import | ❌(必须经 `llm/`) |
| `faster_whisper` 直接 import | ❌(必须经 `asr/`) |
| `subprocess` 调 ffmpeg | ❌(必须经 `media/`) |

以上规则由 `.importlinter` 契约文件强制,CI 检查。

---

## 7. Dependencies

### 项目内
- `core/`:`schemas`、`paths`、`timestamps`、`slugs`、`pipeline`、`cache`(含 `atomic_write_*`、`hash_prompt_template`)、`config`、`context`、`logging`、`exceptions`、`artifacts`、`constants`
- `llm/`:stage 3、4 用(含 `complete_json` helper)
- `asr/`:stage 2 用
- `media/`:stage 1 用

### 外部库
本管线**直接**使用:
- `jinja2`:渲染 prompt 模板
- `pydantic`:配置加载(间接,经 `core/config.py`)
- `tenacity`:API 重试(间接,经 `llm/`)

本管线**不直接**使用:
- `faster-whisper`:仅经 `asr/faster_whisper_local.py` 使用
- `openai` / `anthropic`:仅经 `llm/` 使用
- `ffmpeg-python` / `subprocess`:仅经 `media/` 使用

具体依赖清单与版本以 `pyproject.toml` 为准。

---

## 8. Implementation Order

按 `docs/overview.md` §7 的顺序实现:**extract → transcribe → segment → refine**。每个 stage 独立提交独立 review。

### 每个 stage 的"完成"验收标准

按 `coding-standards.md` §19.2 的硬性清单:

1. 主路径用短音频输入跑通端到端
2. 缓存机制工作(再跑一次能命中缓存,跳过该 stage 的实际计算)
3. 错误路径有测试覆盖(至少 1 个错误输入 → 抛预期异常的测试)
4. 类型检查通过(`pyright` 或 `mypy --strict`),`import-linter` 通过
5. 独立 CLI 调用可用(如 `lvnotes extract <input>`、`lvnotes transcribe`)
6. 至少一个其他模块的范例参照(除第一个 stage 外)

任意一条不满足不算完成,不要进下一个 stage。

### `AudioArtifacts` 的渐进实现

在第一个 stage(extract)开发时**同步创建** `AudioArtifacts` 类骨架,初版只支持已落盘产物对应的轻量访问方法。后续 stage 完成时逐步扩展方法:

| 完成 stage | `AudioArtifacts` 新增方法 |
|---|---|
| extract | `get_extract`、`get_duration`、`is_complete`(基于 refined_transcript.json 存在性) |
| transcribe | `get_transcript`、`get_language`(改为从 transcript 读真实值) |
| segment | `get_segments` |
| refine | `get_refined`、`get_text_at`、`get_segment_at` |

这样实现 stage N 时,stage N+1 需要的接口已经定义好(即使返回值未填齐),调度层骨架可以先按完整 API 写。
