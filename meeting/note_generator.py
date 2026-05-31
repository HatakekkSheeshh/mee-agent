"""
Meeting note generation using OpenAI-compatible LLM API.
Compatible with GreenNode AI Platform and other OpenAI-compatible endpoints.
"""
import json
import logging
import os
import re

from openai import OpenAI

# Chunking thresholds (tuned for 8K-context LLMs like Qwen3-8B).
# Budget: ~2.5k tokens prompt + 1.5k output + 500 safety = leave ~3.5k tokens
# for transcript (≈ 10k chars Vietnamese @ 3 chars/token).
SINGLE_CALL_THRESHOLD = 10_000   # below this, use 1 LLM call (no map-reduce)
CHUNK_SIZE = 9_000               # target chars per map-step chunk
CHUNK_OVERLAP = 400              # overlap between consecutive chunks
MAX_TRANSCRIPT_CHARS = 60_000    # absolute cap — ~6 chunks fit in reduce step

# Output token budget per LLM call (replaces old 8192 which doesn't fit 8K ctx).
LLM_MAX_TOKENS = 1_500


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

## Cách phân loại nội dung (QUAN TRỌNG)

Phân tách thông tin thành 4 nhóm riêng biệt:

**1. action_items** — việc cần LÀM trong tương lai
- Có PIC (người chịu trách nhiệm) + deadline (nếu có)
- Ví dụ: "Tuấn deploy v1 trước thứ 5", "Linh review PR #142 cuối tuần"

**2. decisions** — quyết định ĐÃ CHỐT trong meeting
- Không phải "sẽ làm" mà là "đã quyết định"
- Ví dụ: "Team chốt dùng Postgres thay vì MySQL", "Approved budget $5K"

**3. commitments** — cam kết của member (ai hứa làm gì)
- Khác action_items: không nhất thiết có deadline cụ thể
- Ví dụ: "Mai cam kết sẽ research solution X tuần này", "Em sẽ phụ trách backend"

**4. blockers** — vấn đề đang BLOCK team
- Việc không thể tiến hành vì lý do gì
- Ví dụ: "Database migration đang block deploy", "Đợi review từ legal team"

## Few-shot examples

**Input sample (transcript):**
> "Tuấn nói: deploy v1 đang bị block bởi database migration. Linh chốt: tuần này
> Tuấn focus xử lý migration, deploy lùi sang tuần sau. Mai sẽ research solution
> caching mới, em sẽ làm POC cuối tuần."

**Output mong đợi:**
```
"action_items": [
  {{"pic": "Tuấn", "deadline": "Chưa xác định", "item": "Xử lý database migration"}},
  {{"pic": "Mai", "deadline": "Chưa xác định", "item": "POC solution caching"}}
],
"decisions": [
  {{"text": "Deploy v1 lùi sang tuần sau", "by": "Linh"}}
],
"commitments": [
  {{"text": "Research solution caching mới", "by": "Mai"}}
],
"blockers": [
  {{"text": "Database migration đang block deploy v1", "by": "Tuấn"}}
]
```

## Lưu ý xử lý ngôn ngữ
- Transcript có thể chứa từ tiếng Anh xen lẫn tiếng Việt (code-switching) — giữ nguyên các từ kỹ thuật tiếng Anh, không dịch (ví dụ: "deploy", "sprint", "API", "backend" giữ nguyên).
- Nếu Whisper nghe sai một từ rõ ràng (ví dụ tên người, tên sản phẩm lạ), hãy dựa vào ngữ cảnh để hiểu đúng nghĩa, nhưng không tự bịa thêm thông tin.
- Phân biệt câu HỎI vs câu TRẢ LỜI để gán đúng speaker khi extract events.
- Xưng hô tiếng Việt ("em", "anh", "chị") → infer speaker từ context attendees nếu có thể.

## Hướng dẫn xử lý theo độ dài transcript

