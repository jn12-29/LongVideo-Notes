# LLM Module

`llm/` 模块统一所有 LLM / VLM 调用，屏蔽不同 provider、协议与 endpoint 的差异。stage 只通过稳定 API 获取 `LLMClient`，再调用 `complete_text()` 或 `complete_json()`；stage 不感知 provider 类型、SDK、HTTP 状态码或原生 response 结构。

写代码前必读本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`。

## TOC

- [1. Overview](#1-overview)
- [2. Public API](#2-public-api)
- [3. Provider Types](#3-provider-types)
- [4. Types](#4-types)
- [5. Configuration](#5-configuration)
- [6. Factory](#6-factory)
- [7. Text Completion](#7-text-completion)
- [8. JSON Helper](#8-json-helper)
- [9. Error Handling](#9-error-handling)
- [10. Budget](#10-budget)
- [11. Downstream Contract](#11-downstream-contract)
- [12. Module Layout](#12-module-layout)
- [13. Dependencies](#13-dependencies)
- [14. Implementation Order](#14-implementation-order)

---

## 1. Overview

`llm/` 是项目内唯一允许直接调用 LLM SDK / HTTP API 的模块。

第一版必须实现全部 provider 类型：

| provider | 实现文件 | 用途 |
|---|---|---|
| `openai_chat` | `openai_chat.py` | OpenAI Chat Completions 原生 API |
| `openai_responses` | `openai_responses.py` | OpenAI Responses API |
| `anthropic_messages` | `anthropic_messages.py` | Anthropic Messages API |
| `openai_compatible_chat` | `openai_compatible_chat.py` | OpenAI Chat Completions 兼容 endpoint，例如 OpenRouter、本地 vLLM、DeepSeek、Qwen、各类反代 |

文件职责：

| 文件 | 职责 |
|---|---|
| `base.py` | 定义 `LLMClient` 抽象接口 |
| `types.py` | 定义 message / result / profile / capability / options 等类型 |
| `openai_chat.py` | OpenAI Chat Completions 原生实现 |
| `openai_responses.py` | OpenAI Responses API 实现 |
| `anthropic_messages.py` | Anthropic Messages API 实现 |
| `openai_compatible_chat.py` | OpenAI Chat 兼容 endpoint 实现 |
| `factory.py` | 从配置创建 client，提供 `get_client()` / `for_task()` |
| `text_helper.py` | `complete_text()` 统一文本补全入口 |
| `json_helper.py` | `complete_json()` JSON 解析 + repair retry + schema 校验 |
| `budget.py` | token / 成本估算与上下文预算检查 |

provider 按 API 协议和兼容性边界切分，不按商业厂商切分。OpenRouter、本地 vLLM、DeepSeek、Qwen 和反代 endpoint 都走 `openai_compatible_chat`，不要为这些 endpoint 创建单独模块。

---

## 2. Public API

`llm/` 对 stage 暴露且只暴露以下稳定入口：

```python
def get_client(config: AppConfig, profile_name: str) -> LLMClient: ...

def for_task(config: AppConfig, task_name: str) -> LLMClient: ...

def complete_text(
    client: LLMClient,
    messages: list[LLMMessage],
    options: LLMRequestOptions | None = None,
) -> LLMTextResult: ...

def complete_json(
    client: LLMClient,
    messages: list[LLMMessage],
    schema: type[JsonSchemaT],
    options: LLMRequestOptions | None = None,
    max_repair_retries: int = 1,
) -> dict: ...
```

任务名是普通字符串。文档、配置和代码示例统一使用：`"segment"`、`"refine"`、`"outline"`、`"section"`、`"slide_judge"`、`"slide_describe"`。不要引入 enum。

stage 示例：

```python
from lvnotes.llm import complete_json, complete_text, for_task

client = for_task(ctx.config, "segment")
segments = complete_json(client, messages, SegmentListSchema, options)

