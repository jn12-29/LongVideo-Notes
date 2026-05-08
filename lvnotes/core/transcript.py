from lvnotes.core.schemas import Transcript


def slice_transcript_text(transcript: Transcript, start: float, end: float) -> str:
    if start >= end:
        raise ValueError("start must be less than end")
    pieces: list[str] = []
    for segment in transcript.segments:
        if segment.start >= end or segment.end <= start:
            continue
        if not segment.words:
            pieces.append(segment.text)
            continue
        words = [word.word for word in segment.words if _word_intersects(word.start, word.end, start, end) and word.word]
        if words:
            pieces.append("".join(words))
    return " ".join(piece.strip() for piece in pieces if piece.strip())


def _word_intersects(word_start: float, word_end: float, start: float, end: float) -> bool:
    if word_start == word_end:
        return start <= word_start < end
    return word_start < end and word_end > start
