# LLM Module

`llm/` 模块设计文档。本模块统一所有 LLM / VLM 调用,屏蔽不同协议与 endpoint 差异,对 stage 暴露稳定的 `LLMClient` 接口与 `complete_json()` helper。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`。

文档结构:Overview、Design Considerations、Module Details、JSON Helper、Configuration、Error Handling、Budget、Module Layout、Dependencies、Implementation Order。

---

## 1. Overview

`llm/` 是项目内唯一允许直接调用 LLM SDK / HTTP API 的模块。

第一版只实现 **OpenAI Chat 协议**,即 `openai_chat.py`。`openai_responses.py` 与 `anthropic.py` 只保留文件和接口占位,不实现真实调用。

| 文件 | 职责 |
|---|---|
| `base.py` | 定义 `LLMClient` 抽象接口 |
| `types.py` | 定义 message / response / profile / capability 等类型 |
| `openai_chat.py` | OpenAI Chat Completions 协议实现,第一版唯一真实实现 |
| `openai_responses.py` | OpenAI Responses API 协议占位 |
| `anthropic.py` | Anthropic Messages API 协议占位 |
| `factory.py` | 从配置创建 client,提供 `for_task()` / `get_client()` |
| `json_helper.py` | JSON 解析 + 1 次 repair retry + schema 校验 |
| `budget.py` | token / 成本估算与上下文预算检查 |

stage 不关心 endpoint 是 OpenAI、OpenRouter、本地 vLLM、DeepSeek、Qwen、反代,或以后接入的 Anthropic。stage 只关心任务名和统一接口。

---

## 2. Design Considerations

### 2.1 按协议切,不按厂商切

实现文件按 API 协议切分,不是按厂商切分。

正确划分:

- `OpenAIChatClient`:覆盖 OpenAI、OpenRouter、本地 vLLM、DeepSeek、Qwen、各类 OpenAI Chat 兼容 endpoint
- `OpenAIResponsesClient`:覆盖 OpenAI Responses API
- `AnthropicClient`:覆盖 Anthropic Messages API

不创建这些文件:

- `openrouter.py`
- `deepseek.py`
- `qwen.py`
- `vllm.py`

### 2.2 第一版只实现 OpenAI Chat

第一版范围:

- `protocol: openai_chat`
- 文本输入 / 文本输出
- 可选 JSON mode
- vision message 结构预留
- 统一错误归一化
- `complete_json()` helper
- `tasks.*` 到 profile 的映射
- budget 的粗略 token 估算

第一版不实现:

- Responses API 真实调用
- Anthropic 真实调用
- tool calling
- function calling
- embeddings
- batch API
- provider fallback

### 2.3 任务只映射到 profile

stage 通过任务名拿 client:

```python
client = for_task(ctx.config, TaskName.SEGMENT)
```

配置中声明:

```yaml
tasks:
  segment: gpt5_main
  refine: gpt5_main
  outline: gpt5_main
  section: gpt5_main
  slide_judge: weak_vlm
  slide_describe: gpt5_main
```

stage 内禁止出现模型名、base URL、API key 环境变量名。这些都属于 profile 数据。

### 2.4 profile capabilities 是能力边界

profile 示例:

```yaml
llm:
  profiles:
    gpt5_main:
      protocol: openai_chat
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-5
      capabilities: [vision, prompt_cache, json_mode]
      max_context: 1000000
