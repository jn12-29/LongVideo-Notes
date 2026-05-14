import logging
from pathlib import Path
import shutil

from jinja2 import Template

from lvnotes.core.cache import atomic_write_json, build_cache_key, combined_content_hash, hash_json, hash_prompt_template
from lvnotes.core.context import PipelineContext
from lvnotes.core.exceptions import LLMError
from lvnotes.core.paths import resolve_visual_filter_image_path, resolve_visual_semantic_image_path
from lvnotes.core.pipeline import StageOutput
from lvnotes.core.schemas import SampledFrame, VisualSampleIndex, VisualSemanticJudgementList
from lvnotes.llm import ImagePart, LLMContentPart, LLMMessage, LLMRequestOptions, TextPart, complete_json, for_task

from lvnotes.visual_pipeline._common import cache_output, cached_output, prompt_path, read_samples

log = logging.getLogger(__name__)

_ALLOWED_MEDIA = {"ppt", "blackboard", "code", "demo", "chart", "table", "speaker", "blank", "ui", "other"}
_NON_MEANINGFUL_MEDIA = {"speaker", "blank", "ui"}
_CACHE_ALGORITHM = "semantic_filter_v2"


def run(ctx: PipelineContext) -> StageOutput:
    samples = read_samples(ctx.paths.visual_filtered_sample_json)
    template = prompt_path("semantic_filter.jinja")
    samples_hash = hash_json(samples)
    image_hash = combined_content_hash([resolve_visual_filter_image_path(ctx.paths, frame.image_source_path) for frame in samples.frames])
    prompt_hash = hash_prompt_template(template)
    profile_hash = hash_json(ctx.config.llm.profiles[ctx.config.tasks["slide_judge"]])
    cache_key = build_cache_key(
        "visual_semantic_filter",
        {"samples": samples_hash, "images": image_hash, "profile": profile_hash, "prompt": prompt_hash, "algorithm": _CACHE_ALGORITHM},
    )
    if not ctx.no_cache and ctx.paths.visual_semantic_sample_json.exists():
        semantic = read_samples(ctx.paths.visual_semantic_sample_json)
        expected_outputs = [
            ctx.paths.visual_semantic_sample_json,
            ctx.paths.visual_semantic_judgements_json,
            *[resolve_visual_semantic_image_path(ctx.paths, frame.image_source_path) for frame in semantic.frames],
        ]
        cached = cached_output("visual_semantic_filter", expected_outputs, cache_key)
        if cached is not None:
            return cached

    judgements = _judge_frames(ctx, samples, template)
    _validate_judgements(judgements, {frame.id for frame in samples.frames})
    kept = _copy_meaningful_frames(ctx, samples, judgements)
    semantic_index = VisualSampleIndex(frames=kept, duration=samples.duration)
    atomic_write_json(ctx.paths.visual_semantic_judgements_json, judgements)
    atomic_write_json(ctx.paths.visual_semantic_sample_json, semantic_index)
    outputs = [ctx.paths.visual_semantic_sample_json, ctx.paths.visual_semantic_judgements_json, *[resolve_visual_semantic_image_path(ctx.paths, frame.image_source_path) for frame in kept]]
    return cache_output(
        "visual_semantic_filter",
        outputs,
        cache_key,
        {"filtered_samples": samples_hash, "filtered_images": image_hash},
        "",
        prompt_hash,
        {"item_count": len(kept)},
    )


def _judge_frames(ctx: PipelineContext, samples: VisualSampleIndex, template: Path) -> VisualSemanticJudgementList:
    prompt = Template(template.read_text(encoding="utf-8")).render(frames=samples.frames)
    parts: list[LLMContentPart] = [TextPart(text=prompt)]
    for frame in samples.frames:
        parts.append(TextPart(text=f"frame_id={frame.id} timestamp={frame.timestamp:.3f}"))
        parts.append(ImagePart(path=resolve_visual_filter_image_path(ctx.paths, frame.image_source_path), mime_type="image/png"))
    return complete_json(for_task(ctx.config, "slide_judge"), [LLMMessage(role="user", content=parts)], VisualSemanticJudgementList, LLMRequestOptions(temperature=0.1), 1)


