"""One-shot: regenerate MoM from a saved transcript file."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.note_generator import generate_meeting_notes
from src.report_generator import generate_mom_markdown

transcript_path = Path(sys.argv[1])
raw = transcript_path.read_text(encoding="utf-8")

# Strip header (lines up to the separator) to match server behaviour
if "=" * 20 in raw:
    transcript_body = raw.split("=" * 60 + "\n\n", 1)[-1]
else:
    transcript_body = raw

header = {}
for line in raw.splitlines():
    if line.startswith("="):
        break
    if ":" in line:
        k, v = line.split(":", 1)
        header[k.strip().lower()] = v.strip()

print(f"[*] Transcript length: {len(transcript_body)} chars")
print(f"[*] Calling Claude CLI (timeout=600s) ...")

notes = generate_meeting_notes(
    transcript=transcript_body,
    title=header.get("title", ""),
    date=header.get("date", ""),
    chaired_by=header.get("chaired by", ""),
    attendees=header.get("attendees", ""),
    timeout=600,
)

if "error" in notes:
    print(f"[!] Error: {notes['error']}")
    sys.exit(1)

md_path = generate_mom_markdown(notes=notes, output_dir=str(ROOT / "output"))
print(f"[+] MoM written: {md_path}")