```

capabilities 用于调用前检查:

| capability | 含义 |
|---|---|
| `vision` | 允许传图片输入 |
| `prompt_cache` | endpoint 可能支持 prompt cache |
| `json_mode` | 允许请求原生 JSON 输出模式 |
| `streaming` | 允许使用流式接口 |
| `reasoning` | 允许传 reasoning 相关参数,后续扩展 |

capabilities 表示支持能力,不表示一定启用。是否启用由 stage 和调用参数决定。

### 2.5 JSON helper 只管结构

`complete_json()` 负责:

1. 从 LLM 输出解析 JSON
2. 解析失败或 schema 校验失败时最多做 1 次 repair retry
3. 对 repair 后结果再次做 schema 校验

它不负责业务不变量。例如 segment stage 的时间边界连续性、outline stage 的 chapter 覆盖完整性,都由各自 stage 校验。

### 2.6 错误归一化

所有协议实现必须把第三方 SDK / HTTP 异常归一化为项目异常:

- `AuthError`
- `RateLimitError`
- `ContextLengthError`
- `TransportError`
- `LLMError`

调用方不按 SDK 类型或错误字符串判断。

---

## 3. Module Details

### 3.1 `base.py`

定义统一接口:

```python
class LLMClient(Protocol):
    @property
    def profile(self) -> LLMProfile: ...

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> Iterator[str]: ...
```

要求:

- `complete()` 返回 `LLMResponse`,不返回 SDK 原生对象
- `stream()` 返回文本增量 iterator;第一版可以只保证接口存在
- 协议实现内部完成错误归一化
- `base.py` 不 import 第三方 SDK

### 3.2 `types.py`

建议类型:

```python
class LLMProtocol(StrEnum):
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC = "anthropic"

class LLMCapability(StrEnum):
    VISION = "vision"
    PROMPT_CACHE = "prompt_cache"
    JSON_MODE = "json_mode"
    STREAMING = "streaming"
    REASONING = "reasoning"

@dataclass(frozen=True)
class LLMProfile:
    name: str
    protocol: LLMProtocol
    base_url: str
    api_key_env: str
    model: str
    capabilities: frozenset[LLMCapability]
    max_context: int | None = None
    timeout_seconds: float | None = None
```

message / response 类型:

```python
@dataclass(frozen=True)
class TextPart:
    text: str

@dataclass(frozen=True)
class ImagePart:
    path: Path
    mime_type: str

LLMContentPart = TextPart | ImagePart

@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: list[LLMContentPart]

@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None = None

@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage | None
    raw_response_id: str | None = None

@dataclass(frozen=True)
class LLMRequestOptions:
    temperature: float | None = None
    max_output_tokens: int | None = None
    json_mode: bool = False
    timeout_seconds: float | None = None
```

设计约束:

- `LLMMessage.content` 用 part 列表,提前兼容 vision
- `role` 保持字符串,避免过早抽象不同协议 role 差异
- usage 允许 `None`,兼容 endpoint 不一定返回完整 usage

### 3.3 `openai_chat.py`

职责:实现 OpenAI Chat Completions 协议。

实现要点:

- 使用 OpenAI Python SDK 1.x 客户端 API
- client 初始化使用 profile 的 `base_url` 和 `api_key_env`
- API key 从环境变量读取,缺失时抛 `AuthError`
- `LLMMessage` 转成 Chat Completions messages
- `TextPart` 转成文本内容
- `ImagePart` 仅当 profile 有 `vision` capability 时允许
- `options.json_mode=True` 时要求 profile 有 `json_mode` capability
- SDK / HTTP 异常必须转为统一异常

示意:

```python
class OpenAIChatClient:
    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse:
        request = self._build_request(messages, options)
        try:
            response = self._client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise normalize_openai_error(exc) from exc
        return self._to_llm_response(response)
```

JSON mode 请求形态:

```python
if options.json_mode:
    request["response_format"] = {"type": "json_object"}
```

即使启用了 JSON mode,仍然要走 `complete_json()` 的解析和 schema 校验。

### 3.4 `openai_responses.py`

第一版只保留结构:

```python
class OpenAIResponsesClient:
    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse:
        raise NotImplementedError("openai_responses is not implemented in the first version")
