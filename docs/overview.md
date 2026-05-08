# LongVideo-Notes

从长视频/音频生成结构化文字笔记的本地工具。面向网课、讲座、播客、板书课等长形式教学内容,自用,CLI 优先,**架构稳健且可演进**。

---

## 1. 项目目标

把一段 1-3 小时的视频或音频转成一份高质量、结构化、可读的 Markdown 笔记。

设计原则上有三条硬约束:

1. **质量优先于速度**。可以慢、可以贵,但产出的笔记要真的能替代手记,不是逐字稿堆砌。
2. **稳健、可断点续跑**。任何阶段挂掉,下次重跑只跑挂掉之后的。中间产物全落盘,可单独检查、可单独重跑。
3. **本地工具,不做产品**。CLI、单进程、文件系统存储。不做 Web UI、不做数据库、不做任务队列、不做用户系统。

非目标(明确不做):

- 实时处理(直播转笔记之类)
- 多说话人区分
- 在线协作 / 笔记同步
- 移动端
- 视频平台的下载器(B 站、YouTube 等)。输入只接受本地文件
- 笔记导出格式扩展(PDF / Word / Notion)。输出仅 Markdown
- 笔记问答 / RAG / 向量索引
- **多语种通用性**。第一版仅在中文输入上验证,prompt 模板写死中文。英文 / 日文输入可能可用但不保证质量,后续再参数化

---

## 2. 支持的功能

### 2.1 输入

- 本地视频文件:`.mp4`, `.mkv`, `.webm`, `.mov` 等 ffmpeg 支持的格式
- 本地音频文件:`.mp3`, `.wav`, `.m4a`, `.flac` 等
- 输入文件路径通过 CLI 参数提供

### 2.2 处理模式

- **纯音频模式**:默认模式。只走音频管线,产出基于讲解内容的笔记。适用于音频文件、或视频但不需要画面信息的场景。
- **多模模式**:显式传 `--mm` 时启用。音频管线 + 多模管线并行,画面和讲解联合生成笔记。适用于含 PPT / 板书 / 代码演示的教学视频。

模式由 CLI 参数优先决定。输入是音频文件时强制纯音频模式;输入是视频文件时默认纯音频模式,显式传 `--mm` 才启用多模。配置中的 `visual_pipeline.enabled` 只作为多模能力开关:未传 `--mm` 时不会启动多模;传了 `--mm` 但该配置为 `false` 时直接报配置错误,避免用户以为已经启用画面处理。

### 2.3 输出

- 一份 Markdown 笔记 (`note.md`),含:
  - 自动生成的章节结构
  - 每章正文(清洗后的讲解内容 + 视觉内容描述)
  - 关键画面截图(多模模式下)
  - 时间戳标注(可选 `?t=` 跳转格式)
  - 章节内交叉引用:前文讲过的概念在后文出现时自动渲染为指向首次出现位置的链接
- 完整的中间产物缓存目录,用于调试和重跑

### 2.4 LLM 与 ASR 后端

- **LLM**:通过统一的 provider 抽象层,支持任意 OpenAI Chat 兼容 endpoint(OpenAI、OpenRouter、本地 vLLM、各类反代、DeepSeek、Qwen 等)、OpenAI Responses API、Anthropic Messages API。配置文件中可为不同任务(切分、整理、视觉判断、视觉描述、大纲、分章)配置不同的 profile。
- **ASR**:默认本地 faster-whisper(支持 GPU 批量推理)。抽象接口预留,将来可加 OpenAI / Groq / 国内厂商的 Whisper API 实现。

---

## 3. 整体流程

两条管线 + 一个合并阶段。两条管线大部分阶段并行,但**多模管线的最后一步(VLM 详细描述)依赖音频管线的 refined 产物**——画面理解需要结合该时段讲师的讲解文字。纯音频模式下只跑音频管线和合并阶段。

![longvideo_notes_pipeline](./assets/longvideo_notes_pipeline.svg)

图中实线为必经路径(纯音频模式即可完成);紫色虚线框内为多模管线,仅在多模模式下启用,其入口与出口箭头都是虚线。橙色虚线 `refined_transcript` 表示跨管线依赖:多模管线的 stage 5 (describe) 需要音频管线 stage 4 (refine) 的产物。

调度上的含义:

- 音频管线 stage 1-4 顺序执行
- 多模管线 stage 1-4 顺序执行,**与音频管线并行**(互不依赖)
- 多模管线 stage 5(describe)需要音频管线 stage 4(refine)的产物,因此:
  - 如果多模管线 stage 1-4 比音频管线 stage 1-4 快,stage 5 在音频管线完成后才能启动
  - 反之,多模管线 stage 4 完成时音频管线已经好了,stage 5 直接接上
- 合并阶段在两条管线全部完成后启动
- 纯音频模式下多模管线整体跳过,合并阶段直接消费 `AudioArtifacts`

### 3.1 音频管线(4 个 stage)

| Stage | 名称       | 工具           | 产物                                                       |
| ----- | ---------- | -------------- | ---------------------------------------------------------- |
| 1     | extract    | ffmpeg         | `audio.wav` (16kHz mono) + 元信息                          |
| 2     | transcribe | faster-whisper | `transcript_raw.json` (带 word-level 时间戳)               |
| 3     | segment    | LLM            | `segments.json` (语义切分点)                               |
| 4     | refine     | LLM            | `refined_transcript.json` (按段清洗 + 摘要 + 跨段术语呼应) |

Stage 3 是 LLM 一次性看全文输出切分点(短输出,避免长输出衰减)。Stage 4 是 **串行续写式整理**:每段调用 LLM 时,context 包含全文原始转录 + 已经完成的前 K-1 段整理结果,让 LLM 看着前面段的样子续写第 K 段,保证术语、风格、详略一致。利用 prompt cache 降本。整理时还会识别当前段引用的前文概念,记录为段间的 `cross_refs`,并以内部 marker `[[REF:N]]` 形式嵌入清洗文本;最终笔记由合并阶段据此渲染章节内交叉引用。

### 3.2 多模管线(5 个 stage,仅多模模式启用)

| Stage | 名称     | 工具           | 产物                                                      |
| ----- | -------- | -------------- | --------------------------------------------------------- |
| 1     | sample   | ffmpeg         | 1fps 采样帧                                               |
| 2     | cluster  | pHash + 直方图 | 视觉段(连续渐变合并)                                      |
| 3     | judge    | 弱 VLM         | 每段的 medium / is_meaningful / evolution / richest_frame |
| 4     | select   | 拉普拉斯方差   | 每段 1 张代表帧(无意义段丢弃)                             |
| 5     | describe | 强 VLM + 转录  | 每个代表帧的详细图文描述                                  |

Stage 2 用滑动窗口聚类(双阈值 + 跟段首累积比对),把相邻渐变帧合并为同一段。Stage 3 给每段传首/中/末三帧让弱 VLM 判断段的语义属性。Stage 5 是图文联合理解:把代表帧 + 该段时间区间的转录文字一起喂给强 VLM,让它结合两者输出详细描述。

### 3.3 合并阶段

| 步骤     | 工具       | 产物                                                  |
| -------- | ---------- | ----------------------------------------------------- |
| unify    | 纯逻辑     | `ContentBlock` 序列(按时间排序,含转录 + 可选视觉信息) |
| outline  | LLM        | 章节结构 (`outline.json`)                             |
| section  | LLM (并发) | 每章 Markdown (`sections/*.md`),保留内部 marker       |
| assemble | 纯逻辑     | 最终 `note.md`,marker 替换为人类可读形态              |

`ContentBlock` 是统一抽象:纯音频模式下所有 block 没有视觉字段,多模模式下有视觉的 block 含画面+描述。下游 outline / section 不区分两种模式。

时间戳和跨段引用全程使用内部 marker `[[TS:<seconds>]]` / `[[REF:<id>]]` 在管线内传递,只在 assemble 阶段做最终人类可读渲染。

---

## 4. 模块结构

