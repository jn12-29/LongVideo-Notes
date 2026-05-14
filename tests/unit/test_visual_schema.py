from pathlib import Path

from lvnotes.core.schemas import (
    SampledFrame,
    VisualAlignment,
    VisualDescription,
    VisualDescriptionList,
    VisualSampleIndex,
    VisualSemanticJudgement,
)


def test_visual_schema_matches_pipeline_contract() -> None:
    samples = VisualSampleIndex(
        frames=[SampledFrame(id=1, timestamp=1.0, image_source_path=Path("000001.png"))],
        duration=2.0,
    )
    alignment = VisualAlignment(
        segment_id=3,
        frame_id=1,
        timestamp=1.0,
        image_source_path=Path("000001.png"),
        medium="ppt",
        has_audio_context=True,
    )
    judgement = VisualSemanticJudgement(
        frame_id=1,
        medium="ppt",
        is_meaningful=True,
        reason="content slide",
        semantic_key="slide-title",
        quality_score=5,
        visible_text="AI Safety",
        content_summary="Title slide",
    )
    descriptions = VisualDescriptionList(
        descriptions=[
            VisualDescription(
                segment_id=alignment.segment_id,
                frame_id=alignment.frame_id,
                start=1.0,
                end=2.0,
                image_source_path=alignment.image_source_path,
                medium=alignment.medium,
                description="slide summary",
                visible_text="AI Safety",
                visible_evidence=["title: AI Safety"],
            )
        ]
    )

    assert samples.duration == 2.0
    assert judgement.semantic_key == "slide-title"
    assert descriptions.descriptions[0].frame_id == alignment.frame_id
    assert descriptions.descriptions[0].visible_evidence == ["title: AI Safety"]
