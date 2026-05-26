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
3. Bỏ filler words: "ờ", "um", "à", "dạ", "thì là", "kiểu", "you know"
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

MAX_TRANSCRIPT_CHARS = 30_000


CLEAN_PROMPT = """Bạn là editor chuyên format lại transcript cuộc họp tiếng Việt cho dễ đọc.

## Attendees (có thể có trong cuộc họp)
{attendees}

## Raw transcript (từ Whisper STT)
{transcript}

## Nhiệm vụ
Re-format raw transcript ở trên thành **dạng có cấu trúc** với speaker labels:

1. **Detect speaker** cho mỗi câu/đoạn:
   - Dựa vào attendees list ở trên
   - Xưng hô: "em" = junior/younger, "anh"/"chị" = senior/older
   - Gọi tên trực tiếp: "Tuấn ơi", "Mai à"
   - Nếu không detect được → speaker = "Unknown"
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
   - `question` — câu hỏi (vd "Mai làm xong chưa?")
   - `update` — báo cáo tình trạng (vd "v1 deploy thành công")

## Lưu ý
- Giữ nguyên các từ tiếng Anh kỹ thuật: deploy, API, sprint, backlog, v.v.
- Không tự bịa thông tin. Nếu raw không rõ, để text gần với raw nhất.
- Không tóm tắt hay rút gọn — chỉ format lại.

## Output format
Trả về CHỈ JSON hợp lệ (không markdown fences):
{{
  "segments": [
    {{
      "speaker": "<tên hoặc 'Unknown'>",
      "text": "<câu nói đã clean>",
      "tags": ["commitment" | "decision" | "blocker" | "question" | "update"]
    }}
  ]
}}
"""


def clean_transcript(
    raw_text: str,
    attendees: Optional[str] = None,
    timeout: int = 120,
) -> dict:
    """
    Run LLM post-process to clean + structure transcript.

    Args:
        raw_text: full raw transcript text (joined segments)
        attendees: comma-separated names of attendees, vd "Linh, Tuấn, Mai"
        timeout: LLM timeout in seconds

    Returns:
        {"segments": [{speaker, text, tags}, ...]}
        OR {"error": "..."}
    """
    if not raw_text or not raw_text.strip():
        return {"segments": []}

    # Truncate if too long
    if len(raw_text) > MAX_TRANSCRIPT_CHARS:
        logger.warning(
            f"Transcript too long ({len(raw_text)} chars), "
            f"truncating to {MAX_TRANSCRIPT_CHARS}"
        )
        raw_text = raw_text[:MAX_TRANSCRIPT_CHARS] + "\n[... truncated ...]"

    # Strip Whisper timestamps if any [00:15]
    raw_text = re.sub(r'\[\d{2}:\d{2}\]\s*', '', raw_text)

    prompt = CLEAN_PROMPT.format(
        attendees=attendees or "(không có thông tin attendees)",
        transcript=raw_text,
    )

    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
    )
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            timeout=timeout,
        )
        output = resp.choices[0].message.content.strip()

        # Strip code fences if any
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        result = json.loads(output)

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

        logger.info(
            f"[transcript_cleaner] cleaned {len(result['segments'])} blocks "
            f"from {len(raw_text)} chars raw"
        )
        return result

    except json.JSONDecodeError as e:
        logger.error(f"clean_transcript: JSON parse failed: {e}")
        return {"error": f"Invalid JSON from LLM: {e}", "raw": output[:500]}
    except Exception as e:
        logger.exception("clean_transcript failed")
        return {"error": str(e)}
