"""
Transcript Cleaner — LLM post-process for readable transcript view (Sprint C).

Input:  raw transcript từ Whisper (1 dòng dài, có filler words, no speaker labels)
Output: structured clean view:
    [
      {"speaker": "Linh",  "text": "Hôm nay mình review backlog. Tuấn đã deploy v1 chưa?", "tags": []},
      {"speaker": "Tuấn",  "text": "Em deploy rồi anh, không bug.", "tags": ["commitment"]},
      {"speaker": "Linh",  "text": "OK vậy mai bắt đầu sprint mới nhé.", "tags": ["decision"]},
    ]

LLM tasks:
1. Detect speaker từ context (xưng hô "em"/"anh", gọi tên trực tiếp, attendees list)
2. Group consecutive sentences của cùng speaker thành 1 block
3. Bỏ filler words: "ờ", "um", "à", "dạ", "thì là", "kiểu", "you know", "domixi", "anh nghĩ là", "kiểu như"
4. Add punctuation đúng + normalize wording nhẹ
5. Tag mỗi block: commitment / decision / blocker / question / update (optional)

Phase B đã có few-shot prompt tương tự trong note_generator.py — cleaner ở đây
là cấp thấp hơn (segment-level), không tóm tắt mà chỉ format lại transcript.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# Context budget depends on the chosen LLM. Used as a default at import
# time (legacy env-driven path); when a caller passes `llm_profile`, the
# profile's context_chars + max_tokens override these per-call.
def _budget_for_model(model_lc: str) -> tuple[int, int]:
    if "gemma-4" in model_lc or "gemma-3" in model_lc or "gemini" in model_lc:
        return 60_000, 16_000
    if "gpt-4o" in model_lc or "gpt-5" in model_lc or "gpt-oss" in model_lc:
        return 60_000, 16_000
    if "claude" in model_lc:
        return 80_000, 16_000
    # Qwen3-5-27b on MaaS or self-hosted Qwen3-8B at 16K — keep conservative.
    return 14_000, 8_000


_model_lc = (os.getenv("LLM_MODEL", "") or "").lower()
MAX_TRANSCRIPT_CHARS, LLM_MAX_TOKENS = _budget_for_model(_model_lc)


CLEAN_PROMPT = """Bạn là editor chuyên format lại transcript cuộc họp tiếng Việt cho dễ đọc.

## Attendees (có thể có trong cuộc họp)
{attendees}

## Pre-mapped speakers (đã nhận diện qua voice matching từ DB)
{pre_mapped}

## Thuật ngữ kỹ thuật trong cuộc họp này
{vocab_hints}

## Quy tắc sửa Whisper mistranscription
Whisper ASR thường nhầm thuật ngữ tiếng Anh thành phonetic tiếng Việt vô nghĩa.

### Mapping cụ thể cho meeting này (sinh tự động từ vocab):
{phonetic_examples}

### Ví dụ chung (pattern phonetic VN ↔ EN)
  - "chất manh tây sành" → "segmentation"
  - "công vô lu sần"     → "convolution"
  - "tích cốt"           → "ticket"
  - "đi pờ lôi"          → "deploy"

Nếu thấy cụm tiếng Việt KHÔNG có nghĩa rõ ràng nằm trong ngữ cảnh tech →
thử match với thuật ngữ trong list ở trên (theo phát âm phonetic) → REPLACE.
ƯU TIÊN dùng mapping cụ thể trước, fallback sang pattern chung.
KHÔNG được sửa các cụm tiếng Việt có nghĩa thông thường — chỉ sửa cụm rõ
ràng là mistranscription tech term.

## Raw transcript (từ Whisper STT, có thể có nhãn SPEAKER_00/01... từ pyannote)
{transcript}

## Nhiệm vụ
Re-format raw transcript ở trên thành **dạng có cấu trúc** với speaker labels:

1. **Detect speaker** cho mỗi câu/đoạn — theo thứ tự ưu tiên:
   a) **Pre-mapped**: nếu raw có nhãn "SPEAKER_NN" mà nhãn đó đã có trong
      pre-mapped list ở trên → DÙNG NGAY tên đó (đây là voice match từ DB,
      độ tin cậy cao)
   b) **Self-introduction**: phát hiện câu kiểu "Mình là <Tên>", "Đây là <Tên> nói",
      "Em là <Tên> từ team backend" → bind nhãn SPEAKER_NN với tên đã nghe được
      cho toàn bộ phần còn lại. <Tên> PHẢI là tên xuất hiện trong raw transcript,
      KHÔNG được tự bịa.
   c) **Context cues**: xưng hô (em/anh/chị), gọi tên trực tiếp ("<Tên> ơi"),
      topic ownership ("<Tên> phụ trách deploy") — <Tên> CHỈ lấy từ raw transcript.
   d) **Attendees list**: nếu thấy tên trong attendees được nhắc → có thể là
      cluster đó
   e) Không detect được → **GIỮ NGUYÊN nhãn SPEAKER_NN** (vd "SPEAKER_00")
      ở `segments[].speaker`. TUYỆT ĐỐI KHÔNG collapse nhiều cluster về cùng
      chuỗi "Unknown" — sẽ mất thông tin phân biệt giọng. Riêng trong
      `cluster_mapping`, dùng giá trị "Unknown" để báo cho UI biết là chưa
      suy ra được tên.
   QUAN TRỌNG: Cùng một nhãn SPEAKER_NN xuyên suốt PHẢI map sang CÙNG 1 tên.
   **CỰC KỲ QUAN TRỌNG**: Tên speaker BẮT BUỘC phải xuất hiện trong raw
   transcript hoặc trong attendees list ở trên. TUYỆT ĐỐI KHÔNG sinh ra tên
   tùy ý từ ví dụ trong prompt này, từ kiến thức training, hoặc đoán mò. Nếu
   raw transcript là độc thoại không có ai gọi tên ai → mặc định Unknown,
   KHÔNG được điền "Linh", "Tuấn", "Mai" hay bất kỳ tên thông dụng nào khác.
2. **Group consecutive sentences** của CÙNG 1 speaker thành 1 block
   - QUAN TRỌNG: Nếu block dài (>3 câu), thêm `\\n\\n` (xuống dòng kép) để
     tách thành PARAGRAPHS theo SUB-TOPIC. Ví dụ: 1 paragraph nói về deploy,
     paragraph khác nói về testing. Mỗi paragraph 2-4 câu là đẹp.
   - Nếu speaker chuyển chủ đề rõ ràng (vd "OK chuyển qua chủ đề khác") → paragraph mới
   - Mục tiêu: text DỄ ĐỌC, không phải 1 đoạn dài lê thê
3. **Bỏ filler words**: "ờ", "um", "à", "dạ", "thì là", "kiểu", "anh nghĩ là", "kiểu như"
4. **Add punctuation** đúng (dấu chấm, hỏi, phẩy) + normalize wording nhẹ
5. **Tag mỗi block** với category nếu rõ ràng (mảng rỗng nếu không chắc):
   - `commitment` — ai cam kết làm gì (vd "em sẽ làm xong cuối tuần")
   - `decision` — đã chốt cái gì (vd "chúng ta dùng Postgres")
   - `blocker` — đang stuck (vd "database migration đang block")
   - `question` — câu hỏi (vd "ai làm xong chưa?")
   - `update` — báo cáo tình trạng (vd "v1 deploy thành công")

## QUY TẮC TUYỆT ĐỐI VỀ ĐỘ DÀI (đọc trước tiên!)
🚨 **NHIỆM VỤ LÀ FORMAT LẠI, KHÔNG PHẢI TÓM TẮT** 🚨
- Tổng số ký tự output phải ≈ tổng số ký tự raw (chênh lệch ±20% chấp nhận
  do bỏ filler + thêm dấu câu). KHÔNG được rút ngắn còn 50% hay ít hơn.
- MỌI câu trong raw PHẢI xuất hiện trong output (có thể merge 2-3 câu liền
  cùng speaker thành 1 paragraph, nhưng KHÔNG được bỏ câu).
- KHÔNG được gộp 5-10 câu thành 1 câu tóm tắt. Mỗi ý nói trong raw phải
  có câu tương ứng trong output.
- KHÔNG diễn giải/paraphrase tự do. Giữ NGUYÊN từ ngữ raw, chỉ sửa lỗi
  Whisper transcription + thêm dấu câu + bỏ filler.

VÍ DỤ ĐÚNG (giữ chi tiết):
Raw: "anh em đã demo kết tạo sắp cây kém rồi được rồi em anh vũ rồi thì
      cái đó ok rồi thì cái mấy cái tác kế tiếp là..."
Output đúng: "Em đã demo kết tạo, sắp xong rồi. Anh Vũ duyệt rồi thì cái
              đó OK. Các task kế tiếp là..."
Output SAI (tóm tắt): "Em đã demo và các task tiếp theo là..." ← MẤT chi tiết