```
longvideo-notes/
├── pyproject.toml              (计划创建)
├── .importlinter                (计划创建;依赖契约,CI 强制)
├── config.example.yaml           (计划创建的完整配置示例)
├── README.md                    (项目入口与文档索引)

│
├── docs/
│   ├── audio-pipeline.md        (音频管线详细设计)
│   ├── visual-pipeline.md       (多模管线详细设计,第一版可占位)
│   ├── merge.md                 (合并阶段详细设计)
│   ├── coding-standards.md      (开发规范,所有模块必读)
│   ├── overview.md              (本文档)
│   └── assets/
│       └── longvideo_notes_pipeline.svg  (流程图)
│
├── lvnotes/
│   ├── __init__.py
│   ├── __main__.py              (模块入口)
│   │
│   ├── core/                    (框架层,被所有模块依赖)
│   │   ├── schemas/             (跨模块 dataclass 包)
│   │   │   ├── __init__.py      (re-export 全部)
│   │   │   ├── audio.py
│   │   │   ├── visual.py
│   │   │   └── merge.py
│   │   ├── artifacts.py         (AudioArtifacts / VisualArtifacts 访问接口)
│   │   ├── paths.py             (缓存路径常量)
│   │   ├── timestamps.py        (时间戳工具,全项目唯一来源)
│   │   ├── slugs.py             (slug / chapter anchor 唯一来源)
│   │   ├── pipeline.py          (Stage 抽象 + StageOutput)
│   │   ├── cache.py             (内容寻址缓存 + atomic_write_* + hash_prompt_template)
│   │   ├── config.py            (pydantic-settings 配置加载,含 TaskName 封闭枚举)
│   │   ├── context.py           (PipelineContext)
│   │   ├── exceptions.py
│   │   ├── constants.py
│   │   └── logging.py           (logger 配置)
│   │
│   ├── media/                   (ffmpeg 唯一入口)
│   │   ├── probe.py
│   │   ├── audio.py
│   │   └── video.py
│   │
│   ├── llm/                     (LLM provider 抽象,唯一入口)
│   │   ├── base.py
│   │   ├── types.py
│   │   ├── openai_chat.py
│   │   ├── openai_responses.py
│   │   ├── anthropic.py
│   │   ├── factory.py
│   │   ├── json_helper.py       (complete_json:LLM JSON 解析 + 1 次修复重试)
│   │   └── budget.py            (token / 成本预估)
│   │
│   ├── asr/                     (语音转录抽象 + 实现)
│   │   ├── base.py
│   │   ├── faster_whisper_local.py
│   │   └── factory.py
│   │
│   ├── audio_pipeline/          (音频管线,4 个 stage)
│   │   ├── extract.py
│   │   ├── transcribe.py
│   │   ├── segment.py
│   │   ├── refine.py
│   │   └── prompts/
│   │       ├── segment.jinja
│   │       └── refine.jinja
│   │
│   ├── visual_pipeline/         (多模管线,第一版可空目录)
│   │   ├── sample.py
│   │   ├── cluster.py
│   │   ├── judge.py
│   │   ├── select.py
│   │   ├── describe.py
│   │   └── prompts/
│   │
│   ├── merge/                   (合并阶段)
│   │   ├── unify.py
│   │   ├── outline.py
│   │   ├── section.py
│   │   ├── assemble.py
│   │   └── prompts/
│   │
│   └── cli/
│       └── app.py               (typer/click 入口)
│
├── tests/
│   ├── fixtures/
│   │   ├── prepare.py           (下载 + 转码生成本地 fixture)
│   │   └── MANIFEST.txt         (来源 / license / SHA256)
│   ├── unit/
│   └── integration/
│
└── cache/                       (运行时产生,gitignore)
    └── {input_hash}/
        ├── audio/
        ├── visual/              (多模模式下)
        ├── transcript_raw.json
        ├── segments.json
        ├── refined/
        ├── content_blocks.json
        ├── outline.json
        ├── sections/
        └── note.md
```

---

## 5. 各模块设计原则

### 5.1 `core/` —— 框架层

**职责**:提供所有其他模块共用的基础设施。包括数据 schema、缓存机制、配置加载、日志、路径常量、时间戳工具、slug 工具、原子写入工具。

**原则**:

- 不写业务。任何模块特定的逻辑都不应在 `core/` 出现。
- **所有跨模块共享的数据类型集中在 `core/schemas/`**,禁止在其他模块定义副本。
- **所有路径常量集中在 `core/paths.py`**,禁止在其他模块拼接缓存路径。
- **所有时间戳格式化/解析集中在 `core/timestamps.py`**。
- **所有 slug / anchor 生成集中在 `core/slugs.py`**。
- **所有原子文件写入集中在 `core/cache.py` 的 `atomic_write_*`**。

**接口契约**:

- 时间戳全项目使用 `float` 秒数,精度毫秒。仅在 CLI 输出和 Markdown 渲染时转换为 `mm:ss` / `hh:mm:ss`。
- 所有跨阶段产物的 schema 在此定义,stage 实现时只引用、不修改。
- Schema 字段只放"内容",不放配置元信息。配置元信息由 `StageOutput.metadata` 携带。