def _validate_judgements(judgements: VisualSemanticJudgementList, frame_ids: set[int]) -> None:
    seen: set[int] = set()
    for judgement in judgements.judgements:
        if judgement.frame_id not in frame_ids or judgement.frame_id in seen:
            raise LLMError("visual semantic filter invariant failed")
        seen.add(judgement.frame_id)
        if judgement.medium not in _ALLOWED_MEDIA:
            raise LLMError("visual semantic filter invariant failed")
        if not judgement.reason.strip():
            raise LLMError("visual semantic filter invariant failed")
        if judgement.is_meaningful:
            if judgement.medium in _NON_MEANINGFUL_MEDIA:
                raise LLMError("visual semantic filter invariant failed")
            if judgement.semantic_key is None or not judgement.semantic_key.strip():
                raise LLMError("visual semantic filter invariant failed")
            if judgement.quality_score is None or not 1 <= judgement.quality_score <= 5:
                raise LLMError("visual semantic filter invariant failed")
            if not judgement.content_summary.strip():
                raise LLMError("visual semantic filter invariant failed")
        else:
            if judgement.semantic_key is not None:
                raise LLMError("visual semantic filter invariant failed")
            if judgement.quality_score is not None:
                raise LLMError("visual semantic filter invariant failed")
    if seen != frame_ids:
        raise LLMError("visual semantic filter invariant failed")


def _copy_meaningful_frames(ctx: PipelineContext, samples: VisualSampleIndex, judgements: VisualSemanticJudgementList) -> list[SampledFrame]:
    ctx.paths.visual_semantic_frames_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_semantic_frames(ctx.paths.visual_semantic_frames_dir)
    selected = _select_representative_frames(ctx, samples, judgements)
    kept: list[SampledFrame] = []
    for frame in samples.frames:
        if frame.id not in selected:
            continue
        source = resolve_visual_filter_image_path(ctx.paths, frame.image_source_path)
        target = ctx.paths.visual_semantic_frames_dir / frame.image_source_path.name
        shutil.copy2(source, target)
        kept.append(SampledFrame(id=frame.id, timestamp=frame.timestamp, image_source_path=target.relative_to(ctx.paths.visual_semantic_frames_dir)))
    return kept


def _select_representative_frames(ctx: PipelineContext, samples: VisualSampleIndex, judgements: VisualSemanticJudgementList) -> set[int]:
    frame_by_id = {frame.id: frame for frame in samples.frames}
    groups: dict[str, list[tuple[SampledFrame, int, int, float]]] = {}
    for judgement in judgements.judgements:
        if not judgement.is_meaningful:
            continue
        assert judgement.semantic_key is not None
        assert judgement.quality_score is not None
        frame = frame_by_id[judgement.frame_id]
        text_score = _visible_text_score(judgement.visible_text)
        sharpness = _sharpness_score(resolve_visual_filter_image_path(ctx.paths, frame.image_source_path))
        groups.setdefault(judgement.semantic_key.strip(), []).append((frame, judgement.quality_score, text_score, sharpness))
    return {_best_group_frame(candidates).id for candidates in groups.values()}


def _best_group_frame(candidates: list[tuple[SampledFrame, int, int, float]]) -> SampledFrame:
    frame, _, _, _ = max(candidates, key=lambda candidate: (candidate[1], candidate[2], candidate[3], -candidate[0].id))
    return frame


def _visible_text_score(text: str) -> int:
    return len("".join(text.split()))


def _sharpness_score(path: Path) -> float:
    try:
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return 0.0
        return float(cv2.Laplacian(image, cv2.CV_64F).var())
    except Exception:
        log.debug("failed to score image sharpness path=%s", path, exc_info=True)
        return 0.0


def _remove_stale_semantic_frames(frames_dir: Path) -> None:
    for path in frames_dir.glob("*.png"):
        path.unlink()