```

factory 第一版遇到 `protocol: openai_responses` 时应抛 `ConfigError`,不要返回运行中途才失败的 client。

### 3.5 `anthropic.py`

第一版与 `openai_responses.py` 一样只保留结构。factory 遇到 `protocol: anthropic` 时抛 `ConfigError`。

后续实现仍必须满足统一 `LLMMessage` / `LLMResponse` 和错误归一化。

### 3.6 `factory.py`

对外函数:

```python
def get_client(config: AppConfig, profile_name: str) -> LLMClient: ...
def for_task(config: AppConfig, task_name: TaskName) -> LLMClient: ...
```

行为:

1. `for_task()` 从 `config.tasks[task_name]` 找 profile name
2. `get_client()` 从 `config.llm.profiles[profile_name]` 读取 profile
3. 按 `profile.protocol` 创建对应 client
4. 可按 profile name 做进程内 client 缓存,避免重复创建 SDK client

第一版协议支持:

```python
match profile.protocol:
    case LLMProtocol.OPENAI_CHAT:
        return OpenAIChatClient(profile)
    case LLMProtocol.OPENAI_RESPONSES:
        raise ConfigError("protocol 'openai_responses' is not implemented in the first version")
    case LLMProtocol.ANTHROPIC:
        raise ConfigError("protocol 'anthropic' is not implemented in the first version")
```

不做 fallback,不自动选模型,不在 factory 中写 JSON 解析。

---

## 4. JSON Helper — `json_helper.py`

统一结构化输出入口:

```python
def complete_json(
    client: LLMClient,
    messages: list[LLMMessage],
    schema: type[JsonSchemaT],
    options: LLMRequestOptions | None = None,
    max_repair_retries: int = 1,
) -> JsonSchemaT: ...
```

流程:

1. 调用 `client.complete(messages, options_with_json_mode)`
2. 从 `LLMResponse.text` 解析 JSON
3. 用 `schema` 做 schema 校验
4. 如果解析或 schema 校验失败,构造 repair prompt
5. 最多重试 `max_repair_retries` 次,默认 1
6. repair 仍失败则抛 `LLMError`

JSON 解析顺序:

1. 直接 `json.loads(text)`
2. 如果失败,尝试提取 fenced code block 中的 JSON
3. 如果仍失败,触发 repair retry

不做复杂启发式本地修复,例如自动补括号、替换引号、删除尾逗号。

repair prompt 用英文,目标是只修 JSON,不重新完成业务任务:

```python
repair_messages = [
    LLMMessage(
        role="system",
        content=[TextPart(text="You repair invalid JSON outputs. Return only valid JSON.")],
    ),
    LLMMessage(
        role="user",
        content=[TextPart(text=f"Validation error:\n{error_message}\n\nPrevious output:\n{raw_text}")],
    ),
]
```

业务不变量由 stage 自己校验。`complete_json()` 不知道 `SegmentList` 必须覆盖完整音频,也不知道 `Outline` 必须覆盖所有 block。

---

## 5. Configuration

profile 示例:

```yaml
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
      timeout_seconds: 120
    weak_vlm:
      protocol: openai_chat
      base_url: https://openrouter.ai/api/v1
      api_key_env: OPENROUTER_API_KEY
      model: google/gemini-2.5-flash
      capabilities: [vision]
```

字段:

| 字段 | 含义 |
|---|---|
| `protocol` | API 协议,第一版只允许 `openai_chat` 实际运行 |
| `base_url` | endpoint URL |
| `api_key_env` | API key 所在环境变量名 |
| `model` | 传给 endpoint 的模型名 |
| `capabilities` | 能力列表 |
| `max_context` | 最大上下文 token 数,可空 |
| `timeout_seconds` | 单次请求超时,可空 |

task 映射见 `docs/overview.md` §8。stage 不允许 fallback 到 `llm.active_default`,避免配置遗漏被悄悄掩盖。

---

## 6. Error Handling

LLM 模块只向外抛项目异常:

| 异常 | 场景 |
|---|---|
| `AuthError` | API key 缺失、无效、权限不足 |
| `RateLimitError` | endpoint 限流、配额耗尽 |
| `ContextLengthError` | prompt + output 超过模型上下文 |
| `TransportError` | 网络错误、超时、5xx、SDK transport 错误 |
| `LLMError` | JSON 解析失败、schema 失败、capability 不满足等 |

`openai_chat.py` 内部集中归一化 OpenAI SDK 异常。可以根据 SDK 结构化字段判断;不要把字符串判断散落到 stage。

临时性错误可在 client 内用 `tenacity` 重试:

- `TransportError`
- 可重试的 `RateLimitError`

不重试:

- `AuthError`
- `ContextLengthError`
- capability 不满足
- 业务不变量失败

---

## 7. Budget — `budget.py`

`budget.py` 负责 token / 成本估算与上下文预算检查。第一版做粗略估算即可。

建议接口:

```python
@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    max_output_tokens: int | None
    total_tokens: int