### 5.2 `media/` —— ffmpeg 唯一入口

**职责**:所有 ffmpeg / ffprobe 调用。

**原则**:

- 全项目唯一允许 `subprocess` 调用 ffmpeg 的地方。其他模块需要 ffmpeg 操作时调用 `media/` 提供的函数。
- 函数粒度细:抽 wav 是一个函数、抽帧是一个函数、读元数据是一个函数。不做"万能 ffmpeg 包装器"。

### 5.3 `llm/` —— LLM provider 抽象

**职责**:统一所有 LLM 调用,屏蔽不同协议(OpenAI Chat / Responses / Anthropic Messages)和不同 endpoint 的差异。

**原则**:

- **全项目唯一允许 `import openai` / `import anthropic` 的地方**。其他模块通过 `llm.get_client(profile_name)` 或 `llm.for_task(task_name)` 获取统一接口的客户端。
- 实现按"协议"切,不按"服务商"切:
  - `OpenAIChatClient` 覆盖 OpenAI、OpenRouter、本地 vLLM、DeepSeek、Qwen、各类反代等所有 OpenAI Chat 兼容 endpoint
  - `OpenAIResponsesClient` 给需要 Responses API 的模型
  - `AnthropicClient` 给 Anthropic 原生 API
- 配置中通过 profile 区分 endpoint,profile 包含:协议、base_url、api_key 环境变量名、model、capabilities flags(vision / prompt_cache / json_mode / max_context)。
- 任务 → profile 映射在配置中:`tasks.segment: gpt5_main`、`tasks.slide_judge: weak_vlm` 等。代码用 `llm.for_task("segment")` 获取,配置改 profile 不改代码。

**JSON 输出 helper**:`llm/json_helper.py` 提供 `complete_json(messages, schema, max_repair_retries=1) -> dict`,统一处理"LLM 输出 JSON 解析 + 1 次修复重试 + schema 校验"。所有需要 LLM 输出结构化数据的 stage(segment / outline / 等)走此 helper,不在 stage 内自己写解析重试逻辑。

**接口契约**:

- `LLMClient.complete(messages, **kwargs) -> LLMResponse`
- `LLMClient.stream(messages, **kwargs) -> Iterator[str]`
- 错误归一化:所有实现统一抛 `AuthError` / `RateLimitError` / `ContextLengthError` / `TransportError`,调用方只 catch 这些。

### 5.4 `asr/` —— ASR 抽象

**职责**:语音转录的统一接口。

**原则**:

- 输出统一的 `Transcript` dataclass(在 `core/schemas/audio.py` 定义),下游不感知具体后端。
- 第一版只实现 `faster_whisper_local`,含 batched 推理选项。
- 接口预留 API 后端(OpenAI / Groq)的实现位置,第一版不做。

### 5.5 `audio_pipeline/` —— 音频管线

**职责**:实现音频处理的 4 个 stage(extract / transcribe / segment / refine)。

**原则**:

- 每个 stage 一个文件。每个文件暴露一个主函数 `run(ctx) -> StageOutput`。
- Stage 的输入输出全部用 `core/schemas/` 中定义的 dataclass。
- LLM 调用走 `llm/`,ffmpeg 调用走 `media/`,ASR 调用走 `asr/`。本模块不直接 import 任何第三方 SDK。
- Prompt 模板放在 `prompts/` 子目录,用 Jinja2,跟代码分离。
- **不引用 `visual_pipeline/` 或 `merge/`**。本模块完全独立。

**对外接口**:通过 `core/artifacts.py` 的 `AudioArtifacts` 类对外提供产物访问。其他模块(多模管线、合并阶段)只通过这个接口读音频产物,不直接读文件、不直接 import `audio_pipeline`。

### 5.6 `visual_pipeline/` —— 多模管线

**职责**:实现多模处理的 5 个 stage(sample / cluster / judge / select / describe)。

**原则**:

- 同 `audio_pipeline/` 的原则。
- 通过 `AudioArtifacts` 接口读音频管线的产物(仅 stage 5 describe 需要)。
- **不引用 `audio_pipeline/` 内部模块**,只用 `AudioArtifacts`。
- 第一版可以只创建目录和占位文件,并在 `docs/visual-pipeline.md` 中声明"未实现"。

**对外接口**:`VisualArtifacts` 类,跟 `AudioArtifacts` 对称。

### 5.7 `merge/` —— 合并与笔记生成

