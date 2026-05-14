"""
Generate Minutes of Meeting (MoM) report as Markdown file.
Follows the official MoM template.
"""
import logging
import os
from datetime import date as date_cls


def generate_mom_markdown(
    notes: dict,
    output_dir: str = "output",
) -> str:
    """
    Generate a Markdown MoM file from structured notes dict.

    Returns:
        Path to the generated .md file.
    """
    os.makedirs(output_dir, exist_ok=True)

    meeting_date = notes.get("date", date_cls.today().strftime("%d/%m/%Y"))
    safe_title = (notes.get("title") or "meeting").replace(" ", "_").replace("/", "-")[:40]
    safe_date = meeting_date.replace("/", "-").replace(" ", "_")
    filename = f"MoM_{safe_title}_{safe_date}.md"
    filepath = os.path.join(output_dir, filename)

    lines = []

    # Title
    lines.append(f"# MINUTES OF MEETING (MoM)")
    lines.append("")

    # Header table
    lines.append("| | |")
    lines.append("| --- | --- |")
    lines.append(f"| **Mục đích cuộc họp / Purpose of Meeting** | {notes.get('purpose', '')} |")
    lines.append(f"| **Địa điểm họp / Venue** | {notes.get('venue', '')} |")
    lines.append(f"| **Ngày họp / Date** | {meeting_date} |")
    lines.append(f"| **Người chủ trì / Chaired by** | {notes.get('chaired_by', '')} |")
    lines.append(f"| **Thư ký / Noted by** | {notes.get('noted_by', '')} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Attendees
    lines.append("## THÀNH PHẦN THAM GIA / Present")
    lines.append("")
    lines.append("| No. | Họ và tên / Full name | Đơn vị / Com./Dept. | Chức vụ / Job title |")
    lines.append("| --- | --------------------- | ------------------- | ------------------- |")

    attendees = notes.get("attendees", [])
    if attendees:
        for i, att in enumerate(attendees, 1):
            name = att.get("name", "")
            dept = att.get("department", "")
            title = att.get("title", "")
            lines.append(f"| {i} | {name} | {dept} | {title} |")
    else:
        lines.append("| 1 | | | |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Meeting content
    lines.append("## NỘI DUNG CUỘC HỌP / Meeting Content")
    lines.append("")
    lines.append("| Topic No. | TÓM TẮT NỘI DUNG / Agenda | CHI TIẾT / Description and Action Plan |")
    lines.append("| --------- | ------------------------- | -------------------------------------- |")

    agenda_items = notes.get("agenda_items", [])
    if agenda_items:
        for item in agenda_items:
            topic_no = item.get("topic_no", "")
            agenda = item.get("agenda", "").replace("|", "\\|").replace("\n", "<br>")
            description = item.get("description", "").replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"| {topic_no} | {agenda} | {description} |")
    else:
        lines.append("| 1 | | |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Next steps / Action items
    lines.append("## Next step: Các công việc tiếp theo")
    lines.append("")
    lines.append("| CHỊU TRÁCH NHIỆM / PIC | NGÀY / DATE | NỘI DUNG / ITEM |")
    lines.append("| ---------------------- | ----------- | --------------- |")

    action_items = notes.get("action_items", [])
    if action_items:
        for action in action_items:
            pic = action.get("pic", "").replace("|", "\\|")
            deadline = action.get("deadline", "").replace("|", "\\|")
            item = action.get("item", "").replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"| {pic} | {deadline} | {item} |")
    else:
        lines.append("| | | |")

    lines.append("")

    # Summary
    summary = notes.get("summary", "")
    if summary:
        lines.append("---")
        lines.append("")
        lines.append("## Tóm tắt / Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    content = "\n".join(lines)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logging.info(f"MoM Markdown saved: {filepath}")
    return filepath