**Nếu transcript ngắn (dưới ~300 từ):**
- `agenda_items`: tóm tắt những gì đã nói
- `action_items`, `decisions`, `commitments`, `blockers`: có thể `[]` nếu không rõ
- `summary` 1-2 câu

**Nếu transcript đầy đủ:**
- Nhóm `agenda_items` theo chủ đề
- Extract đầy đủ 4 nhóm trên với chi tiết PIC

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
  "decisions": [
    {{
      "text": "<nội dung quyết định đã chốt>",
      "by": "<người ra quyết định, nếu rõ>"
    }}
  ],
  "commitments": [
    {{
      "text": "<nội dung cam kết>",
      "by": "<người cam kết>"
    }}
  ],
  "blockers": [
    {{
      "text": "<nội dung blocker>",
      "by": "<người raise blocker, nếu rõ>"
    }}
  ],
  "summary": "<tóm tắt 1-3 câu, nhắc đến decisions chính>"
}}
"""


MAP_PROMPT = """Bạn là trợ lý phân tích cuộc họp. Đây là PHẦN {idx}/{total} của bản ghi (transcript) cuộc họp dài. Sẽ có bước merge các phần lại sau — nhiệm vụ của bạn LÀ extract sự kiện rời rạc trong phần này, KHÔNG cần tóm tắt toàn cuộc họp.

## Thông tin cuộc họp (context)
- Tiêu đề: {title}
- Người tham gia: {attendees}

## Bản ghi (phần {idx}/{total})
{chunk}

## Cách phân loại (4 nhóm chính)

**action_items** — việc cần LÀM trong tương lai, có PIC + deadline (nếu có).
**decisions** — quyết định ĐÃ CHỐT trong meeting (không phải "sẽ làm").
**commitments** — cam kết của member (ai hứa làm gì, không nhất thiết có deadline).
**blockers** — vấn đề đang BLOCK team.

Lưu ý: vì đây là 1 phần của transcript, có thể nội dung dở dang — chỉ extract những gì RÕ trong phần này, không suy diễn.

## Yêu cầu output
Trả về CHỈ JSON hợp lệ (không markdown fences, không giải thích). Dùng `\\n` cho xuống dòng trong string. Schema:
{{
  "agenda_items": [
    {{"agenda": "<chủ đề>", "description": "<chi tiết nội dung>"}}
  ],
  "action_items": [
    {{"pic": "<người>", "deadline": "<DD/MM/YYYY hoặc 'Chưa xác định'>", "item": "<nội dung>"}}
  ],
  "decisions": [
    {{"text": "<nội dung quyết định>", "by": "<người ra quyết định>"}}
  ],
  "commitments": [
    {{"text": "<nội dung cam kết>", "by": "<người cam kết>"}}
  ],
  "blockers": [
    {{"text": "<nội dung blocker>", "by": "<người raise, nếu rõ>"}}
  ],
  "key_points": ["<điểm chính 1 trong phần này>", "<điểm chính 2>"]
}}
"""


REDUCE_PROMPT = """Bạn là trợ lý tổng hợp meeting. Bên dưới là {n_partials} partial MoM extracted từ {n_partials} chunks của CÙNG 1 cuộc họp (các chunks có overlap nên có thể trùng lặp). Nhiệm vụ: GỘP lại thành 1 biên bản hoàn chỉnh, DEDUPE các entry trùng lặp, gom agenda_items theo chủ đề.

## Thông tin cuộc họp
- Tiêu đề: {title}
- Mục đích: {purpose}
- Ngày họp: {date}
- Người chủ trì: {chaired_by}
- Thư ký: {noted_by}
- Địa điểm: {venue}
- Thành viên tham gia: {attendees}

## Partial MoMs ({n_partials} chunks)
{partials_json}

## Quy tắc merge

