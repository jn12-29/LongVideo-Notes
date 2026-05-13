from dataclasses import dataclass
from pathlib import Path
import re
import shutil


ARCHIVE_DIR_NAME = "_archive"
_TIMESTAMPED_NOTE_RE = re.compile(r"^(.+)-\d{8}-\d{6}$")


@dataclass(frozen=True)
class OutputTidyMove:
    source_md: Path
    destination_md: Path
    source_assets: Path | None = None
    destination_assets: Path | None = None


@dataclass(frozen=True)
class OutputTidyConflict:
    source: Path
    destination: Path
    reason: str


@dataclass(frozen=True)
class OutputTidyPlan:
    output_dir: Path
    archive_root: Path
    moves: list[OutputTidyMove]
    conflicts: list[OutputTidyConflict]


def build_output_tidy_plan(output_dir: Path) -> OutputTidyPlan:
    output_dir = output_dir.expanduser().resolve()
    if not output_dir.exists():
        raise ValueError(f"output directory not found: {output_dir}")
    if not output_dir.is_dir():
        raise ValueError(f"output path is not a directory: {output_dir}")

    archive_root = output_dir / ARCHIVE_DIR_NAME
    moves: list[OutputTidyMove] = []
    conflicts: list[OutputTidyConflict] = []
    for path in sorted(output_dir.rglob("*.md"), key=lambda item: item.relative_to(output_dir).as_posix()):
        if path.is_symlink() or not path.is_file() or _is_in_archive(path, output_dir):
            continue
        match = _TIMESTAMPED_NOTE_RE.fullmatch(path.stem)
        if match is None:
            continue
        latest_path = path.with_name(f"{match.group(1)}.md")
        if latest_path.is_symlink() or not latest_path.is_file():
            continue

        relative_parent = path.parent.relative_to(output_dir)
        destination_md = archive_root / relative_parent / path.name
        source_assets = path.with_name(f"{path.stem}_assets")
        destination_assets = destination_md.with_name(f"{destination_md.stem}_assets")
        move = OutputTidyMove(source_md=path, destination_md=destination_md)
        if source_assets.exists() or source_assets.is_symlink():
            if not source_assets.is_dir() or source_assets.is_symlink():
                conflicts.append(OutputTidyConflict(source_assets, destination_assets, "assets path is not a normal directory"))
            else:
                move = OutputTidyMove(path, destination_md, source_assets, destination_assets)
        parent_conflict = _destination_parent_conflict(destination_md.parent, output_dir)
        if parent_conflict is not None:
            conflicts.append(OutputTidyConflict(path, parent_conflict, "destination parent is not a directory"))
        if destination_md.exists() or destination_md.is_symlink():
            conflicts.append(OutputTidyConflict(path, destination_md, "destination markdown already exists"))
        if move.source_assets is not None and move.destination_assets is not None:
            parent_conflict = _destination_parent_conflict(move.destination_assets.parent, output_dir)
            if parent_conflict is not None:
                conflicts.append(OutputTidyConflict(move.source_assets, parent_conflict, "destination parent is not a directory"))
            if move.destination_assets.exists() or move.destination_assets.is_symlink():
                conflicts.append(OutputTidyConflict(move.source_assets, move.destination_assets, "destination assets already exists"))
        moves.append(move)
    return OutputTidyPlan(output_dir, archive_root, moves, conflicts)


def apply_output_tidy_plan(plan: OutputTidyPlan) -> None:
    if plan.conflicts:
        raise ValueError("cannot apply output tidy plan with conflicts")
    for move in plan.moves:
        if move.destination_md.exists() or move.destination_md.is_symlink():
            raise ValueError(f"destination markdown already exists: {move.destination_md}")
        if move.destination_assets is not None and (move.destination_assets.exists() or move.destination_assets.is_symlink()):
            raise ValueError(f"destination assets already exists: {move.destination_assets}")
        move.destination_md.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(move.source_md), str(move.destination_md))
        if move.source_assets is not None and move.destination_assets is not None:
            shutil.move(str(move.source_assets), str(move.destination_assets))


def _is_in_archive(path: Path, output_dir: Path) -> bool:
    relative = path.relative_to(output_dir)
    return bool(relative.parts) and relative.parts[0] == ARCHIVE_DIR_NAME


def _destination_parent_conflict(parent: Path, output_dir: Path) -> Path | None:
    current = output_dir
    try:
        relative = parent.relative_to(output_dir)
    except ValueError:
        return parent
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if not current.is_dir() or current.is_symlink():
                return current
    return None
