"""
Meeting note generation using OpenAI-compatible LLM API.
Compatible with GreenNode AI Platform and other OpenAI-compatible endpoints.
"""
import json
import logging
import os
import re

from openai import OpenAI

MAX_TRANSCRIPT_CHARS = 40_000


NOTE_PROMPT = """Bạn là trợ lý tóm tắt cuộc họp chuyên nghiệp. Phân tích bản ghi cuộc họp dưới đây và tạo biên bản họp (Minutes of Meeting). TẤT CẢ nội dung phải viết bằng TIẾNG VIỆT.

## Thông tin cuộc họp (đã được xác nhận — dùng trực tiếp, không suy diễn lại)
- Tiêu đề: {title}
- Mục đích: {purpose}
- Ngày họp: {date}
- Người chủ trì: {chaired_by}
- Thư ký: {noted_by}
- Địa điểm: {venue}
- Thành viên tham gia: {attendees}

## Bản ghi cuộc họp
{transcript}

## Hướng dẫn xử lý theo độ dài transcript

**Nếu transcript ngắn (dưới ~300 từ):**
- Chỉ cần tóm tắt những gì đã nói vào `agenda_items`
- `action_items` để mảng rỗng `[]` nếu không rõ next step
- `summary` ngắn gọn 1-2 câu là đủ

**Nếu transcript đầy đủ:**
- Nhóm nội dung theo chủ đề thành các `agenda_items`
- Trích xuất `action_items` có PIC và deadline nếu được đề cập

## Lưu ý xử lý ngôn ngữ
- Transcript có thể chứa từ tiếng Anh xen lẫn tiếng Việt (code-switching) — giữ nguyên các từ kỹ thuật tiếng Anh, không dịch (ví dụ: "deploy", "sprint", "API", "backend" giữ nguyên).
- Nếu Whisper nghe sai một từ rõ ràng (ví dụ tên người, tên sản phẩm lạ), hãy dựa vào ngữ cảnh để hiểu đúng nghĩa, nhưng không tự bịa thêm thông tin.

## Yêu cầu đầu ra
Trả về CHỈ JSON hợp lệ (không markdown fences, không giải thích). QUAN TRỌNG: không dùng dòng mới thô trong giá trị string — nếu cần xuống dòng, dùng ký tự `\\n`. Cấu trúc:
{{
  "title": "{title}",
  "purpose": "{purpose}",
  "venue": "{venue}",
  "date": "{date}",
  "chaired_by": "{chaired_by}",
  "noted_by": "{noted_by}",
  "attendees": {attendees_json},
  "agenda_items": [
    {{
      "topic_no": 1,
      "agenda": "<tóm tắt chủ đề>",
      "description": "<chi tiết nội dung>"
    }}
  ],
  "action_items": [
    {{
      "pic": "<người chịu trách nhiệm>",
      "deadline": "<DD/MM/YYYY hoặc 'Chưa xác định'>",
      "item": "<nội dung công việc>"
    }}
  ],
  "summary": "<tóm tắt 1-3 câu>"
}}
"""


def generate_meeting_notes(
    transcript: str,
    title: str = "",
    purpose: str = "",
    date: str = "",
    chaired_by: str = "",
    noted_by: str = "",
    venue: str = "",
    attendees: str = "",
    timeout: int = 300,
) -> dict:
    """
    Send transcript to Claude Code CLI to generate structured meeting notes.

    Returns:
        Parsed MoM dict, or error dict on failure.
    """
    # Parse attendees string into JSON array for the prompt
    attendees_json = _parse_attendees(attendees)

    # Strip timestamps like [00:00] to reduce prompt size
    transcript = re.sub(r'\[\d{2}:\d{2}\]\s*', '', transcript)
    # Remove Whisper hallucination (repeated lines) before sending to LLM
    transcript = _dedup_transcript(transcript)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        logging.warning(
            f"Transcript too long ({len(transcript)} chars), truncating to {MAX_TRANSCRIPT_CHARS}"
        )
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n[... transcript truncated ...]"

    prompt = NOTE_PROMPT.format(
        title=title or "Cuộc họp",
        purpose=purpose or "(không có)",
        date=date or "(không có)",
        chaired_by=chaired_by or "(không có)",
        noted_by=noted_by or "Mee Agent",
        venue=venue or "(không có)",
        attendees=attendees or "(không có)",
        attendees_json=json.dumps(attendees_json, ensure_ascii=False),
        transcript=transcript,
    )

    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            timeout=timeout,
        )
        output = response.choices[0].message.content.strip()

        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        output = _fix_llm_json(output)
        try:
            notes = json.loads(output)
        except json.JSONDecodeError:
            output = _repair_truncated_json(output)
            notes = json.loads(output)
        return notes

    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse LLM output as JSON: {e}")
        logging.error(f"Raw output (first 500 chars): {output[:500]}")
        return {"error": f"Invalid JSON from LLM: {str(e)}", "raw_output": output[:2000]}
    except Exception as e:
        logging.error(f"LLM call failed: {e}")
        return {"error": f"LLM call failed: {str(e)}"}