## Lưu ý khác
- Giữ nguyên các từ tiếng Anh kỹ thuật: deploy, API, sprint, backlog, v.v.
- Không tự bịa thông tin. Nếu raw không rõ, để text gần với raw nhất.

## Output format
Trả về CHỈ JSON hợp lệ (không markdown fences):
{{
  "cluster_mapping": {{
    "SPEAKER_00": "<tên đã suy ra, hoặc 'Unknown' nếu không chắc>",
    "SPEAKER_01": "<...>"
  }},
  "segments": [
    {{
      "speaker": "<tên đã map nếu cluster_mapping có tên cụ thể; nếu cluster_mapping = 'Unknown' thì GIỮ NGUYÊN nhãn cluster (vd 'SPEAKER_00') — KHÔNG ghi 'Unknown'>",
      "text": "<câu nói đã clean>",
      "tags": ["commitment" | "decision" | "blocker" | "question" | "update"]
    }}
  ]
}}

QUAN TRỌNG:
- `cluster_mapping` PHẢI chứa MỌI nhãn SPEAKER_NN xuất hiện trong raw transcript.
- Nếu raw transcript KHÔNG có nhãn SPEAKER_NN (Whisper plain output), bỏ qua
  cluster_mapping (để empty {{}}).
- Quy tắc `segments[].speaker`:
  * Nếu `cluster_mapping[SPEAKER_NN]` = "<tên cụ thể>" → segments dùng "<tên cụ thể>".
  * Nếu `cluster_mapping[SPEAKER_NN]` = "Unknown" → segments PHẢI dùng "SPEAKER_NN"
    (raw cluster id), KHÔNG dùng "Unknown". Cách này giúp giữ phân biệt giữa
    SPEAKER_00 và SPEAKER_01 dù chưa biết tên.
