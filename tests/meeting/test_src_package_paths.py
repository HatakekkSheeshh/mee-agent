from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]

SKIPPED_PARTS = {
    ".git",
    ".agents",
    ".codex",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "docs/superpowers",
    "docs/html",
    "docs/doctrees",
}

SCANNED_SUFFIXES = {".py", ".sh", ".ini", ".yml", ".yaml", ".md"}
SCANNED_NAMES = {"Dockerfile", "Dockerfile.backend"}

STALE_PATTERNS = {
    "old backend import": re.compile(r"^\s*(?:from|import)\s+meeting(?=\.|\s|$)", re.MULTILINE),
    "old backend module string": re.compile(
        r"meeting\.(?:app|celery_app|tasks|db|api|auth|graphs|services|memory_client|note_generator|report_generator|ws_transcribe)"
    ),
    "old backend filesystem path": re.compile(
        r"(?:COPY\s+meeting/|--directory=meeting|reload_dirs=.*[\"']meeting[\"'])"
    ),
    "old frontend directory": re.compile(r"meeting_frontend(?:_react)?"),
}


def _is_skipped(path: Path) -> bool:
    if path == Path(__file__):
        return True
    rel = path.relative_to(ROOT)
    rel_text = rel.as_posix()
    return any(part in rel.parts or rel_text.startswith(f"{part}/") for part in SKIPPED_PARTS)


def _is_scanned(path: Path) -> bool:
    return path.suffix in SCANNED_SUFFIXES or path.name in SCANNED_NAMES


def test_active_files_do_not_reference_old_meeting_package_paths() -> None:
    violations: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or _is_skipped(path) or not _is_scanned(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in STALE_PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(ROOT)}:{line_no}: {label}: {match.group(0)!r}")

    assert violations == []