client = for_task(ctx.config, "refine")
result = complete_text(client, messages, options)
text = result.text
```

约束：

- stage 不读取 `profile.provider`、`base_url`、`api_key_env`、`model`。
- stage 不 import `openai`、`anthropic` 或任何第三方 LLM SDK。
- stage 不 catch SDK 原生异常，不按 HTTP status code 或错误字符串分支。
- stage 不自己写 JSON repair 逻辑；结构化输出统一走 `complete_json()`。

---

## 3. Provider Types

### 3.1 `openai_chat`

用于 OpenAI Chat Completions 原生 API。

实现要点：

- 使用 OpenAI Python SDK 1.x 客户端 API。
- client 初始化使用 profile 的 `base_url`、`api_key_env`、`model`、`timeout_seconds`。
- `LLMMessage` 转成 Chat Completions messages。
- `TextPart` 转成文本内容。
- `ImagePart` 仅当 profile 有 `vision` capability 时允许。
- `options.json_mode=True` 时要求 profile 有 `json_mode` capability，并设置 `response_format={"type": "json_object"}`。
- SDK / HTTP 异常必须转为统一项目异常。

### 3.2 `openai_responses`

用于 OpenAI Responses API。

实现要点：

- 使用 OpenAI Python SDK 1.x Responses API。
- `LLMMessage` 转成 Responses API input 结构。
- 文本结果归一化为 `LLMTextResult.text`。
- usage 归一化为 `LLMUsage`，缺失字段填 `None`。
- `options.json_mode=True` 时要求 profile 有 `json_mode` capability，并使用 Responses API 支持的结构化输出参数。
- `ImagePart` 仅当 profile 有 `vision` capability 时允许。
- SDK / HTTP 异常必须转为统一项目异常。

### 3.3 `anthropic_messages`

用于 Anthropic Messages API。

实现要点：

- 使用 Anthropic Python SDK 的 Messages API。
- `system` role 内容按 Anthropic API 要求提取为顶层 system 参数；其余消息转成 messages 列表。
- `TextPart` 转成 Anthropic text block。
- `ImagePart` 仅当 profile 有 `vision` capability 时允许，并转成 Anthropic image block。
- Anthropic 没有与 OpenAI JSON mode 完全等价的通用参数时，`options.json_mode=True` 只作为 capability 检查和 prompt 约束，不伪造不支持的 SDK 参数。
- SDK / HTTP 异常必须转为统一项目异常。

### 3.4 `openai_compatible_chat`

用于 OpenAI Chat Completions 兼容 endpoint。

实现要点：

- 使用 OpenAI Python SDK 1.x 客户端 API，传入 profile 的 `base_url`。
- 请求 shape 默认与 `openai_chat` 相同。
- 只依赖 OpenAI Chat Completions 标准字段，不依赖某个兼容 endpoint 的私有扩展。
- 兼容 endpoint 不保证完整返回 usage；缺失字段填 `None`。
- `options.json_mode=True` 时要求 profile 有 `json_mode` capability；如果 endpoint 实际不支持该参数，归一化为 `LLMError` 或 `TransportError`，不要在 stage 里特殊处理。
- SDK / HTTP 异常必须转为统一项目异常。

---

## 4. Types

### 4.1 `base.py`

统一 client 协议：

```python
class LLMClient(Protocol):
    @property
    def profile(self) -> LLMProfile: ...

    def complete(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> LLMTextResult: ...

    def stream(
        self,
        messages: list[LLMMessage],
        options: LLMRequestOptions | None = None,
    ) -> Iterator[str]: ...
```

要求：

- `complete()` 返回 `LLMTextResult`，不返回 SDK 原生对象。
- `stream()` 返回文本增量 iterator；provider 支持 `streaming` capability 时必须真实可用。
- 协议实现内部完成错误归一化。
- `base.py` 不 import 第三方 SDK。

### 4.2 `types.py`

建议类型：

```python
@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: str
    base_url: str | None
    api_key_env: str
    model: str
    capabilities: frozenset[str]
    max_context: int | None = None
    timeout_seconds: float | None = None

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
class LLMTextResult:
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

约束：

- `LLMProfile.provider` 必须是 `openai_chat`、`openai_responses`、`anthropic_messages`、`openai_compatible_chat` 之一。
- `LLMMessage.content` 用 part 列表，统一承载文本和图片。
- `role` 保持字符串，provider 实现负责映射不同 API 的 role 差异。
- capabilities 使用字符串集合，允许的当前值为 `vision`、`prompt_cache`、`json_mode`、`streaming`、`reasoning`。
- usage 允许 `None`，兼容 endpoint 不一定返回完整 usage。

---

## 5. Configuration

示例：

```yaml
llm:
  profiles:
    gpt_main:
      provider: openai_responses
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-5
      capabilities: [vision, prompt_cache, json_mode, reasoning]
      max_context: 1000000
      timeout_seconds: 120
    claude_main:
      provider: anthropic_messages
      base_url: https://api.anthropic.com
      api_key_env: ANTHROPIC_API_KEY
      model: claude-sonnet-4-5
      capabilities: [vision, prompt_cache]
      max_context: 200000
      timeout_seconds: 120
    weak_vlm:
      provider: openai_compatible_chat
      base_url: https://openrouter.ai/api/v1
      api_key_env: OPENROUTER_API_KEY
      model: google/gemini-2.5-flash
      capabilities: [vision]
      timeout_seconds: 60

tasks:
  segment: gpt_main
  refine: gpt_main
  outline: gpt_main
  section: gpt_main
  slide_judge: weak_vlm
  slide_describe: gpt_main
```

字段：

| 字段 | 含义 |
|---|---|
| `provider` | provider 类型，必须是四种受支持值之一 |
| `base_url` | endpoint URL；provider 有默认官方 endpoint 时仍建议显式配置 |
| `api_key_env` | API key 所在环境变量名 |
| `model` | 传给 endpoint 的模型名 |
| `capabilities` | profile 支持的能力列表 |
| `max_context` | 最大上下文 token 数，可空 |
| `timeout_seconds` | 单次请求超时，可空 |

`llm` 配置不提供默认 profile。每个会调用 LLM 的 stage 必须通过 `tasks.<task_name>` 显式映射到 profile；缺失映射应在配置加载或 `for_task()` 时抛 `ConfigError`。

### 5.1 `api_key_env` 规则

`api_key_env` 是环境变量名，不是 API key 明文。

规则：

- `api_key_env` 必须是非空字符串。
- 配置文件中不得出现真实 API key。
- client 创建时读取 `os.environ[api_key_env]`。
- 环境变量不存在或值为空字符串时，必须抛 `AuthError`。
- 错误信息必须包含 profile name 与 env var 名，例如：`LLM profile 'gpt_main' requires env var OPENAI_API_KEY`。
- 错误信息不得包含 API key 值、token 片段或 Authorization header。
- 同一个 `api_key_env` 可被多个 profile 复用。

---

## 6. Factory

对外函数：

```python
def get_client(config: AppConfig, profile_name: str) -> LLMClient: ...

def for_task(config: AppConfig, task_name: str) -> LLMClient: ...
```

行为：

1. `for_task()` 从 `config.tasks[task_name]` 找 profile name。
2. `get_client()` 从 `config.llm.profiles[profile_name]` 读取 profile。
3. 校验 provider、capabilities、`api_key_env` 和必要字段。
4. 按 `profile.provider` 创建对应 client。
5. 可按 profile name 做进程内 client 缓存，避免重复创建 SDK client。

provider 分发：

```python
match profile.provider:
    case "openai_chat":
        return OpenAIChatClient(profile)
    case "openai_responses":
        return OpenAIResponsesClient(profile)
    case "anthropic_messages":
        return AnthropicMessagesClient(profile)
    case "openai_compatible_chat":
        return OpenAICompatibleChatClient(profile)
    case _:
        raise ConfigError(f"unknown LLM provider: {profile.provider}")
```

factory 不做 fallback、不自动选模型、不写 JSON 解析、不把 provider 信息返回给 stage 做分支。

---

## 7. Text Completion

`complete_text()` 是文本输出统一入口：

```python
def complete_text(
    client: LLMClient,
    messages: list[LLMMessage],
    options: LLMRequestOptions | None = None,
) -> LLMTextResult: ...
```

行为：

1. 调用 `check_context_budget(client.profile, messages, options.max_output_tokens)`。
2. 校验 `ImagePart` 需要 `vision` capability。
3. 校验 `options.json_mode=True` 需要 `json_mode` capability。
4. 调用 `client.complete(messages, options)`。
5. 返回 `LLMTextResult`。

`complete_text()` 不解析 JSON、不做业务校验、不修改 stage prompt。

---

## 8. JSON Helper

统一结构化输出入口：

```python
def complete_json(
    client: LLMClient,
    messages: list[LLMMessage],
    schema: type[JsonSchemaT],
    options: LLMRequestOptions | None = None,
    max_repair_retries: int = 1,
) -> dict: ...
```

流程：

1. 构造 `options_with_json_mode`，尽量启用 `json_mode`。
2. 调用 `complete_text(client, messages, options_with_json_mode)`。
3. 从 `LLMTextResult.text` 解析 JSON。
4. 用 `schema` 做 schema 校验。
5. 如果解析或 schema 校验失败，构造 repair prompt。
6. 最多重试 `max_repair_retries` 次，默认 1。
7. repair 仍失败则抛 `LLMError`。
8. 返回校验后的 `dict`。

JSON 解析顺序：

1. 直接 `json.loads(text)`。
2. 如果失败，尝试提取 fenced code block 中的 JSON。
3. 如果仍失败，触发 repair retry。

不做复杂启发式本地修复，例如自动补括号、替换引号、删除尾逗号。

repair prompt 用英文，目标是只修 JSON，不重新完成业务任务：

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

业务不变量由 stage 自己校验。`complete_json()` 不知道 `SegmentList` 必须覆盖完整音频，也不知道章节范围必须覆盖所有 block。

---

## 9. Error Handling

LLM 模块只向外抛项目异常：

| 异常 | 场景 |
|---|---|
| `AuthError` | API key env var 缺失、env var 值为空、key 无效、权限不足 |
| `RateLimitError` | endpoint 限流、配额耗尽 |
| `ContextLengthError` | prompt + output 超过模型上下文 |
| `TransportError` | 网络错误、超时、5xx、SDK transport 错误 |
| `LLMError` | JSON 解析失败、schema 失败、capability 不满足、provider 返回无法归一化的响应 |

provider 实现内部集中归一化 SDK / HTTP 异常。可以根据 SDK 结构化字段判断；不要把字符串判断散落到 stage。

临时性错误可在 client 内用 `tenacity` 重试：

- `TransportError`
- 可重试的 `RateLimitError`

不重试：

- `AuthError`
- `ContextLengthError`
- capability 不满足
- JSON schema 失败
- 业务不变量失败

---

## 10. Budget

`budget.py` 负责 token / 成本估算与上下文预算检查。第一版做粗略估算即可。

建议接口：

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

行为：

1. `profile.max_context is None` 时只返回估算，不阻塞。
2. 有 `max_context` 时估算 `input_tokens + max_output_tokens`。
3. 超过 `max_context` 时抛 `ContextLengthError`。

第一版不硬编码模型价格。价格变化频繁，后续如需要应进入配置文件。

---

## 11. Downstream Contract

stage 可依赖：

- `get_client(config, profile_name) -> LLMClient`
- `for_task(config, task_name) -> LLMClient`
- `complete_text(client, messages, options) -> LLMTextResult`
- `complete_json(client, messages, schema, options, max_repair_retries=1) -> dict`
- `LLMTextResult.text`
- `LLMTextResult.usage`
- 统一异常类型

stage 不应依赖：

- `LLMProfile.provider`
- 第三方 SDK 原生 response
- OpenAI / Anthropic 原生异常类
- HTTP status code
- token 估算精度
- 某个兼容 endpoint 的非标准扩展字段

---

## 12. Module Layout

```text
lvnotes/llm/
├── __init__.py
├── base.py
├── types.py
├── openai_chat.py
├── openai_responses.py
├── anthropic_messages.py
├── openai_compatible_chat.py
├── factory.py
├── text_helper.py
├── json_helper.py
└── budget.py
```

Import 规则：

| 来源 | 允许? |
|---|---|
| `core/config.py` | yes |
| `core/exceptions.py` | yes |
| `openai` SDK | yes，仅 `openai_chat.py` / `openai_responses.py` / `openai_compatible_chat.py` |
| `anthropic` SDK | yes，仅 `anthropic_messages.py` |
| `tenacity` | yes |
| `audio_pipeline/` | no |
| `visual_pipeline/` | no |
| `merge/` | no |
| `asr/` | no |
| `media/` | no |

---

## 13. Dependencies

第一版需要：

- `openai`
- `anthropic`
- `tenacity`
- `pydantic`，如果最终用 pydantic model 做 LLM JSON schema 校验

具体依赖版本以后续 `pyproject.toml` 为准。

---

## 14. Implementation Order

按以下顺序实现，保证统一 API 先稳定，再接入各 provider：

1. 创建 `types.py`。
2. 创建 `base.py`。
3. 创建 `budget.py`。
4. 创建 `text_helper.py`。
5. 创建 `json_helper.py`。
6. 创建 `openai_chat.py`。
7. 创建 `openai_compatible_chat.py`。
8. 创建 `openai_responses.py`。
9. 创建 `anthropic_messages.py`。
10. 创建 `factory.py`。
11. 更新 `__init__.py` re-export 稳定入口。

验收标准：

1. 四种 provider profile 都可通过 `get_client(config, profile_name)` 创建对应 client。
2. `for_task(config, "segment")`、`for_task(config, "refine")`、`for_task(config, "outline")`、`for_task(config, "section")`、`for_task(config, "slide_judge")`、`for_task(config, "slide_describe")` 能按 `tasks.*` 返回 client。
3. `api_key_env` 缺失或对应环境变量为空时抛 `AuthError`，错误信息包含 profile name 和 env var 名但不包含 key 值。
4. 未知 provider 抛 `ConfigError`。
5. `complete_text()` 返回 `LLMTextResult`。
6. `complete_json()` 能解析合法 JSON。
7. `complete_json()` 对非法 JSON 做 1 次 repair retry。
8. repair 后仍失败抛 `LLMError`。
9. schema 通过但业务不变量错误不在 helper 中处理。
10. stage 不直接 import `openai` 或 `anthropic`。