def estimate_messages_tokens(messages: list[LLMMessage]) -> int: ...

def check_context_budget(
    profile: LLMProfile,
    messages: list[LLMMessage],
    max_output_tokens: int | None,
) -> TokenEstimate: ...
```

行为:

1. `profile.max_context is None` 时只返回估算,不阻塞
2. 有 `max_context` 时估算 `input_tokens + max_output_tokens`
3. 超过 `max_context` 时抛 `ContextLengthError`

第一版不硬编码模型价格。价格变化频繁,后续如需要应进入配置文件。

---

## 8. Downstream Interfaces

stage 可依赖的稳定 API:

```python
def get_client(config: AppConfig, profile_name: str) -> LLMClient: ...
def for_task(config: AppConfig, task_name: TaskName) -> LLMClient: ...
def complete_json(...) -> JsonSchemaT: ...
```

stage 可依赖:

- `LLMClient.complete()`
- `LLMResponse.text`
- `LLMResponse.usage`
- `LLMProfile.capabilities`
- `complete_json()` 默认 1 次 repair retry
- 统一异常类型

stage 不应依赖:

- 第三方 SDK 原生 response
- OpenAI / Anthropic 原生异常类
- HTTP status code
- token 估算精度
- 某个兼容 endpoint 的非标准扩展字段

---

## 9. Module Layout

```text
lvnotes/llm/
├── __init__.py
├── base.py
├── types.py
├── openai_chat.py
├── openai_responses.py
├── anthropic.py
├── factory.py
├── json_helper.py
└── budget.py
```

Import 规则:

| 来源 | 允许? |
|---|---|
| `core/config.py` | ✅ |
| `core/exceptions.py` | ✅ |
| `openai` SDK | ✅,仅 `openai_chat.py` / 后续 `openai_responses.py` |
| `anthropic` SDK | ✅,仅 `anthropic.py` |
| `tenacity` | ✅ |
| `audio_pipeline/` | ❌ |
| `visual_pipeline/` | ❌ |
| `merge/` | ❌ |
| `asr/` | ❌ |
| `media/` | ❌ |

---

## 10. Dependencies

第一版真实需要:

- `openai`
- `tenacity`
- `pydantic`,如果最终用 pydantic model 做 LLM JSON schema 校验

后续扩展才需要:

- `anthropic`

具体依赖版本以后续 `pyproject.toml` 为准。

---

## 11. Implementation Order

第一阶段:可用 OpenAI Chat。

1. 创建 `types.py`
2. 创建 `base.py`
3. 创建 `openai_chat.py`
4. 创建 `factory.py`
5. 创建 `json_helper.py`
6. 创建 `budget.py`
7. 创建 `openai_responses.py` / `anthropic.py` 占位
8. 更新 `__init__.py` re-export 稳定入口

验收标准:

1. `protocol: openai_chat` profile 可创建 `OpenAIChatClient`
2. `for_task(config, TaskName.SEGMENT)` 能按 `tasks.segment` 返回 client
3. API key 缺失抛 `AuthError`
4. 未实现协议抛 `ConfigError`
5. `complete_json()` 能解析合法 JSON
6. `complete_json()` 对非法 JSON 做 1 次 repair retry
7. repair 后仍失败抛 `LLMError`
8. schema 通过但业务不变量错误不在 helper 中处理
9. stage 不直接 import `openai`

第二阶段接入 `audio_pipeline/segment.py`、`audio_pipeline/refine.py`,第三阶段接入 `merge/` 和 `visual_pipeline/`,第四阶段再扩展 Responses / Anthropic 协议。
