# Media Module

`media/` 模块设计文档。本模块是全项目 ffmpeg / ffprobe 的唯一入口,负责媒体元信息读取、抽取 wav、抽取采样帧和创建开头裁剪文件。**写代码前必读**本文档以及 `coding-standards.md`、`README.md`、`docs/overview.md`。

文档结构:Overview、Design Considerations、Public API、Module Layout、Error Handling、Dependencies、Implementation Order。

---

## 1. Overview

`media/` 只做四类事:

| 模块 | 职责 | 主要消费者 |
|---|---|---|
| `probe.py` | 调 `ffprobe` 读取音视频元信息 | `audio_pipeline/extract.py`、`visual_pipeline/sample.py`、`cli/app.py` |
| `audio.py` | 调 `ffmpeg` 抽取 / 转码音频 wav | `audio_pipeline/extract.py` |
| `video.py` | 调 `ffmpeg` 抽取视频帧 | `visual_pipeline/sample.py` |
| `trim.py` | 调 `ffmpeg` 创建输入开头裁剪文件 | `cli/app.py` |

全项目只有 `media/` 允许直接使用 `subprocess` 调用 `ffmpeg` / `ffprobe`。其他模块需要媒体处理时,必须调用 `media/` 暴露的函数。

`media/` 不负责缓存、不拼接缓存路径、不写 stage 产物 JSON。调用方提供输入路径和输出路径;调用方决定产物放哪里、是否写 index、是否进入缓存。

---

## 2. Design Considerations

### 2.1 唯一入口

ffmpeg / ffprobe 是外部进程,错误形态复杂。集中在 `media/` 可以统一:

- 命令参数构造
- stderr 捕获
- 超时策略
- 错误包装为 `MediaError`
- 输出文件校验
- import-linter 约束

### 2.2 函数粒度细

本模块不提供万能 wrapper,只提供项目需要的函数:

- 读媒体元信息
- 抽取 wav
- 按 fps 抽帧
- 创建或解析 `--head-minutes` 开头裁剪文件

禁止新增形态:

```python
def run_ffmpeg(args: list[str]) -> None: ...
```

这会把 ffmpeg 细节泄漏给调用方,等于绕开唯一入口。

### 2.3 不持有业务状态

`media/` 是无状态工具层:

- 不读取 `Config`
- 不读取 `PipelineContext`
- 不知道 `cache/` 目录结构
- 不知道 stage 名
- 不写 `StageOutput`

配置值由调用方展开后传入函数参数。

### 2.4 输出校验在 media 边界完成

ffmpeg 成功退出不等于产物可用。`media/` 对自己创建的文件做最小校验:

- 输出文件存在
- 输出文件非空
- 必要时用 `ffprobe` 复查 stream 类型
- 抽 wav 后确认存在 audio stream
- 抽帧后确认至少生成 1 张帧

更高层业务校验由调用方做,例如 wav 时长与源文件时长误差。

---

## 3. Public API

### 3.1 `probe.py`

职责:封装 `ffprobe`,返回结构化元信息。

```python
@dataclass(frozen=True)
class AudioStreamInfo:
    codec: str
    sample_rate: int
    channels: int
    duration: float | None

@dataclass(frozen=True)
class VideoStreamInfo:
    codec: str
    width: int
    height: int
    fps: float
    duration: float | None

@dataclass(frozen=True)
class MediaProbeResult:
    path: Path
    duration: float
    audio: AudioStreamInfo | None
    video: VideoStreamInfo | None
```

公开函数:

```python
def probe_media(input_path: Path) -> MediaProbeResult: ...
def has_audio_stream(input_path: Path) -> bool: ...
def has_video_stream(input_path: Path) -> bool: ...
```

实现要点:

- `probe_media()` 内部调用 `ffprobe -print_format json`
- duration 统一为 `float` 秒数,精度毫秒
- 缺少 audio stream 时 `audio=None`
- 缺少 video stream 时 `video=None`
- 输入文件不存在时不做额外预检,让 ffmpeg / ffprobe 失败并包装为 `MediaError`
- ffprobe JSON 解析失败包装为 `MediaError`

### 3.2 `audio.py`

职责:封装音频抽取 / 转码。

```python
def extract_wav(
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    channels: int,
) -> Path: ...
```

语义:把输入视频或音频文件中的音频流抽取为 wav。调用方传入目标采样率和声道数;第一版音频管线默认 `16000` / `1`。

实现要点:

- 使用 `ffmpeg`
- 覆盖输出用 `-y`
- 输出 wav 使用 PCM signed 16-bit little-endian
- 重采样参数来自函数参数,不在函数内写死业务默认值
- 输出目录由调用方保证存在
- 抽取后校验输出文件存在且非空
- 抽取后可用 `probe_media(output_path)` 复查 audio stream
- ffmpeg 返回非 0 时包装为 `MediaError`

调用示例:

```python
audio_path = extract_wav(
    input_path=ctx.source_path,
    output_path=ctx.paths.audio_wav,
    sample_rate=ctx.config.audio_pipeline.extract.sample_rate,
    channels=ctx.config.audio_pipeline.extract.channels,
)
```

### 3.3 `video.py`

职责:封装视频帧抽取。

```python
@dataclass(frozen=True)
class ExtractedFrame:
    path: Path
    timestamp: float

def extract_frames(
    input_path: Path,
    output_dir: Path,
    fps: float,
    filename_pattern: str,
) -> list[ExtractedFrame]: ...
```

语义:按固定 fps 从视频中抽取采样帧,返回实际生成的帧路径与时间戳列表。调用方负责根据返回列表构造 `VisualSampleIndex`。

实现要点:

- 使用 `ffmpeg`
- 输出目录由调用方保证存在
- `filename_pattern` 必须是单层文件名模式,例如 `frame_%06d.jpg`
- 抽帧前清理当前 `filename_pattern` 匹配的旧帧,不删除其他文件
- 返回列表按时间戳排序
- `timestamp` 由固定 fps 与 0-based 帧序号推导:`timestamp = frame_index / fps`,再经 `core.timestamps.normalize_seconds()` 归一到毫秒。第一版不处理 VFR 精确 PTS;如后续需要精确 PTS,再扩展 media API
- 输入没有 video stream 时抛 `MediaError`
- 第一版只支持固定 fps,不做 scene detect / 关键帧抽取 / 裁剪 / 缩放

### 3.4 `trim.py`

职责:封装 `--head-minutes` 输入裁剪文件创建与解析。

```python
def trim_media_head(input_path: Path, head_minutes: float, reuse: bool = True) -> Path: ...
def resolve_head_trim_path(input_path: Path, head_minutes: float) -> Path: ...
def make_head_trim_path(input_path: Path, head_minutes: float) -> Path: ...
```

语义:裁剪文件与输入文件放在同一目录,命名为 `<source-stem>.head-<minutes>m<source-suffix>`。写入型命令使用 `trim_media_head()` 创建或复用裁剪文件;`inspect` 使用 `resolve_head_trim_path()` 只解析已存在裁剪文件。

实现要点:

- 创建裁剪文件时使用 `core.locks.trim_output_lock()` 保护目标输出
- 使用临时文件生成,校验通过后原子替换目标裁剪文件
- `resolve_head_trim_path()` 不创建裁剪文件或锁文件
- 裁剪后校验输出文件存在、非空且包含 audio stream

---

## 4. Function Boundaries

| 函数 | 做什么 | 不做什么 |
|---|---|---|
| `probe_media` | 调 ffprobe,解析元信息 | 不写缓存、不推断处理模式 |
| `extract_wav` | 抽取 / 转码 wav | 不决定默认配置、不写 `extract.json` |
| `extract_frames` | 固定 fps 抽帧,返回帧路径与时间戳 | 不做视觉聚类、不生成 `VisualSampleIndex` |
| `trim_media_head` | 创建或复用开头裁剪文件 | 不计算 input hash、不创建 pipeline cache |
| `resolve_head_trim_path` | 解析并校验已存在裁剪文件 | 不创建裁剪文件、不创建锁文件 |

允许内部 helper:

```python
def _run_command(args: list[str], tool_name: str) -> subprocess.CompletedProcess[str]: ...
def _ensure_output_file(path: Path) -> None: ...
def _parse_fraction(value: str) -> float: ...
```

这些 helper 不从 `__init__.py` re-export。

---

## 5. Error Handling

所有 ffmpeg / ffprobe 相关可预期错误统一包装为 `MediaError`。

```python
try:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
except OSError as exc:
    raise MediaError(f"failed to execute ffmpeg: {exc}") from exc

if completed.returncode != 0:
    raise MediaError(f"ffmpeg failed: {completed.stderr}")
```

| 场景 | 处理 |
|---|---|
| 输入文件不存在 | `MediaError` |
| ffmpeg / ffprobe 不存在 | `MediaError` |
| 外部命令返回非 0 | `MediaError`,包含 stderr 摘要 |
| ffprobe 输出不是合法 JSON | `MediaError` |
| 输入无音频流但调用 `extract_wav` | `MediaError` |
| 输入无视频流但调用 `extract_frames` | `MediaError` |
| 输出文件不存在或为空 | `MediaError` |
| media 内部不变量违反 | `AssertionError` |

不做:

- 不吞异常
- 不返回 `None` 表示失败
- 不自动 fallback 到其他参数
- 不在 media 层重试 ffmpeg

---

## 6. Module Layout

```text
lvnotes/media/
├── __init__.py
├── probe.py
├── audio.py
├── trim.py
└── video.py
```

Import 规则:

| 来源 | 允许? |
|---|---|
| `core/exceptions.py` | ✅ |
| `core/locks.py` | ✅,用于 `--head-minutes` 裁剪输出锁 |
| `core/timestamps.py` | ✅,如需毫秒归一化 |
| `subprocess` | ✅,仅本模块 |
| `json` | ✅ |
| `audio_pipeline/` | ❌ |
| `visual_pipeline/` | ❌ |
| `merge/` | ❌ |
| `asr/` / `llm/` | ❌ |

---

## 7. Dependencies

项目内:

- `core/exceptions.py`: `MediaError`
- `core/locks.py`: 裁剪输出文件锁
- `core/timestamps.py`: 如需时间戳归一化

系统依赖:

- `ffmpeg`
- `ffprobe`

Python 标准库:

- `subprocess`
- `json`
- `pathlib`
- `dataclasses`
- `logging`

第一版不引入 `ffmpeg-python`。

---

## 8. Implementation Order

建议顺序:

1. 实现 `probe.py`
2. 实现 `audio.py`
3. 实现 `video.py`
4. 为三类公开函数补单元测试
5. 接入 `audio_pipeline/extract.py`
6. 接入 `visual_pipeline/sample.py`

验收标准:

1. `probe_media()` 能读取真实 30 秒音频和视频 fixture
2. `extract_wav()` 能抽出符合采样率与声道参数的 wav
3. `extract_frames()` 能按 fps 生成帧
4. ffmpeg / ffprobe 失败路径有测试覆盖
5. 非 `media/` 模块没有 `subprocess` 调 ffmpeg / ffprobe
6. 类型检查通过,公开函数无 `Any`
