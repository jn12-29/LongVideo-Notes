import logging

from lvnotes.core.cache import build_cache_key, hash_file, hash_json, atomic_write_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import AudioExtractResult
from lvnotes.media.audio import extract_wav
from lvnotes.media.probe import probe_media

from lvnotes.audio_pipeline._common import cache_output, cached_output

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    cfg = ctx.config.audio_pipeline.extract
    input_hash = hash_file(ctx.source_path)
    config_hash = hash_json(cfg)
    cache_key = build_cache_key("extract", {"input": input_hash, "config": config_hash})
    output_paths = [ctx.paths.audio_wav, ctx.paths.audio_extract_json]
    if not ctx.no_cache:
        cached = cached_output(
            "extract", output_paths, cache_key, manifest_output_path=ctx.paths.audio_extract_json
        )
        if cached is not None:
            log.info("audio.extract cache hit input_hash=%s", ctx.input_hash)
            return cached

    source_probe = probe_media(ctx.source_path)
    if source_probe.audio is None:
        raise AssertionError("input must have an audio stream before extraction")
    extract_wav(ctx.source_path, ctx.paths.audio_wav, cfg.sample_rate, cfg.channels)
    wav_probe = probe_media(ctx.paths.audio_wav)

    result = AudioExtractResult(
        audio_path=ctx.paths.audio_wav,
        duration=wav_probe.duration,
        sample_rate=cfg.sample_rate,
        channels=cfg.channels,
        source_path=ctx.source_path,
        source_codec=source_probe.audio.codec,
        source_sample_rate=source_probe.audio.sample_rate,
        source_channels=source_probe.audio.channels,
    )
    atomic_write_json(ctx.paths.audio_extract_json, result)
    return cache_output(
        "extract",
        output_paths,
        cache_key,
        {"input": input_hash},
        config_hash,
        None,
        {"duration_seconds": result.duration},
        manifest_output_path=ctx.paths.audio_extract_json,
    )