1. **Dedupe action_items**: nếu 2 entry có cùng PIC + nội dung tương tự (vd "deploy v1" vs "deploy version 1") → giữ 1 entry, chọn deadline cụ thể nhất.
2. **Dedupe decisions/commitments/blockers**: gộp các entry trùng nghĩa (so sánh theo nội dung + người).
3. **Gom agenda_items theo chủ đề**: nếu nhiều chunks đều bàn cùng 1 topic (vd "Database migration") → merge thành 1 agenda với description gộp các chi tiết.
4. **Đánh `topic_no` 1,2,3...** theo thứ tự agenda xuất hiện trong meeting.
5. **Summary 2-4 câu** cho cả cuộc họp dựa trên key_points + decisions chính (không lặp lại agenda).

## Yêu cầu output
Trả về CHỈ JSON hợp lệ (không markdown fences). Dùng `\\n` cho xuống dòng. Schema:
{{
  "title": "{title}",
  "purpose": "{purpose}",
  "venue": "{venue}",
  "date": "{date}",
  "chaired_by": "{chaired_by}",
  "noted_by": "{noted_by}",
  "attendees": {attendees_json},
  "agenda_items": [
    {{"topic_no": 1, "agenda": "<chủ đề>", "description": "<chi tiết gộp từ các chunks>"}}
  ],
  "action_items": [
    {{"pic": "...", "deadline": "...", "item": "..."}}
  ],
  "decisions": [
    {{"text": "...", "by": "..."}}
  ],
  "commitments": [
    {{"text": "...", "by": "..."}}
  ],
  "blockers": [
    {{"text": "...", "by": "..."}}
  ],
  "summary": "<tóm tắt 2-4 câu>"
}}
"""


def _build_llm_client():
    """Shared OpenAI-compatible client + model name."""
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    return client, model

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", flags=re.DOTALL | re.IGNORECASE)


def _call_llm_for_json(client, model, prompt: str, timeout: int, max_tokens: int = LLM_MAX_TOKENS) -> dict:
    """Single LLM call expecting JSON output. Returns parsed dict or {'error': ...}."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            timeout=timeout,
            # Disable Qwen3 chain-of-thought (vLLM honors this; OpenAI ignores).
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        output = (response.choices[0].message.content or "").strip()
        # Self-hosted Qwen3 sometimes still emits <think>...</think> as raw text
        # in content. Strip it (and unclosed openers from truncation).
        output = _THINK_TAG_RE.sub("", output)
        output = _THINK_OPEN_RE.sub("", output).strip()
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()
        output = _fix_llm_json(output)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            output = _repair_truncated_json(output)
            return json.loads(output)
    except json.JSONDecodeError as e:
        logging.error(f"JSON parse failed: {e}; raw[:300]={output[:300] if 'output' in dir() else 'N/A'}")
        return {"error": f"Invalid JSON: {e}"}
    except Exception as e:
        logging.error(f"LLM call failed: {e}")
        return {"error": f"LLM call failed: {e}"}


def _atomic_units(text: str, max_unit: int) -> list[str]:
    """Break text into units no larger than `max_unit` chars.

    Hierarchy: lines → sentences → hard char-split. Used by chunker so a single
    very-long line (e.g. post-dedup joined paragraph) doesn't blow past chunk_size.
    """
    units: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_unit:
            units.append(line)
            continue
        # Long line — split on sentence boundary
        for sent in re.split(r'(?<=[.!?])\s+', line):
            if len(sent) <= max_unit:
                units.append(sent)
                continue
            # Sentence still too long — hard char-split
            for i in range(0, len(sent), max_unit):
                units.append(sent[i:i + max_unit])
    return units


