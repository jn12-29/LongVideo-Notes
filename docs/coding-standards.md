# Coding Standards

本文档定义 LongVideo-Notes 项目的开发规范。**所有为本项目写代码前必读**,包括人类开发者和 AI coding agent。

文档分两部分:
- **第一部分**:通用工程原则。所有代码必须遵守。
- **第二部分**:针对 AI coding agent 的特殊注意事项。Agent 写代码时必须自我对照。

---

# 第一部分 通用工程原则

## 1. 类型标注

### 1.1 全覆盖

所有公开函数、方法、dataclass 字段必须有完整的类型标注。私有辅助函数也建议加。

```python
# 正确
def transcribe(audio_path: Path, config: TranscribeConfig) -> Transcript: ...

# 错误
def transcribe(audio_path, config): ...
```

### 1.2 使用现代写法(Python 3.10+)

```python
# 正确
def f(x: list[int], y: str | None = None) -> dict[str, int]: ...

# 错误(旧式)
from typing import List, Optional, Dict
def f(x: List[int], y: Optional[str] = None) -> Dict[str, int]: ...
```

### 1.3 不允许 `Any`

跨模块接口、公共 API、dataclass 字段不允许 `typing.Any`。如果数据形状不确定,定义 union 类型或使用泛型。

仅允许 `Any` 的场景:第三方库返回的 `dict` 在解析为本项目 dataclass 之前的中间变量,且仅在局部作用域内。

### 1.4 类型检查与依赖检查在 CI 中强制

项目根目录配置类型检查工具与 `import-linter`(契约见项目根目录 `.importlinter`,落实 `docs/overview.md` §6 的单向依赖与唯一入口规则)。CI 阶段任一不通过则 PR 不能合并。

## 2. 数据结构

### 2.1 跨模块数据用 dataclass / pydantic 模型

跨阶段、跨模块传递的数据必须有显式的 schema 定义。

```python
# 正确
@dataclass
class TranscriptSegment:
    id: int
    start: float
    end: float
    text: str
    words: list[WordTimestamp]

def transcribe(...) -> list[TranscriptSegment]: ...

# 错误
def transcribe(...) -> list[dict]: ...
def transcribe(...) -> dict[str, Any]: ...
```

### 2.2 Schema 集中定义

所有跨模块共享的 dataclass 集中在 `core/schemas/` 包下(`audio.py` / `visual.py` / `merge.py`,由 `__init__.py` 统一 re-export)。其他模块**只能从 `core.schemas` 引用,不能在自己模块内重新定义副本**。

模块内部使用的临时数据结构(不传出模块边界)可以在该模块本地定义。

### 2.3 不可变优先

dataclass 默认 `frozen=True` 或使用 `pydantic.BaseModel` 的不可变模式。需要修改时构造新实例(用 `dataclasses.replace`)。

需要累积构建的数据应优先使用局部可变列表,完成后一次性构造 frozen dataclass。

### 2.4 Schema 不携带配置信息

dataclass 字段只放"内容",不放生成时的配置信息(如 `config_hash`、`runtime_hint` 这类)。配置元信息由 `StageOutput` 的 sidecar metadata 携带。理由:缓存机制本身就按"内容 + 配置 hash"索引,命中即等价于配置匹配,在 data schema 内重复声明是双源事实。

## 3. 错误处理

### 3.1 三类错误,三种处理

| 类别 | 处理方式 |
|---|---|
| 可预期的业务错误(API 限流、文件不存在、配置缺失、外部服务错误) | 抛出项目定义的专用异常类,调用方可按需 catch |
| 不可预期的内部错误(bug、不变量违反、断言失败) | 直接抛 / `raise AssertionError`,让顶层 logging 记录并退出 |
| 临时性错误(网络抖动、API 偶发 5xx) | 用 `tenacity` 装饰器自动重试,重试耗尽后转为可预期错误 |

### 3.2 异常类层次

