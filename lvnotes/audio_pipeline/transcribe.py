import logging

from lvnotes.asr import create_transcriber
from lvnotes.core.cache import atomic_write_json, build_cache_key, hash_file, hash_json
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import ASRError
from lvnotes.core.pipeline import StageOutput

from lvnotes.audio_pipeline._common import cache_output, cached_output

log = logging.getLogger(__name__)


def run(ctx: PipelineContext) -> StageOutput:
    extract = ctx.artifacts.audio.get_extract()
    audio_hash = hash_file(extract.audio_path)
    config_hash = hash_json(ctx.config.asr)
    cache_key = build_cache_key(
        "transcribe",
        {"audio": audio_hash, "config": config_hash, "backend_version": hash_json({"backend": ctx.config.asr.backend})},
    )
    output_paths = [ctx.paths.transcript_raw_json]
    if not ctx.no_cache:
        cached = cached_output("transcribe", output_paths, cache_key)
        if cached is not None:
            log.info("audio.transcribe cache hit input_hash=%s", ctx.input_hash)
            return cached

    transcript = create_transcriber(ctx.config.asr).transcribe(extract.audio_path, ctx.config.asr)
    if not transcript.segments:
        raise ASRError("no speech detected")
    atomic_write_json(ctx.paths.transcript_raw_json, transcript)
    return cache_output(
        "transcribe",
        output_paths,
        cache_key,
        {"audio": audio_hash},
        config_hash,
        None,
        {"item_count": len(transcript.segments), "duration_seconds": transcript.duration},
    )
