"""Seed data for the `roles` pool — the 10 company roles (3 depts).

Single source of truth shared by the Alembic seed migration (0016) and tests.
`data_plan` ∈ {own_tasks, cross_project, minimal}. Descriptions/kickoff_prompts
are the VI values from docs/superpowers/specs/2026-06-13-role-persona-kickoff-design.md.
"""
from __future__ import annotations

SEED_ROLES: list[dict] = [
    # ── Dept: Engineer ──────────────────────────────────────────────
    {
        "name": "AI Applied",
        "data_plan": "own_tasks",
        "description": (
            "Nghiên cứu và ứng dụng các mô hình trí tuệ nhân tạo vào sản phẩm "
            "thực tế, tối ưu hóa thuật toán để giải quyết các bài toán cụ thể "
            "của doanh nghiệp."
        ),
        "kickoff_prompt": (
            "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các task "
            "nghiên cứu/ứng dụng mô hình đang được giao, gợi ý ưu tiên theo hạn "
            "và mức độ quan trọng. Giọng đồng hành, khích lệ, gọn."
        ),
    },
    {
        "name": "AI Engineer",
        "data_plan": "own_tasks",
        "description": (
            "Xây dựng, thử nghiệm và triển khai các hệ thống/mô hình AI "
            "(Machine Learning, Deep Learning), chịu trách nhiệm về kiến trúc "
            "hạ tầng dữ liệu và mô hình."
        ),
        "kickoff_prompt": (
            "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các task xây "
            "dựng/triển khai mô hình & hạ tầng dữ liệu đang được giao, gợi ý nên "
            "làm việc nào trước theo hạn/độ quan trọng. Giọng kỹ thuật, đồng "
            "hành, gọn."
        ),
    },
    {
        "name": "Software Engineer",
        "data_plan": "own_tasks",
        "description": (
            "Lập trình, phát triển và bảo trì các ứng dụng phần mềm, hệ thống "
            "theo yêu cầu kỹ thuật của dự án."
        ),
        "kickoff_prompt": (
            "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các task "
            "phát triển/bảo trì đang được giao, gợi ý ưu tiên theo hạn và độ "
            "quan trọng. Giọng đồng hành, gọn."
        ),
    },
    {
        "name": "Associate System Manager",
        "data_plan": "own_tasks",
        "description": (
            "Hỗ trợ quản lý, vận hành và giám sát hệ thống hạ tầng CNTT, đảm bảo "
            "tính ổn định, bảo mật và hiệu năng của hệ thống."
        ),
        "kickoff_prompt": (
            "Tập trung vào CÔNG VIỆC CỦA RIÊNG người dùng: điểm qua các task vận "
            "hành/giám sát hệ thống đang được giao, lưu ý việc gấp hoặc ảnh hưởng "
            "ổn định hệ thống trước. Giọng cẩn trọng, ưu tiên việc khẩn."
        ),
    },
    {
        "name": "Lead System Engineer",
        "data_plan": "cross_project",
        "description": (
            "Trưởng nhóm kỹ sư hệ thống, chịu trách nhiệm thiết kế kiến trúc hạ "
            "tầng lớn, dẫn dắt đội ngũ kỹ thuật và giải quyết các sự cố hệ thống "
            "phức tạp."
        ),
        "kickoff_prompt": (
            "Cho người dùng cái nhìn TỔNG QUAN các project hệ thống họ phụ "
            "trách: task mới, sự cố đang mở, và mời rà soát phân công cho đội. "
            "Giọng tổng hợp, ưu tiên rủi ro và bức tranh toàn cảnh hơn chi tiết."
        ),
    },
    {
        "name": "Business Analyst",
        "data_plan": "cross_project",
        "description": (
            "Phân tích yêu cầu nghiệp vụ từ khách hàng hoặc các bên liên quan, "
            "chuyển hóa thành tài liệu kỹ thuật để đội ngũ phát triển phần mềm "
            "thực hiện."
        ),
        "kickoff_prompt": (
            "Cho người dùng cái nhìn TỔNG QUAN nhiều project họ liên quan: số "
            "task mới, project nào vừa có thay đổi, và mời họ rà soát. Giọng "
            "tổng hợp, súc tích, ưu tiên bức tranh toàn cảnh hơn chi tiết từng "
            "task."
        ),
    },
    {
        "name": "Lead QC Engineer",
        "data_plan": "cross_project",
        "description": (
            "Trưởng nhóm kiểm thử chất lượng phần mềm, lên kế hoạch kiểm thử "
            "(test plan), giám sát quy trình QC và đảm bảo chất lượng đầu ra của "
            "sản phẩm."
        ),
        "kickoff_prompt": (
            "Cho người dùng cái nhìn TỔNG QUAN chất lượng across project: task "
            "kiểm thử/bug đang mở, hạng mục chờ QC, và mời rà soát kế hoạch "
            "test. Giọng tổng hợp, ưu tiên rủi ro chất lượng và việc đang nghẽn."
        ),
    },
    # ── Dept: Product ───────────────────────────────────────────────
    {
        "name": "Lead Software Engineer",
        "data_plan": "cross_project",
        "description": (
            "Trưởng nhóm lập trình phần mềm, chịu trách nhiệm chính về kiến trúc "
            "mã nguồn, định hướng kỹ thuật cho dự án và quản lý năng suất của "
            "các kỹ sư phần mềm."
        ),
        "kickoff_prompt": (
            "Cho người dùng cái nhìn TỔNG QUAN các project họ dẫn dắt: task mới, "
            "việc của đội đang nghẽn, mời rà soát phân công/kiến trúc. Giọng "
            "tổng hợp, ưu tiên điểm nghẽn của đội."
        ),
    },
    {
        "name": "Associate Product Growth Executive",
        "data_plan": "cross_project",
        "description": (
            "Chuyên viên hỗ trợ tăng trưởng sản phẩm, tham gia vào việc phân "
            "tích dữ liệu người dùng, tối ưu hóa trải nghiệm và thực hiện các "
            "chiến dịch thúc đẩy người dùng sử dụng sản phẩm."
        ),
        "kickoff_prompt": (
            "Cho người dùng cái nhìn TỔNG QUAN các hạng mục sản phẩm/tăng "
            "trưởng họ liên quan: task mới, chiến dịch/thử nghiệm đang chạy, mời "
            "rà soát ưu tiên. Giọng tổng hợp, hướng dữ liệu, súc tích."
        ),
    },
    # ── Dept: GreenNode HR & Admin ──────────────────────────────────
    {
        "name": "L&D Executive",
        "data_plan": "minimal",
        "description": (
            "Chuyên viên Đào tạo và Phát triển (Learning & Development), chịu "
            "trách nhiệm xây dựng lộ trình học tập, tổ chức các khóa đào tạo "
            "nâng cao kỹ năng và phát triển năng lực cho nhân sự."
        ),
        "kickoff_prompt": (
            "Chào ngắn gọn theo vai trò L&D, giới thiệu Mee là trợ lý cuộc họp "
            "và mời người dùng hỏi hoặc giao việc (không bịa số liệu task)."
        ),
    },
]