def _fix_llm_json(text: str) -> str:
    """Fix common LLM JSON formatting mistakes."""
    # Escape raw control characters inside string values (most common: newlines)
    text = _escape_control_chars_in_strings(text)
    # Python None/True/False/NaN → JSON null/true/false/null
    text = re.sub(r'(?<!["\w])None(?!["\w])', 'null', text)
    text = re.sub(r'(?<!["\w])NaN(?!["\w])', 'null', text)
    text = re.sub(r'(?<!["\w])Infinity(?!["\w])', 'null', text)
    text = re.sub(r'(?<!["\w])True(?!["\w])', 'true', text)
    text = re.sub(r'(?<!["\w])False(?!["\w])', 'false', text)
    # Trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _repair_truncated_json(text: str) -> str:
    """Close any open strings, arrays, and objects in a truncated JSON response."""
    # Close any unterminated string first
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string

    if in_string:
        text += '"'

    # Count unmatched brackets and braces, then close them
    depth = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            depth.append('}' if ch == '{' else ']')
        elif ch in ('}', ']'):
            if depth and depth[-1] == ch:
                depth.pop()

    # Remove trailing comma before closing
    text = re.sub(r',\s*$', '', text.rstrip())
    # Close all unclosed structures
    text += ''.join(reversed(depth))
    return text


def _escape_control_chars_in_strings(text: str) -> str:
    """Walk the JSON text and escape raw newlines/tabs inside string values."""
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == '\\':
                # Already-escaped sequence — copy both chars unchanged
                result.append(ch)
                i += 1
                if i < len(text):
                    result.append(text[i])
            elif ch == '"':
                in_string = False
                result.append(ch)
            elif ch == '\n':
                result.append('\\n')
            elif ch == '\r':
                result.append('\\r')
            elif ch == '\t':
                result.append('\\t')
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)
        i += 1
    return ''.join(result)


def _dedup_transcript(transcript: str) -> str:
    """Remove consecutive repeated sentences caused by Whisper hallucination.

    Handles both newline-separated and period-separated repetitions.
    """
    # Split into sentences on '. ' boundaries while keeping the delimiter
    segments = re.split(r'(?<=\.)\s+', transcript)
    result = []
    prev = None
    repeat_count = 0
    for seg in segments:
        key = seg.strip().rstrip('.')
        if key and key == prev:
            repeat_count += 1
            if repeat_count < 2:
                result.append(seg)
        else:
            prev = key
            repeat_count = 0
            result.append(seg)
    deduped = ' '.join(result)

    # Also deduplicate at line level for line-separated repetitions
    lines = deduped.splitlines()
    result2 = []
    prev2 = None
    repeat_count2 = 0
    for line in lines:
        stripped = line.strip()
        if stripped == prev2:
            repeat_count2 += 1
            if repeat_count2 < 2:
                result2.append(line)
        else:
            prev2 = stripped
            repeat_count2 = 0
            result2.append(line)
    return "\n".join(result2)


def _parse_attendees(attendees_str: str) -> list:
    """
    Parse attendees string into list of dicts.
    Supports: "Nguyễn Văn A (Team AI - PM), Trần Thị B (Engineering - Lead)"
    """
    if not attendees_str or not attendees_str.strip():
        return []

    result = []
    for part in attendees_str.split(","):
        part = part.strip()
        if not part:
            continue
        # Try to extract name + (dept - title)
        if "(" in part and ")" in part:
            name = part[:part.index("(")].strip()
            info = part[part.index("(") + 1:part.index(")")].strip()
            if "-" in info:
                dept, title = info.split("-", 1)
                result.append({"name": name, "department": dept.strip(), "title": title.strip()})
            else:
                result.append({"name": name, "department": info, "title": ""})
        else:
            result.append({"name": part, "department": "", "title": ""})

    return result