项目定义的异常集中在 `core/exceptions.py`:

```python
class LVNotesError(Exception):
    """所有项目异常的基类。"""

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

具体子类按需添加,但都从 `LVNotesError` 继承。

### 3.3 严禁的反模式

```python
# 严禁:吞异常
try:
    do_something()
except Exception:
    pass

# 严禁:吞异常 + 假装成功
try:
    result = do_something()
except Exception:
    result = None  # 调用方完全不知道失败了

# 严禁:用字符串判断异常类型
try:
    do_something()
except Exception as e:
    if "rate limit" in str(e):
        ...

# 严禁:catch 后只 print 不重抛
try:
    do_something()
except Exception as e:
    print(f"出错了: {e}")
```

### 3.4 允许的模式

```python
# 允许:catch 特定可预期异常并处理
try:
    result = llm_client.complete(messages)
except RateLimitError:
    log.warning("rate limited, sleeping 60s")
    time.sleep(60)
    result = llm_client.complete(messages)

# 允许:catch 后包装为更高层异常
try:
    transcript = whisper_model.transcribe(audio)
except RuntimeError as e:
    raise ASRError(f"faster-whisper inference failed: {e}") from e
```

## 4. 日志

### 4.1 用 logging,不用 print

每个模块顶部:

```python
import logging
log = logging.getLogger(__name__)
```

模块内部用 `log.info()` / `log.debug()` / `log.warning()` / `log.error()`。

仅 CLI 入口允许 `print`(用于直接给用户的输出,比如进度条、最终结果)。

### 4.2 日志级别约定

| 级别 | 用途 |
|---|---|
| DEBUG | 详细的内部状态(变量值、中间结果),仅文件日志 |
| INFO | 主要执行节点("开始 Stage X"、"缓存命中"、"已完成第 K 段") |
| WARNING | 异常但可继续(重试、降级、配置缺省) |
| ERROR | 失败(即将抛异常前记录) |
| CRITICAL | 极少用,进程级故障 |

### 4.3 日志内容规范

- 不打印敏感信息(API key、完整 token、原始音频内容)
- 日志条目应可被 grep:包含可识别的 stage 名 / 阶段名 / input_hash
- 长内容(如 prompt 全文)打 DEBUG,且写入文件而非控制台

## 5. 配置

### 5.1 配置即数据

代码中**不出现魔法数字、阈值、模型名、URL、超时秒数**。这些必须从配置文件读入。

```python
# 错误
if phash_distance > 15:
    new_segment()

# 正确
if phash_distance > config.visual_pipeline.filter.phash_threshold:
    keep_frame()
