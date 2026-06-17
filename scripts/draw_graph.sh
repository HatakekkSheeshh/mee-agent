#!/usr/bin/env bash
#
# draw_graph.sh — render the LangGraph graphs to Mermaid (.mmd) diagrams.
#
# Uses LangGraph's built-in CompiledGraph.get_graph().draw_mermaid() (and
# draw_mermaid_png() with --png). Building a graph only constructs its
# structure — no DB connection is made — so a dummy DATABASE_URL is enough.
#
# Usage:
#   scripts/draw_graph.sh                # render all graphs → docs/diagrams/*.mmd
#   scripts/draw_graph.sh chat           # only the chat agent graph
#   scripts/draw_graph.sh mom            # only the MoM graph
#   scripts/draw_graph.sh chat --png     # also render a PNG (needs network: mermaid.ink)
#   scripts/draw_graph.sh --out /tmp/d   # custom output dir
#
set -euo pipefail

# Repo root = parent of this script's dir.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

PY="$ROOT/venv/bin/python"
[ -x "$PY" ] || PY="python3"

WHICH="all"
OUT="$ROOT/docs/diagrams"
PNG=0
while [ $# -gt 0 ]; do
  case "$1" in
    chat|mom|all) WHICH="$1" ;;
    --png) PNG=1 ;;
    --out) shift; OUT="$1" ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$OUT"

WHICH="$WHICH" OUT="$OUT" PNG="$PNG" "$PY" - <<'PY'
import os
import sys

# Building a graph imports src.db.base, which needs DATABASE_URL at import
# time (engine is lazy — no connection). Seed a dummy, then let a real .env
# override it if present.
os.environ.setdefault("DATABASE_URL", "postgresql://t:t@localhost:5432/t")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"), override=True, interpolate=False)
except Exception:
    pass

from langgraph.checkpoint.memory import MemorySaver

which = os.environ["WHICH"]
out_dir = os.environ["OUT"]
want_png = os.environ["PNG"] == "1"


def _chat():
    from src.graphs.chat_graph import build_chat_graph
    return build_chat_graph(session=object(), checkpointer=MemorySaver())


def _mom():
    from src.graphs.mom_graph import build_mom_graph
    from src.services.memory_service import get_memory_service
    return build_mom_graph(session=object(), memory_service=get_memory_service(),
                           checkpointer=MemorySaver())


builders = {"chat": _chat, "mom": _mom}
targets = list(builders) if which == "all" else [which]

for name in targets:
    try:
        graph = builders[name]().get_graph()
    except Exception as e:
        print(f"[skip] {name}: could not build graph: {e}", file=sys.stderr)
        continue

    mmd_path = os.path.join(out_dir, f"{name}_graph.mmd")
    with open(mmd_path, "w", encoding="utf-8") as f:
        f.write(graph.draw_mermaid())
    print(f"[ok] {mmd_path}")

    if want_png:
        png_path = os.path.join(out_dir, f"{name}_graph.png")
        try:
            with open(png_path, "wb") as f:
                f.write(graph.draw_mermaid_png())
            print(f"[ok] {png_path}")
        except Exception as e:
            print(f"[warn] {name}: PNG render failed ({e}). "
                  f"The .mmd is fine — paste it into https://mermaid.live", file=sys.stderr)
PY

echo "Done. Diagrams in: $OUT"