**职责**:把音频和(可选的)多模产物合并为 `ContentBlock` 序列,调用 LLM 生成大纲、分章生成、组装最终笔记。

**原则**:

- 通过 `AudioArtifacts` / `VisualArtifacts` 读上游产物,不直接读文件。
- 同样不直接 import 上游管线模块。
- `unify.py` 处理两种模式(纯音频 / 多模)的合并逻辑,下游 outline / section / assemble 对模式无感。
- 时间戳与跨段引用全程使用内部 marker(`[[TS:...]]` / `[[REF:...]]`),只在 `assemble.py` 做最终人类可读替换。

### 5.8 `cli/` —— 入口

**职责**:CLI 命令解析、调度两条管线、错误处理、进度显示。

**原则**:

- 用 typer 或 click,子命令风格:`lvnotes run`、`lvnotes inspect`、以及顶层 stage 命令(如 `lvnotes extract`、`lvnotes transcribe`、`lvnotes outline`、`lvnotes assemble`)。
- 调度结构按双管线设计。调度层可以并发执行音频管线和多模管线,但每个 stage 对外仍固定暴露同步 `run(ctx) -> StageOutput` 接口;多模管线 disabled 时直接跳过。
- 不在 CLI 写业务逻辑。CLI 只负责"解析参数 → 配置 Pipeline → 启动 → 处理输出"。

---

## 6. 关键架构约定

这几条是项目的"宪法",所有模块必须遵守。`coding-standards.md` 会把它们落到具体的 do/don't。

1. **单向数据流**。下游模块不回头读上游的"上一阶段"产物。比如 `merge/` 只读 `AudioArtifacts.get_refined()`,不读 `transcript_raw.json`。
2. **所有阶段产物落 JSON / 文本文件,不用 pickle**。手工可读、可改、可单独重跑。所有跨模块产物文件的写入必须经 `core/cache.py` 的 `atomic_write_*` 唯一入口,避免进程中断留下半文件。
3. **缓存按"内容 hash + 配置 hash + stage 名"自动失效**。改 prompt、改模型、改阈值会自动让对应 stage 缓存失效,无需手动 invalidate。Prompt 模板的 hash 经 `hash_prompt_template` 做最小归一化(去 Jinja 注释 + collapse 空白),避免格式化无关变更触发重跑。
4. **跨模块数据用 dataclass / pydantic 模型,禁用 `dict[str, Any]`**。Schema 字段只放内容,不放配置元信息。
5. **唯一入口规则**:ffmpeg 经 `media/`,LLM 经 `llm/`,ASR 经 `asr/`,缓存路径经 `core/paths.py`,时间戳经 `core/timestamps.py`,slug / anchor 经 `core/slugs.py`,跨模块产物访问经 `core/artifacts.py`,**原子写入与模板 hash 经 `core/cache.py`**。其他模块不绕开这些入口。
6. **音频管线和多模管线相互不可见**。两者通过 `core/` 的 `AudioArtifacts` / `VisualArtifacts` 解耦。
7. **配置即数据**。所有阈值、模型名、超时、prompt 路径、profile 名通过配置文件传入,代码中不出现魔法数字。
8. **没有数据库、没有任务队列、没有 Web 服务器**。文件系统 + 单进程足够。
9. **管线内部用 marker,人类形态在 assemble**。时间戳跳转、跨段引用链接,都用内部 marker(`[[TS:...]]` / `[[REF:...]]`)在 LLM 输出与中间产物中传递,assemble 阶段做唯一一次人类可读替换。LLM 不直接生成最终链接 URL。

---

## 7. 实现优先级

按这个顺序写,每一步都端到端可跑。

1. **基础设施**:`core/`(schemas、paths、timestamps、slugs、pipeline、cache、config、logging、context)+ `media/probe.py` + `media/audio.py` + `cli/app.py` 骨架 + `.importlinter` 契约。
2. **LLM 抽象**:`llm/`(base、types、openai_chat、json_helper、factory)。第一版只实现 OpenAI Chat 协议,覆盖 90% 场景。
3. **ASR 抽象**:`asr/`(base、faster_whisper_local、factory)。
4. **音频管线**:按 stage 顺序 extract → transcribe → segment → refine,每个 stage 独立提交独立 review。
5. **合并阶段(简化版)**:`merge/unify.py`(纯音频模式直接转换)+ `outline` + `section` + `assemble`。打通"音频文件 → Markdown 笔记"端到端。
6. **测试与打磨**:用真实音频跑全流程,调 prompt、调阈值、修 bug。
7. **多模管线**:5 个 stage 顺序实现。
8. **合并阶段升级**:`merge/unify.py` 支持双管线合并。
9. **LLM 抽象扩展**:增加 `openai_responses` / `anthropic` 协议实现。