```

允许的字面量:

- 标志值(`-1`、`None`、`""`)
- 数学常量(`pi`、`e`,但放在 `core/constants.py`)
- 单元测试中的具体测试数据

### 5.2 配置 schema 用 pydantic 定义

所有配置字段在 `core/config.py` 中以 pydantic 模型定义,包括:

- 类型
- 默认值(如有)
- `Field(description=...)` 字段说明(用于自动生成文档)
- 验证逻辑(`@field_validator`)

### 5.3 配置错误尽早失败

启动时一次性加载并验证完整配置,验证失败立即报错退出,不要在运行中途才发现配置缺失。

## 6. 模块边界

### 6.1 唯一入口规则

| 资源 | 唯一入口 |
|---|---|
| ffmpeg / ffprobe | `media/` |
| LLM 调用(OpenAI / Anthropic / 任何兼容 endpoint) | `llm/` |
| ASR | `asr/` |
| 缓存路径 | `core/paths.py` |
| 时间戳格式化 | `core/timestamps.py` |
| 跨管线产物访问 | `core/artifacts.py` |
| 配置 | `core/config.py` |
| Slug / anchor 生成 | `core/slugs.py` |
| 原子文件写入(JSON / 文本 / 字节) | `core/cache.py`:`atomic_write_json` / `atomic_write_text` / `atomic_write_bytes` |
| Prompt 模板加载与归一化 hash | `core/cache.py`:`hash_prompt_template` |

其他模块**禁止绕开这些入口**直接调用 ffmpeg / 第三方 SDK / 拼接路径。

进程中途被中断不能留下半文件。所有"按段/按章累积落盘"的产物(refine 的 `refined/*.json`、section 的 `sections/*.md`,以及任何 stage 主产物)写入必须经 `atomic_write_*`,内部实现是 `write to .tmp + fsync + os.replace`。直接 `open(..., "w")` + `json.dump()` 在跨模块产物路径下视为违规。

### 6.2 单向依赖

```
audio_pipeline/   ─┐
                   ├──> core/, llm/, asr/, media/
visual_pipeline/  ─┤
                   │
merge/            ─┘──> core/ (含 artifacts)

cli/              ───> 上述所有

audio_pipeline/   ✗  禁止 import visual_pipeline/
visual_pipeline/  ✗  禁止 import audio_pipeline/
merge/            ✗  禁止 import audio_pipeline/ 或 visual_pipeline/ 内部
                       (只能通过 core/artifacts 访问)
```

如果你发现"我必须从 audio_pipeline 反向 import core 之外的东西",**这是设计错了,停下来重新审视,不要硬写**。

### 6.3 文件大小

- 单文件 ≤ 400 行(含注释、不含空行)
- 单函数 ≤ 50 行(不含 docstring)

例外:
- 数据 schema 文件(`core/schemas/*.py`)可以更长
- Prompt 模板字符串不计入函数行数
- 测试文件不受限

超过上限说明该拆。

## 7. 命名

### 7.1 一般约定

- 模块名:小写 + 下划线(`audio_pipeline`)
- 类名:CamelCase(`AudioArtifacts`、`TranscriptSegment`)
- 函数 / 变量名:小写 + 下划线(`get_text_at`)
- 常量:全大写 + 下划线(`DEFAULT_SAMPLE_RATE`)
- 私有:单下划线前缀(`_internal_helper`)

### 7.2 语义清晰

变量名应能猜出含义。

```python
# 错误
def f(x, y, z):
    return x[y:z]

# 正确
def slice_text_by_time(transcript: str, start: float, end: float) -> str: ...
```

避免缩写,除非是项目内或行业内通用术语:

| 允许的缩写 | 含义 |
|---|---|
| `ts` | timestamp |
| `cfg` | config(仅局部变量) |
| `ctx` | context |
| `vlm` | vision language model |
| `asr` | automatic speech recognition |
| `llm` | large language model |
| `vad` | voice activity detection |

不允许:`tmp`、`val`、`data`、`info`、`obj` 等无意义命名。

## 8. 函数设计

### 8.1 纯函数优先

能写成纯函数的不要写成方法。状态尽量集中在 `PipelineContext` 等少数 context 对象中,函数接收 ctx 参数。

```python
# 优先
def cleanup_segment(text: str, fillers: list[str]) -> str: ...

# 次之(有副作用时才用)
class TextCleaner:
    def __init__(self, fillers: list[str]): ...
    def clean(self, text: str) -> str: ...
```

### 8.2 一个函数一件事

函数名就是它做的事。如果你的函数名带 "and"、"with"、"plus",大概率该拆。

### 8.3 默认参数不用可变对象

```python
# 错误
def f(items: list = []): ...

# 正确
def f(items: list | None = None):
    if items is None:
        items = []
```

## 9. 注释与 docstring

### 9.1 文档化"为什么",不是"做什么"

代码本身告诉读者做了什么。注释 / docstring 解释**为什么这么做**、**有什么取舍**、**外部约束是什么**。

```python
# 错误(说了等于没说)
# 调用 whisper 转录
result = whisper.transcribe(audio)

# 正确
# condition_on_previous_text 关掉,避免长音频中一次幻觉传染后续段落
# 见 https://github.com/SYSTRAN/faster-whisper/issues/...
result = whisper.transcribe(audio, condition_on_previous_text=False)
```

### 9.2 公共函数 docstring 格式

```python
def get_text_at(
    self,
    start: float,
    end: float,
    prefer_raw: bool = False,
    strict: bool = False,
    strip_refs: bool = True,
) -> str:
    """返回指定时间区间内的转录文字。

    主要被多模管线和合并阶段用于按时间区间反查内容。

    Args:
        start: 起始时间(秒)
        end: 结束时间(秒)
        prefer_raw: True 时返回原始转录,False 时返回 refined 后的文本。
            默认 False。如需保证最高时间戳精度,可传 True。
        strict: True 时仅返回完全包含在区间内的句子,False 时包含相交的所有句子。
            默认 False(宁多勿少)。
        strip_refs: True 时从 refined 文本中剥离 [[REF:N]] 内部 marker(默认)。

    Returns:
        指定区间内的文本,跨多句时用空格连接。

    Raises:
        ValueError: 当 start >= end 或区间超出音频时长。

    实现说明:
        - 区间二分查找定位边界,O(log n)
        - 时间戳精度毫秒
    """
```

## 10. 测试

### 10.1 测试要求

每个公开函数至少:

- 1 个正路径测试(典型输入 → 期望输出)
- 1 个错误路径测试(边界 / 错误输入 → 正确报错)

### 10.2 测试要覆盖真实边界

测试应优先覆盖真实模块边界:文件读写、JSON 序列化、CLI 参数、缓存命中 / 失效、错误路径和外部工具包装边界。需要外部服务、GPU、大型媒体或网络下载的场景,用小型本地构造数据或 mock 外部 IO 保持单测稳定。

### 10.3 不要 mock 自己的代码

只 mock 外部 IO(HTTP API、文件系统极少数情况)。本项目内部代码之间的调用不 mock。

### 10.4 不要写占位测试

```python
# 严禁
def test_foo():
    assert True

def test_bar():
    foo(1)
    # 没有断言

def test_baz():
    assert foo(1) == foo(1)  # 自我循环
```

如果暂时不知道怎么测,**写一条 TODO 注释 + 跳过该测试**,不要编占位测试骗过覆盖率。

## 11. Git 提交

- 一次提交聚焦一个事(一个 stage、一个 bug 修复、一次重构)
- Commit message:第一行简洁说明做了什么;如有需要,空行后正文解释为什么
- 不要混合"实现 + 重构 + 风格修改"在一个 commit 里

---

# 第二部分 针对 AI Coding Agent 的注意事项

本部分专门针对 AI 写代码时的常见问题。**Agent 在写每一段代码前,应自我对照本节**。

## 12. 不要过度设计

### 12.1 反对过度抽象

本项目优先简单实现。

**不允许**未经文档明确要求添加:

- 新的抽象基类
- 工厂模式(除文档已声明的 `get_client()` / `for_task()` 等)
- 注册机制(`@register("xxx")` 装饰器之类)
- 插件系统
- 中间件 / 拦截器
- 事件总线 / 信号机制
- 元类、`__init_subclass__`、动态属性
- "为将来扩展"而设的 hook

如果你觉得"这里加个抽象层会更优雅",**先停下来检查文档是否要求**。文档没要求就不要加。文档要求的抽象层级已经经过设计,你的"优化"很可能破坏整体一致性。

### 12.2 反对过度防御

**不允许**:

- 在每层都重复 catch 同一个异常
- 给每个公开函数都加 `try/except Exception`
- 对内部模块的输入做"以防万一"的类型检查(类型标注 + 类型检查器已经保证)
- 给所有路径都加 `path.exists()` 检查(让 `FileNotFoundError` 自然抛出)

防御性编程的位置在**模块边界和外部 IO**,不在模块内部。

### 12.3 反对超前优化

**不允许**未经性能问题驱动添加:

- 缓存层(除文档已声明的 stage 级缓存)
- 连接池
- 批处理(除文档已声明,如 batched whisper)
- 异步化非 IO 函数
- 用 numpy 重写本来用 Python 列表能做的小规模计算

如果你觉得"这里可能慢",**先按简单方式实现**。性能问题等真的出现再优化。

## 13. 不要添加规范外的功能

### 13.1 严格按文档实现

文档里写了 5 个函数,你就实现这 5 个。不要顺手加:

- "我觉得用户可能需要"的便捷方法
- "为了完整性"的 getter/setter
- "顺便处理一下"的边界情况(除非文档说了要处理)
- 自动重试(除非文档说了要重试)
- 自动清理临时文件(除非文档说了)
- 自动检测语言 / 自动选模型(除非文档说了)
- "进度日志可能有用"的日志(按文档约定的日志级别和位置)

### 13.2 发现规范问题时

如果实现到一半发现规范有遗漏、矛盾、不合理:

1. **停下来**,不要自行决策
2. 在 PR 描述、commit message、或 TODO 注释中明确指出
3. 提出 1-2 个候选方案
4. 等用户确认后再继续

不要"先实现一个我觉得合理的版本,反正可以改"。这种自作主张往往跟整体设计冲突,发现时已经写了一堆代码。

## 14. 不要绕开模块边界

### 14.1 检查清单(写代码前自查)

写一个新函数 / 新文件前,问自己:

- 我用到了 ffmpeg 吗?→ 必须从 `media/` 调用,不要 `subprocess.run("ffmpeg ...")`
- 我用到了 OpenAI / Anthropic SDK 吗?→ 必须从 `llm/` 调用,不要 `import openai`
- 我在拼接缓存路径吗?→ 用 `core/paths.py` 的常量,不要 `Path(cache_dir) / "audio" / ...`
- 我在格式化时间戳吗?→ 用 `core/timestamps.py` 的函数
- 我在生成章节 anchor / slug 吗?→ 用 `core/slugs.py`
- 我在写跨模块产物文件吗?→ 用 `core/cache.py` 的 `atomic_write_*`
- 我在 hash prompt 模板吗?→ 用 `core/cache.py` 的 `hash_prompt_template`
- 我在定义跨模块的数据类型吗?→ 加到 `core/schemas/{audio,visual,merge}.py`,不要在本模块定义
- 我从 `audio_pipeline/` 模块里 import 了东西吗?→ 必须通过 `core/artifacts.py` 的 `AudioArtifacts`

任何一条违反,停下来重新设计。

### 14.2 不要"临时"违反

不要写"我先这样写,后面再重构"。这种临时方案 99% 不会被重构。第一次就按规范写。

## 15. 跨模块一致性

### 15.1 你只看一个文件,整体一致性靠规范保证

你在写 `refine.py` 时不会同时打开 `segment.py`,但两者必须共享相同的数据结构、命名风格、错误处理方式。

**做法**:

1. 写代码前先读 `core/schemas/` 看有什么类型可用
2. 读至少一个已实现的同类模块作为风格参照(`docs/overview.md` §7 会指明参照对象)
3. 跨模块共享的概念用 schema 中已定义的名字,不要重新发明

### 15.2 名字一致

同一个概念在所有地方叫同一个名字。

例:refined 后的段在 schema 里叫 `RefinedSegment`,那么所有地方都叫这个,不要在某个文件里叫 `CleanedSegment` 或 `ProcessedSegment`。

## 16. 第三方库

### 16.1 依赖版本以项目配置为准

依赖版本以 `pyproject.toml` 中的约束为准。不要:

- 未经讨论临时安装新依赖
- 凭经验选择版本后直接写进配置
- 引入文档没列出的新依赖

如果你认为需要新依赖,先在 PR 中提出讨论,不要直接 `pip install` + 加进项目配置。

### 16.2 用现代 API

文档会指明每个关键库的"标准用法"。如果你的训练数据中有该库的旧用法,**以文档指定为准**。

如果后续采用这些库,特别注意:

- `faster-whisper` 1.0+ 的 `BatchedInferencePipeline` 是新 API
- `openai` 1.0+ 的客户端 API 跟 0.x 完全不同
- `anthropic` SDK 的 messages API 跟早期 completion API 完全不同
- `pydantic` 2.x 跟 1.x 不兼容

如果文档没指明用法,去查官方文档,不要凭训练记忆写。

### 16.3 不要用废弃 API

如果库的某个功能已废弃(即使你的训练数据里还在用),不要用。warning 不能忽略。

### 16.4 第三方库的 import 规则受 import-linter 强制

`docs/overview.md` §6 的"唯一入口规则"以 `.importlinter` 契约落地,CI 检查。新增模块或调整依赖时优先改契约文件,而不是绕开。

## 17. 测试的诚实性

### 17.1 测试要真实

- 测试输入应覆盖真实边界和不变量,不是凭空构造的"看起来像"的字符串
- 测试断言要 specific:`assert len(segments) == 5` 优于 `assert len(segments) > 0`
- 测试覆盖错误路径:传错参数、传空数据、传不存在的文件,验证抛出预期异常

### 17.2 严禁的测试反模式

```python
# 严禁:assert True
def test_foo():
    foo(1)
    assert True

# 严禁:自我循环
def test_foo():
    assert foo(1) == foo(1)

# 严禁:测试啥都不测
def test_foo():
    foo(1)  # 没有断言

# 严禁:把实现复制到测试里
def test_calculate():
    expected = a * b + c  # 这就是 calculate 函数本身
    assert calculate(a, b, c) == expected
```

### 17.3 暂时无法测试时

如果某个函数暂时难以测(依赖外部服务、需要 GPU、需要大数据),**写 TODO 注释跳过**:

```python
@pytest.mark.skip(reason="TODO: 需要 GPU 才能跑,CI 上跳过")
def test_transcribe_with_gpu():
    pass
```

不要为了凑覆盖率写假测试。

## 18. 留下决策痕迹

### 18.1 在代码中记录非 trivial 选择

任何不是显而易见的实现选择,在 docstring 或行内注释中说明原因:

```python
def transcribe(audio_path: Path) -> Transcript:
    """音频转录。

    实现说明:
    - 使用 BatchedInferencePipeline 而不是普通 transcribe,因为对长音频
      在 GPU 上有 3-5x 加速;CPU 上无收益所以代码里有分支判断。
    - condition_on_previous_text=False,避免长音频中幻觉传播。
    - VAD 必开。faster-whisper 在静音段会大量幻觉。
    - word_timestamps=True,后续按语义段时间范围切分 ASR 文本。
    - 不传 initial_prompt,避免 prompt 文案被幻觉进正文。
    """
```

### 18.2 TODO / FIXME 要具体

```python
# 错误
# TODO: 改进这个

# 正确
# TODO(@username, 2026-05): 当前阈值是经验值,跑过 ~10 个真实视频后调整。
# 见 issue #42 的讨论。
```

### 18.3 标注规范来源

实现某个细节时如果文档/规范里有依据,引用它:

```python
# 按 docs/audio-pipeline.md §3.4,refine 阶段所有模式共享同一校验合同
_validate_refined_transcript(result, transcript, segments.markers)
```

## 19. 迭代节奏

### 19.1 一次只做一个 stage

不要试图一次性把整个管线写完。按 `docs/overview.md` §7 的"实现优先级"顺序:

- 实现一个 stage
- 写它的测试
- 跑通真实输入
- 提交 / Review
- 才开始下一个 stage

### 19.2 每个 stage 完成的"验收标准"

一个 stage 算"完成"必须满足:

1. 主路径用真实输入跑通端到端
2. 缓存机制工作(再跑一次能命中缓存)
3. 错误路径有测试覆盖
4. 类型检查通过
5. 独立 CLI 调用可用
6. 至少一个其他模块的范例参照(如果不是第一个 stage)

不满足任意一条不算完成,不要进下一个 stage。

## 20. 自我审查清单

每写完一段代码,提交前对照这个清单:

- [ ] 所有公开函数有完整类型标注,没有 `Any`
- [ ] 所有跨模块数据用 dataclass / pydantic,没有 `dict[str, Any]`
- [ ] dataclass 不携带 config_hash / runtime_hint 这类配置元信息
- [ ] 错误处理符合三类规则,没有 `except Exception: pass`
- [ ] 用 `logging` 不是 `print`
- [ ] 没有魔法数字,全部从 config 读
- [ ] 没有绕开模块唯一入口(ffmpeg / LLM / 路径 / 时间戳 / slug / 原子写入 / 模板 hash)
- [ ] 没有反向 import(audio_pipeline 不 import visual_pipeline 等)
- [ ] 没有添加文档没要求的功能 / 抽象 / 优化
- [ ] 单文件 ≤ 400 行,单函数 ≤ 50 行
- [ ] 命名清晰,没有 `tmp` / `val` / `data` / `info`
- [ ] 测试用真实小数据,没有 `assert True` 这类占位
- [ ] 非 trivial 决策在 docstring / 注释中说明了原因
- [ ] TODO / FIXME 具体(含日期、原因、引用)
- [ ] 用了文档指定版本的库 API,没有用废弃用法

任意一条不满足,回去改。

---

# 附录 A:模块参照对照表

实现某个 stage 时参照同类已实现 stage:

| 实现中 | 参照 |
|---|---|
| audio_pipeline 第一个 stage(extract) | 文档 + 通用规范 |
| audio_pipeline 后续 stage | extract.py |
| visual_pipeline 任何 stage | audio_pipeline 任意 stage |
| merge 任何步骤 | audio_pipeline.refine(如涉及 LLM)/ extract(如纯逻辑) |

# 附录 B:Forbidden Patterns 速查

```python
# 严禁
except Exception: pass
except: pass
print("...")  # 非 CLI 入口
import openai  # 非 llm/
import anthropic  # 非 llm/
subprocess.run(["ffmpeg", ...])  # 非 media/
Path("./cache") / ...  # 非 core/paths.py
open(path, "w")  # 跨模块产物路径,需走 atomic_write_*
json.dump(obj, open(path, "w"))  # 同上
slug = re.sub(r"\W+", "-", title.lower())  # 走 core/slugs.py
hashlib.sha256(template_text.encode()).hexdigest()  # 模板 hash 走 hash_prompt_template
def foo(x, y): ...  # 缺类型标注
def foo(x: Any) -> Any: ...  # 用 Any
def foo(items=[]): ...  # 可变默认参数
@dataclass
class Foo: ...  # 不在 core/schemas/ 但需要跨模块用
def foo() -> dict: ...  # 跨模块返回 dict
@dataclass(frozen=True)
class Bar:
    config_hash: str  # 配置 hash 进 schema(应放 StageOutput.metadata)
if x > 15: ...  # 魔法数字
def test_foo(): assert True  # 占位测试
# TODO: 改进这个  # 不具体的 TODO
```

# 附录 C:信任的来源优先级

实现细节有歧义时,按这个优先级查证:

1. `docs/<pipeline>.md` —— 详细设计文档
2. `README.md` —— 项目级说明
3. `coding-standards.md` —— 本文档
4. `core/schemas/` —— 数据类型权威
5. 已实现的同类模块代码
6. 第三方库的官方文档(最新稳定版)
7. 第三方库的源码

**不要**信任:

- 训练数据中的旧版本 API
- Stack Overflow 上的旧答案
- 自己的"经验直觉"(这是一个有特定设计的项目,不是通用脚手架)
