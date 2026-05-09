from pathlib import Path
from types import SimpleNamespace

from lvnotes.audio_pipeline import extract
from lvnotes.core.cache import read_json_file
from lvnotes.core.config import AppConfig
from lvnotes.core.context import ArtifactBundle, PipelineContext
from lvnotes.core.paths import build_paths
from lvnotes.core.schemas import AudioExtractResult


def test_extract_allows_wav_duration_to_differ_from_source(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"source")
    paths = build_paths(source, tmp_path / "cache", tmp_path / "output", "inputhash")
    paths.audio_dir.mkdir(parents=True)
    ctx = PipelineContext(source, "inputhash", "audio_only", _config(), paths, ArtifactBundle(audio=SimpleNamespace()), no_cache=True)

    def extract_wav(input_path: Path, output_path: Path, sample_rate: int, channels: int) -> Path:
        output_path.write_bytes(b"wav")
        return output_path

    def probe_media(path: Path) -> SimpleNamespace:
        if path == source:
            return SimpleNamespace(
                duration=1201.0,
                audio=SimpleNamespace(codec="aac", sample_rate=48000, channels=2, duration=1199.8),
            )
        return SimpleNamespace(duration=1199.8, audio=SimpleNamespace(codec="pcm_s16le", sample_rate=16000, channels=1))

    monkeypatch.setattr(extract, "extract_wav", extract_wav)
    monkeypatch.setattr(extract, "probe_media", probe_media)

    output = extract.run(ctx)

    result = read_json_file(paths.audio_extract_json, AudioExtractResult)
    assert output.stage_name == "extract"
    assert result.duration == 1199.8
    assert result.source_path == source


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {
                "profiles": {
                    "main": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test"},
                    "vlm": {"provider": "openai_compatible_chat", "base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "test", "capabilities": ["vision"]},
                }
            },
            "tasks": {"segment": "main", "refine": "main", "outline": "main", "section": "main", "slide_judge": "vlm", "slide_describe": "main"},
        }
    )