每一步完成的"验收标准"是:

- 该 stage 单独可通过 CLI 调用
- 缓存命中正常
- 至少一个真实输入跑通端到端
- 单元测试覆盖主路径和错误路径

---

## 8. 配置文件示例

完整配置示例计划放在 `config.example.yaml`,当前这里只给框架。

`tasks.*` 是封闭枚举,可用任务名集中在 `core/config.py` 的 `TaskName` 字面量类型中(`segment` / `refine` / `outline` / `section` / `slide_judge` / `slide_describe`)。新增任务时**先**改 `TaskName` 再用,运行时配置含未知任务名直接 `ConfigError` 退出。

```yaml
project:
  cache_dir: ./cache
  output_dir: ./output # 最终 note.md 写入这里;cache 内 note.md 作为可复查的中间产物

llm:
  active_default: gpt5_main
  profiles:
    gpt5_main:
      protocol: openai_chat
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-5
      capabilities: [vision, prompt_cache, json_mode]
      max_context: 1000000
    weak_vlm:
      protocol: openai_chat
      base_url: https://openrouter.ai/api/v1
      api_key_env: OPENROUTER_API_KEY
      model: google/gemini-2.5-flash
      capabilities: [vision]

tasks:
  segment: gpt5_main
  refine: gpt5_main
  outline: gpt5_main
  section: gpt5_main
  slide_judge: weak_vlm # 多模管线用
  slide_describe: gpt5_main # 多模管线用

asr:
  backend: faster_whisper_local
  model: large-v3
  device: auto
  compute_type: auto
  use_batched: true
  batch_size: 16
  vad: true
  language: zh

audio_pipeline:
  enabled: true
  extract:
    sample_rate: 16000 # 重采样目标,可选 8000/16000/22050/32000/44100/48000
    channels: 1 # 重采样目标,1 (mono) 或 2 (stereo)
  segment:
    target_count_hint: "15-40"
    min_segment_seconds: 30
    max_segment_seconds: 480
  refine:
    review_first: false
    sliding_window_token_threshold: 30000
    sliding_window_recent_segments: 5

visual_pipeline:
  enabled: true # 多模能力开关;默认配置建议为 true,但只有显式传 --mm 才会实际启动
  sample:
    fps: 1
  cluster:
    phash_low_threshold: 5
    phash_high_threshold: 15
  # ... 其他 stage 配置

merge:
  outline:
    target_chapter_count_hint: "5-12"
  section:
    concurrent_calls: 5
    timestamp_format: "[{hms}]" # 渲染形态;支持 {hms} {mmss} {seconds} {seconds_int}
    # LLM 输出走内部 marker [[TS:seconds]],由 assemble 替换
    include_visuals: true # 多模模式下章节内嵌入视觉描述与图片
  assemble:
    include_toc: true # 顶部目录
    include_metadata: true # YAML frontmatter(输入路径、时长、模式等)
    video_url_template: null # 默认时间戳为纯文本;配置后渲染为跳转链接
    # 例: "file://{video_path}?t={seconds_int}"
    top_title: null # null 时从输入文件名派生
```

---

## 9. 依赖

项目尚未进入代码实现与环境配置阶段,依赖清单暂不固定。后续创建 `pyproject.toml` 时再以实际实现为准补齐运行时依赖与 dev 依赖。

系统层面预计需要 `ffmpeg`,但安装与版本要求等实现阶段再确认。

---

## 10. 文档索引

- `docs/coding-standards.md` —— 开发规范、工程原则、针对 coding agent 的注意事项。**所有写代码前必读**。
- `docs/audio-pipeline.md` —— 音频管线 4 个 stage 的详细设计与接口契约。
- `docs/visual-pipeline.md` —— 多模管线 5 个 stage 的详细设计(第一版可仅占位)。
- `docs/merge.md` —— 合并阶段的详细设计。

每份管线文档都按以下结构组织:Overview、Design Considerations(设计要点)、Stages(含每个 stage 的 input/output schema、实现要点、配置项、缓存规则、错误处理)、Schema、Downstream Interfaces、Module Layout、Dependencies、Implementation Order。