"""


def clean_transcript(
    raw_text: str,
    attendees: Optional[str] = None,
    pre_mapped: Optional[dict[str, str]] = None,
    vocab_hints: Optional[str] = None,
    phonetic_examples: Optional[list[dict]] = None,
    llm_profile: Optional[dict] = None,
    timeout: int = 600,
) -> dict:
    """
    Run LLM post-process to clean + structure transcript.

    Args:
        raw_text: full raw transcript text (joined segments). May contain
                  pyannote speaker labels like "SPEAKER_00: ..."
        attendees: comma-separated names of attendees, vd "Linh, Tuấn, Mai"
        pre_mapped: voice-matched cluster_id → name from voiceprints DB,
                    vd {"SPEAKER_00": "Linh"}. LLM uses these as ground truth.
        vocab_hints: comma-separated technical terms from meeting.vocab_hints,
                    vd "segmentation, CNN, deploy, API". LLM uses these to
                    fix Vietnamese phonetic mistranscriptions.
        phonetic_examples: dynamic few-shot mappings from phonetic_generator,
                    vd [{"wrong": "công vô lu sần", "correct": "convolution"}].
                    Cached on recording.phonetic_examples_json — replaces the
                    old hardcoded prompt examples. Pass None for fallback to
                    the generic pattern examples baked into the prompt.
        timeout: LLM timeout in seconds

    Returns:
        {"segments": [{speaker, text, tags}, ...]}
        OR {"error": "..."}
    """
    if not raw_text or not raw_text.strip():
        return {"segments": []}

    # Resolve per-call chunk size — profile beats env default.
    if llm_profile:
        chunk_chars = int(llm_profile.get("context_chars") or MAX_TRANSCRIPT_CHARS)
    else:
        chunk_chars = MAX_TRANSCRIPT_CHARS

    # Long transcripts: split into chunks of chunk_chars, clean each
    # independently, then concatenate the segments + merge cluster_mappings.
    # Truncating was the old behavior — silently dropped the tail and we
    # ended up with a 1/3-length clean view for a 1h meeting.
    if len(raw_text) > chunk_chars:
        logger.info(
            f"Transcript is {len(raw_text)} chars — splitting into chunks of "
            f"{chunk_chars} for the cleaner."
        )
        chunks: list[str] = []
        # Split on paragraph boundaries when possible so a sentence isn't cut.
        pos = 0
        while pos < len(raw_text):
            end = min(pos + chunk_chars, len(raw_text))
            if end < len(raw_text):
                # Snap back to nearest newline / period within the last 500 chars
                snap_window = raw_text.rfind("\n", pos, end)
                if snap_window == -1 or snap_window < pos + chunk_chars // 2:
                    snap_window = raw_text.rfind(". ", pos, end)
                if snap_window > pos + chunk_chars // 2:
                    end = snap_window + 1
            chunks.append(raw_text[pos:end])
            pos = end

        all_segments: list[dict] = []
        merged_mapping: dict[str, str] = {}
        for i, chunk in enumerate(chunks, 1):
            logger.info(f"  Cleaning chunk {i}/{len(chunks)} ({len(chunk)} chars)")
            sub = _clean_one(
                chunk,
                attendees=attendees,
                pre_mapped=pre_mapped,
                vocab_hints=vocab_hints,
                phonetic_examples=phonetic_examples,
                llm_profile=llm_profile,
                timeout=timeout,
            )
            if sub.get("error"):
                logger.warning(f"  chunk {i} failed: {sub['error']}")
                continue
            for seg in sub.get("segments", []):
                all_segments.append(seg)
            for cid, name in (sub.get("cluster_mapping") or {}).items():
                # Prefer a concrete name over "Unknown" if any chunk identified
                # the speaker.
                existing = merged_mapping.get(cid)
                if not existing or existing == "Unknown":
                    merged_mapping[cid] = name
        return {"segments": all_segments, "cluster_mapping": merged_mapping}

    return _clean_one(
        raw_text,
        attendees=attendees,
        pre_mapped=pre_mapped,
        vocab_hints=vocab_hints,
        phonetic_examples=phonetic_examples,
        llm_profile=llm_profile,
        timeout=timeout,
    )


def _clean_one(
    raw_text: str,
    *,
    attendees: Optional[str] = None,
    pre_mapped: Optional[dict[str, str]] = None,
    vocab_hints: Optional[str] = None,
    phonetic_examples: Optional[list[dict]] = None,
    llm_profile: Optional[dict] = None,
    timeout: int = 600,
) -> dict:
    """Single-shot cleaner: one LLM call for a chunk that fits in context."""

    # Strip Whisper timestamps if any [00:15]
    raw_text = re.sub(r'\[\d{2}:\d{2}\]\s*', '', raw_text)

    # Format pre_mapped for the prompt
    if pre_mapped:
        pre_mapped_str = "\n".join(f"  - {cid} = {name}" for cid, name in pre_mapped.items())
    else:
        pre_mapped_str = "(không có pre-mapping — toàn bộ dựa vào context)"

    vocab_str = (vocab_hints or "").strip() or "(không có vocab hint — bỏ qua phần sửa mistranscription)"

    # Inject dynamic phonetic mappings (sinh từ vocab_hints, cached on recording).
    # If none, leave a placeholder so cleaner falls back to the generic pattern
    # examples already baked below in the prompt template.
    from meeting.services.phonetic_generator import format_for_prompt
    phonetic_block = format_for_prompt(phonetic_examples or [])
    if not phonetic_block:
        phonetic_block = "  (chưa có mapping — dùng pattern chung bên dưới)"

    prompt = CLEAN_PROMPT.format(
        attendees=attendees or "(không có thông tin attendees)",
        pre_mapped=pre_mapped_str,
        vocab_hints=vocab_str,
        phonetic_examples=phonetic_block,
        transcript=raw_text,
    )

    # Profile-driven creds: when caller passed a resolved profile, use it.
    # Otherwise fall back to legacy LLM_* env (single-model deploys).
    # max_retries=0 — the cleaner generates long outputs (~5-10K tokens) that
    # legitimately take 60-300s. Default SDK retry (2x) would triple the wall
    # clock when the first call is just slow rather than failed. Fail fast,
    # let the user see the error + retry manually.
    if llm_profile:
        client = OpenAI(
            api_key=llm_profile.get("api_key") or "",
            base_url=llm_profile.get("base_url") or "",
            max_retries=0,
        )
        model = llm_profile.get("model") or os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    else:
        client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", ""),
            max_retries=0,
        )
        model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

    try:
        # Qwen3-specific: disable reasoning mode (burns tokens + leaves content
        # empty for JSON tasks). Other models (Gemma, Llama…) don't have this
        # param — passing it to MaaS Gemma endpoint may 400. Send only when
        # model name actually contains "qwen".
        extra_kwargs: dict = {}
        if "qwen" in model.lower():
            extra_kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }
        max_toks = int((llm_profile or {}).get("max_tokens") or LLM_MAX_TOKENS)
        # Rough ETA — Gemma generates ~50-100 tokens/s, output ≈ input size
        # for the cleaner (it's a format pass, not summarization). Logged so
        # user knows whether to wait or pick a faster path.
        import time as _t
        _t0 = _t.time()
        est_output_tokens = len(raw_text) // 4
        eta_s = est_output_tokens // 60  # conservative 60 tok/s
        logger.info(
            f"[transcript_cleaner] LLM call → {model} · input={len(raw_text)} chars · "
            f"est_output≈{est_output_tokens} tokens · ETA ~{eta_s}s (timeout={timeout}s)"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_toks,
            timeout=timeout,
            **extra_kwargs,
        )
        output = (resp.choices[0].message.content or "").strip()
        # Strip Qwen3 <think>...</think> tags if model emits them as raw text
        output = re.sub(r"<think>.*?</think>", "", output, flags=re.DOTALL | re.IGNORECASE).strip()
        output = re.sub(r"<think>.*$", "", output, flags=re.DOTALL | re.IGNORECASE).strip()

        # Strip code fences if any
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        try:
            result = json.loads(output)
        except json.JSONDecodeError as parse_err:
            # LLM may have hit max_tokens mid-JSON. Try to salvage the segments
            # that finished cleanly: trim back to the last complete object in
            # the "segments" array, then close the brackets.
            logger.warning(
                f"clean_transcript: JSON truncated ({parse_err}); attempting salvage"
            )
            salvaged = _salvage_truncated_json(output)
            if salvaged is None:
                raise
            result = salvaged
            logger.info(
                f"  salvaged {len(result.get('segments', []))} segments from truncated output"
            )

        # Validate structure
        if "segments" not in result:
            return {"error": "LLM output missing 'segments' field", "raw": output[:500]}

        # Normalize tags field
        for seg in result["segments"]:
            if "tags" not in seg or not isinstance(seg["tags"], list):
                seg["tags"] = []
            if "speaker" not in seg:
                seg["speaker"] = "Unknown"
            if "text" not in seg:
                seg["text"] = ""

        # Ensure cluster_mapping exists (LLM may have omitted)
        if "cluster_mapping" not in result or not isinstance(result["cluster_mapping"], dict):
            result["cluster_mapping"] = {}

        # Safety check: cleaner is supposed to FORMAT, not SUMMARIZE. If the
        # output is <50% the input size, LLM ignored the "no summarization"
        # rule. Log a warning so user notices missing content. Threshold 0.5
        # is generous (cleaner legitimately removes ~20% as filler/duplicate).
        clean_chars = sum(len(s.get("text", "")) for s in result.get("segments", []))
        raw_chars = len(raw_text)
        ratio = clean_chars / raw_chars if raw_chars else 1.0
        if ratio < 0.5 and raw_chars > 2000:
            logger.warning(
                f"[transcript_cleaner] ⚠ Output suspiciously short: "
                f"clean={clean_chars} chars vs raw={raw_chars} chars (ratio={ratio:.0%}). "
                f"LLM may have summarized despite 'no summarization' instruction. "
                f"Consider switching to a more instruction-following model (GPT-OSS)."
            )
        elapsed = _t.time() - _t0
        logger.info(
            f"[transcript_cleaner] cleaned {len(result['segments'])} blocks · "
            f"{clean_chars}/{raw_chars} chars ({ratio:.0%}) · "
            f"took {elapsed:.1f}s · cluster_mapping={result['cluster_mapping']}"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"clean_transcript: JSON parse failed: {e}")
        return {"error": f"Invalid JSON from LLM: {e}", "raw": output[:500]}
    except Exception as e:
        logger.exception("clean_transcript failed")
        return {"error": str(e)}


def _salvage_truncated_json(text: str) -> Optional[dict]:
    """Best-effort recovery from LLM output cut mid-JSON by max_tokens.

    Strategy: walk forward tracking brace depth; find the position where the
    LAST complete top-level object inside `segments` ends. Trim there, close
    the array + outer object, and try parsing.

    Returns parsed dict on success, None if it couldn't be repaired.
    """
    # Locate the start of segments array.
    seg_start = text.find('"segments"')
    if seg_start == -1:
        return None
    arr_start = text.find("[", seg_start)
    if arr_start == -1:
        return None

    # Walk through the array, tracking brace depth + last complete object end.
    depth = 0
    in_string = False
    escape = False
    last_complete_end = -1  # index AFTER the last `}` that closed at depth=1
    for i in range(arr_start + 1, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                last_complete_end = i + 1  # include the `}`
    if last_complete_end == -1:
        return None

    repaired = text[:last_complete_end] + "]}"
    # Some LLM outputs put cluster_mapping BEFORE segments; not handled here —
    # the merger in clean_transcript() will fall back to an empty mapping for
    # this chunk, which is fine.
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
