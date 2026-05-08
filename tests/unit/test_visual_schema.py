from pathlib import Path

from lvnotes.core.schemas import (
    SampledFrame,
    VisualDescription,
    VisualDescriptionList,
    VisualSampleIndex,
    VisualSelection,
)


def test_visual_schema_matches_pipeline_contract() -> None:
    samples = VisualSampleIndex(
        frames=[SampledFrame(id=1, timestamp=1.0, image_source_path=Path("000001.png"))],
        duration=2.0,
    )
    selection = VisualSelection(
        segment_id=3,
        frame_id=1,
        start=1.0,
        end=2.0,
        image_source_path=Path("000001.png"),
        medium="ppt",
    )
    descriptions = VisualDescriptionList(
        descriptions=[
            VisualDescription(
                segment_id=selection.segment_id,
                frame_id=selection.frame_id,
                start=selection.start,
                end=selection.end,
                image_source_path=selection.image_source_path,
                medium=selection.medium,
                description="slide summary",
            )
        ]
    )

    assert samples.duration == 2.0
    assert descriptions.descriptions[0].frame_id == selection.frame_id
