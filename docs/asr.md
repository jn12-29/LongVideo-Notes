# ASR Module

`asr/` 模块设计文档。本模块是全项目语音识别的唯一入口,负责把音频文件转成统一的 `Transcript`。第一版只实现本地 `faster_whisper_local` 后端。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`、`docs/audio-pipeline.md`。

文档结构:Overview、Design Considerations、Public API、Backend、Transcript Normalization、Module Layout、Error Handling、Dependencies、Implementation Order。

---

## 1. Overview

`asr/` 对上层只暴露统一接口:

```python
class Transcriber:
    def transcribe(self, audio_path: Path, config: ASRConfig) -> Transcript: ...
```

音频管线 stage 2 只依赖这个接口,不直接 import `faster_whisper`,不接触后端原生 segment 类型。

第一版模块结构:

| 模块 | 职责 |
|---|---|
| `base.py` | 定义 `Transcriber` 协议 |
| `faster_whisper_local.py` | 本地 faster-whisper 实现 |
| `factory.py` | 根据配置创建 transcriber |

`Transcript`、`TranscriptSegment`、`WordTimestamp` 定义在 `core/schemas/audio.py`,由 `core.schemas` re-export。`asr/` 不定义自己的跨模块 transcript schema。

---

## 2. Design Considerations

### 2.1 ASR 唯一入口

所有语音识别调用必须经过 `asr/`。其他模块禁止:

- `import faster_whisper`
- 直接调用 Whisper API
- 返回后端原生类型给上层
- 在音频管线内写模型加载和转录细节

### 2.2 第一版只实现 `faster_whisper_local`

第一版目标是本地可跑、端到端闭环。API 后端位置预留,但不实现。

不做:

- OpenAI Whisper API
- Groq Whisper API
- 注册插件系统
- 动态 backend discovery

`factory.py` 用简单分支即可。

### 2.3 输出必须归一化为 `Transcript`

`faster-whisper` 的 segment / word 类型不能传出 `asr/`。所有后端必须返回 `core.schemas.Transcript`。

理由:

- 后端类型不稳定
- 缓存 JSON 需要项目自己的 schema
- 下游不应感知后端实现
- 将来换 API 后端时不影响音频管线

### 2.4 `condition_on_previous_text=False` 必须显式设置

长音频中一次幻觉可能通过 previous text 影响后续段落。`faster_whisper_local` 必须显式传:

```python
condition_on_previous_text=False
```

不要依赖库默认值。

### 2.5 VAD 默认开启且可配置

静音段容易触发 Whisper 幻觉。VAD 默认 `true`,但保留配置项:

```yaml
asr:
  vad: true
```

默认开启,允许用户在特殊输入上关闭。

### 2.6 `use_batched` 保留开关,GPU 才考虑

`BatchedInferencePipeline` 在 GPU 上可能有明显吞吐收益;CPU 上通常无收益甚至更慢。

规则:

- 配置保留 `asr.use_batched`
- 只有解析后的 device 是 CUDA / GPU 时才考虑启用
- CPU 时即使 `use_batched=true` 也不启用 batched
- 记录 DEBUG 日志说明选择,不报错

---

## 3. Public API

### 3.1 `base.py`

```python
class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, config: ASRConfig) -> Transcript:
        """Transcribe an audio file into the normalized project schema."""
        ...
```

说明:

- 用 `Protocol` 足够,不需要抽象基类
- 公共接口只接收 `Path` 和 `ASRConfig`
- 返回值固定为 `Transcript`
- 不返回 tuple / dict / 后端原生对象

### 3.2 `factory.py`

```python
def create_transcriber(config: ASRConfig) -> Transcriber:
    if config.backend == "faster_whisper_local":
        return FasterWhisperLocalTranscriber()
    raise ASRError(f"unsupported ASR backend: {config.backend}")
```

实现要点:

- `backend` 第一版只接受 `faster_whisper_local`
- 不做注册表
- 不做 entry point 插件
- 不在 factory 里加载模型
- 不缓存全局 transcriber,避免跨模块全局状态

### 3.3 调用方式

音频管线 stage 2 示例:

```python
transcriber = create_transcriber(ctx.config.asr)
transcript = transcriber.transcribe(audio_path, ctx.config.asr)
```

禁止写法:

```python
from faster_whisper import WhisperModel
```

---

## 4. Backend: `faster_whisper_local.py`

### 4.1 职责

本地加载 faster-whisper 模型,执行转录,并归一化为 `Transcript`。

```python
class FasterWhisperLocalTranscriber:
    def transcribe(self, audio_path: Path, config: ASRConfig) -> Transcript:
        ...