def _split_transcript_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split transcript into chunks ≤ chunk_size chars with `overlap` chars carry-over.

    Falls back from line → sentence → char boundaries so chunking is robust against
    transcripts that have already been joined into one giant line (e.g. after dedup).
    """
    if len(text) <= chunk_size:
        return [text]

    units = _atomic_units(text, max_unit=chunk_size)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def _flush():
        nonlocal current, current_len
        if not current:
            return
        chunk_text = "\n".join(current)
        chunks.append(chunk_text)
        if overlap > 0 and len(chunk_text) > overlap:
            tail = chunk_text[-overlap:]
            space_idx = tail.find(" ")
            if 0 < space_idx < overlap // 2:
                tail = tail[space_idx + 1:]
            current = [tail]
            current_len = len(tail)
        else:
            current = []
            current_len = 0

    for unit in units:
        unit_len = len(unit) + 1  # +1 for the \n joiner
        if current_len + unit_len > chunk_size and current:
            _flush()
        current.append(unit)
        current_len += unit_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def _generate_via_map_reduce(
    client, model,
    transcript: str,
    title: str, purpose: str, date: str,
    chaired_by: str, noted_by: str, venue: str,
    attendees: str, attendees_json: list,
    timeout: int,
) -> dict:
    """Long transcript → split chunks → extract partial → merge."""
    chunks = _split_transcript_chunks(transcript)
    n = len(chunks)
    logging.info(f"[map-reduce] {len(transcript)} chars → {n} chunks (size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP})")

    partials: list[dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        logging.info(f"[map {idx}/{n}] {len(chunk)} chars → LLM")
        prompt = MAP_PROMPT.format(
            idx=idx, total=n,
            title=title or "Cuộc họp",
            attendees=attendees or "(không có)",
            chunk=chunk,
        )
        partial = _call_llm_for_json(client, model, prompt, timeout=timeout, max_tokens=LLM_MAX_TOKENS)
        if "error" in partial:
            logging.warning(f"[map {idx}/{n}] skip chunk (error: {partial['error']})")
            continue
        partials.append(partial)

    if not partials:
        return {"error": "All chunks failed to parse during map step"}

    logging.info(f"[reduce] merging {len(partials)} partial MoMs → final")
    partials_json = json.dumps(partials, ensure_ascii=False, indent=2)
    reduce_prompt = REDUCE_PROMPT.format(
        n_partials=len(partials),
        title=title or "Cuộc họp",
        purpose=purpose or "(không có)",
        date=date or "(không có)",
        chaired_by=chaired_by or "(không có)",
        noted_by=noted_by or "Mee Agent",
        venue=venue or "(không có)",
        attendees=attendees or "(không có)",
        attendees_json=json.dumps(attendees_json, ensure_ascii=False),
        partials_json=partials_json,
    )
    final = _call_llm_for_json(client, model, reduce_prompt, timeout=timeout, max_tokens=LLM_MAX_TOKENS)
    if "error" in final:
        return {"error": f"Reduce step failed: {final['error']}", "partials_count": len(partials)}
    return final


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
    """Generate structured MoM JSON from transcript.

    Routes to single-call path (≤ SINGLE_CALL_THRESHOLD chars) or map-reduce chunking
    (long transcripts). Hard-truncates above MAX_TRANSCRIPT_CHARS as safety net.
    """
    attendees_json = _parse_attendees(attendees)

    # Pre-processing — same for both paths
    transcript = re.sub(r'\[\d{2}:\d{2}\]\s*', '', transcript)
    transcript = _dedup_transcript(transcript)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        logging.warning(
            f"Transcript exceeds hard cap ({len(transcript)} > {MAX_TRANSCRIPT_CHARS} chars), truncating tail."
        )
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n[... transcript truncated ...]"

    client, model = _build_llm_client()

    # Long transcript → map-reduce
    if len(transcript) > SINGLE_CALL_THRESHOLD:
        return _generate_via_map_reduce(
            client, model, transcript,
            title=title, purpose=purpose, date=date,
            chaired_by=chaired_by, noted_by=noted_by, venue=venue,
            attendees=attendees, attendees_json=attendees_json,
            timeout=timeout,
        )

    # Short transcript → single call (original path)
    logging.info(f"[single-call] {len(transcript)} chars → 1 LLM call")
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
    return _call_llm_for_json(client, model, prompt, timeout=timeout, max_tokens=LLM_MAX_TOKENS)


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
