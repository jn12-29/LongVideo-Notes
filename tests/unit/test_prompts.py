from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def _prompt(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _contains_asr_style(text: str) -> bool:
    return re.search(r"asr[- ]style", text, re.IGNORECASE) is not None


def test_refine_prompts_describe_lecture_scene_and_pronunciation_corrections() -> None:
    prompt_paths = [
        "lvnotes/audio_pipeline/prompts/refine.jinja",
        "lvnotes/audio_pipeline/prompts/refine_single.jinja",
        "lvnotes/audio_pipeline/prompts/refine_batch.jinja",
    ]

    for path in prompt_paths:
        text = _prompt(path)
        assert "Chinese lecture transcript" in text
        assert "automatically generated from speech" in text
        assert "pronunciation and surrounding context both strongly support" in text
        assert "homophones or near-homophones" in text
        assert "咸味" in text
        assert "纤维" in text
        assert "Do not invent names, facts, years, awards, or technical terms" in text
        assert "Write topic, cleaned_text, and summary in Simplified Chinese" in text
        assert "Keep English technical terms" in text
        assert not _contains_asr_style(text)


def test_structure_prompts_describe_lecture_note_tasks() -> None:
    segment = _prompt("lvnotes/audio_pipeline/prompts/segment.jinja")
    outline = _prompt("lvnotes/merge/prompts/outline.jinja")
    section = _prompt("lvnotes/merge/prompts/section.jinja")

    assert "timestamped transcript of a Chinese lecture" in segment
    assert "Avoid creating very short units" in segment
    assert "chapter outline for notes from a Chinese lecture" in outline
    assert "Do not make every block its own chapter" in outline
    assert "Write every chapter title and summary in Simplified Chinese" in outline
    assert "Keep English technical terms" in segment
    assert "Keep English technical terms" in outline
    assert "Keep English technical terms" in section
    assert "Do not translate established English terms mechanically" in segment
    assert "Chapter id is 1-based" in outline
    assert "Block ids are 0-based consecutive ids" in outline
    assert "previous chapter's block_id_end + 1" in outline
    assert "Cover every input block exactly once" in outline
    assert "cleaned Chinese transcript blocks" in section
    assert "level-2 heading" in section
    assert "Do not output # or ## headings" in section
    assert "You may use ### or #### subheadings" in section
    assert "do not create new reference markers" in section
    assert "timestamp marker at the start" in section
    assert "Do not place timestamp markers at the end" in section
    assert "in the middle of a sentence" in section
    assert "Do not include an end time or timestamp range" in section
    assert "copy every Visual markdown image" in section
    assert "preserving the image path and alt text" in section


def test_visual_prompts_require_chinese_but_preserve_english_terms() -> None:
    describe = _prompt("lvnotes/visual_pipeline/prompts/describe.jinja")
    semantic = _prompt("lvnotes/visual_pipeline/prompts/semantic_filter.jinja")

    assert "Write description in Simplified Chinese" in describe
    assert "complete, meaningful sentence" in describe
    assert "Write reason in Simplified Chinese" in semantic
    assert "Keep English technical terms" in describe
    assert "Keep English technical terms" in semantic