```

### 4.2 配置项

来自 `asr.*`:

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `backend` | str | `faster_whisper_local` | 第一版唯一后端 |
| `model` | str | `large-v3` | 模型名或本地路径 |
| `device` | str | `auto` | `auto` / `cuda` / `cpu` |
| `compute_type` | str | `auto` | `float16` / `int8` 等 |
| `use_batched` | bool | `true` | GPU 下才考虑启用 |
| `batch_size` | int | `16` | batched 推理 batch size |
| `vad` | bool | `true` | 默认开启 |
| `language` | str | `zh` | ISO 639-1 语言码 |

配置 schema 在 `core/config.py` 中定义并校验。`asr/` 不自行解析 YAML。

### 4.3 device 与 compute_type

`device="auto"` 时由实现选择:

- CUDA 可用 → `cuda`
- 否则 → `cpu`

`compute_type="auto"` 时由实现选择:

- CUDA → `float16`
- CPU → `int8` 或 faster-whisper 推荐的 CPU 默认值

具体策略实现时以 faster-whisper 官方文档为准,选择结果记 DEBUG 日志。

### 4.4 转录参数

必须显式传:

```python
segments, info = model.transcribe(
    str(audio_path),
    language=config.language,
    word_timestamps=True,
    vad_filter=config.vad,
    condition_on_previous_text=False,
)
```

参数约束:

- `word_timestamps=True`:下游用 word-level 时间戳把一个 ASR segment 精确切入多个语义段
- `vad_filter=config.vad`:默认 true,但可配置
- `condition_on_previous_text=False`:必须显式关闭
- 不传 `initial_prompt`:避免 prompt 文案被模型幻觉进转录正文
- 不启用 speaker diarization,项目第一版明确非目标

转录进度通过消费 `segments` iterable 时更新:总量使用 `info.duration`,每产出一个 faster-whisper segment 后按 `segment.end` 推进 `tqdm` 秒级进度条。该进度表示已产出的音频时间戳范围,不是底层 GPU/CPU 计算百分比;VAD、长静音或长句子会导致进度跳跃。生成器结束后进度补到 100%。

### 4.5 batched 推理

当且仅当同时满足以下条件时使用 batched:

1. `config.use_batched is True`
2. `resolved_device == "cuda"`
3. 当前 faster-whisper 版本提供 `BatchedInferencePipeline`

CPU 上不启用 batched:

```python
use_batched = config.use_batched and resolved_device == "cuda"
```

---

## 5. Transcript Normalization

目标 schema 见 `docs/audio-pipeline.md` §4。

归一化规则:

1. segment `id` 从 0 开始严格递增
2. 所有 `start < end`
3. 时间戳是 `float` 秒数,保留毫秒精度
4. `text` 只做 strip,不重写内容
5. `words` 按时间排序
6. word 缺少 probability 时按后端能力处理;完全不支持时后续 API 后端可返回空列表
7. `language` 优先来自 faster-whisper info,否则用配置值
8. `duration` 来自 faster-whisper info;若不可用,由 transcribe stage 用 `AudioExtractResult.duration` 做一致性校验

`asr/` 不做:

- 删除口水词
- 合并句子
- 补全文结构
- 章节切分
- 术语统一
- 插入 cross refs

如果转录后没有任何有效 segment:

```python
raise ASRError("no speech detected")
```

不要返回空 `Transcript`。

---

## 6. Error Handling

所有 ASR 可预期错误统一包装为 `ASRError`。

```python
try:
    segments, info = model.transcribe(...)
except RuntimeError as exc:
    raise ASRError(f"faster-whisper inference failed: {exc}") from exc
```

| 场景 | 处理 |
|---|---|
| 不支持的 backend | `ASRError` |
| 模型加载失败 | `ASRError` |
| CUDA / compute_type 不可用 | `ASRError` |
| 推理时 RuntimeError | `ASRError` |
| 输出为空 | `ASRError("no speech detected")` |
| 后端输出违反 schema 不变量 | `AssertionError` |

不允许吞异常后返回空 transcript。

日志规则:

- 模型加载开始 / 完成可记 INFO
- resolved device / compute_type / batched 选择可记 DEBUG
- 推理失败前可记 ERROR 后重抛 `ASRError`
- 不打印完整原始转录到日志

---

## 8. Module Layout

```text
lvnotes/asr/
├── __init__.py
├── base.py
├── faster_whisper_local.py
└── factory.py
```

Import 规则:

| 来源 | 允许? |
|---|---|
| `core/config.py` | ✅,用 `ASRConfig` |
| `core/schemas` | ✅,用 `Transcript` 等 |
| `core/exceptions.py` | ✅,用 `ASRError` |
| `faster_whisper` | ✅,仅 `faster_whisper_local.py` |
| `media/` | ❌ |
| `audio_pipeline/` | ❌ |
| `visual_pipeline/` | ❌ |
| `merge/` | ❌ |
| `llm/` | ❌ |

`asr/` 不依赖 `media/`。音频文件是否已经是目标 wav 由音频管线 stage 1 保证。

---

## 9. Dependencies

项目内:

- `core/config.py`: `ASRConfig`
- `core/schemas`: `Transcript`、`TranscriptSegment`、`WordTimestamp`
- `core/exceptions.py`: `ASRError`
- `core/timestamps.py`: 如需毫秒精度处理

外部库:

- `faster-whisper`

系统 / 硬件:

- CPU 可运行但慢
- CUDA 可选
- `use_batched` 只有 GPU 下才考虑启用

具体依赖版本以 `pyproject.toml` 为准。

---

## 10. Implementation Order

建议顺序:

1. 在 `core/config.py` 定义 `ASRConfig`
2. 确认 `core/schemas/audio.py` 已有 transcript schema
3. 实现 `base.py`
4. 实现 `factory.py`
5. 实现 `faster_whisper_local.py`
6. 用真实 30 秒 wav fixture 跑通转录
7. 接入 `audio_pipeline/transcribe.py`

验收标准:

1. `create_transcriber()` 能根据 `backend="faster_whisper_local"` 返回实现
2. 不支持 backend 抛 `ASRError`
3. 真实 wav 输入能返回非空 `Transcript`
4. 输出 segment / word 时间戳满足不变量
5. `condition_on_previous_text=False` 显式存在
6. `vad` 默认 true 且配置可关闭
7. CPU 下即使 `use_batched=true` 也不启用 batched
8. 模型加载失败 / 推理失败包装为 `ASRError`
9. 非 `asr/faster_whisper_local.py` 模块没有 `import faster_whisper`
