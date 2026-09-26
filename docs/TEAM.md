# Danh Sách Thành Viên & Báo Cáo Phân Công Nhóm

- **Tên Nhóm:** `NGUYEN VAN SON (solo)`
- **Mã Nhóm / Lớp:** `K4-L3-DAY10`
- **Tên Repository Nộp Bài:** `K4-L3A-Day10-Data-Pipeline-Data-Observability`
- **Số thành viên:** 1 (làm cá nhân, kiêm toàn bộ 4 vai trò dưới đây)

---

## # Thành viên

| STT | Họ và tên | MSSV | Email | Vai trò & Phân công công việc | Báo cáo cá nhân |
|---:|---|---|---|---|---|
| 1 | NGUYỄN VĂN SƠN | 2A202602744 | dzson231@gmail.com | Solo: Pipeline Integrator + Data Foundation & Recovery + RAG & Vector Index + Observability & Evaluation (toàn bộ `src/`, pipeline, báo cáo) | (tích hợp trong repo) |

---

## # Cá nhân

### NGUYEN VAN SON-2A202602744
- **Vai trò:** Solo — kiêm toàn bộ 4 vai trò (Pipeline Integrator, Data Foundation, RAG & Vector Index, Observability & Evaluation).
- **Công việc chi tiết đã hoàn thành:**
  - Pipeline: kết nối end-to-end `src/pipelines/phase1.py` và `src/pipelines/corruption_flow.py`; kiểm tra artifacts và contributor tracking nhánh `main`.
  - Ingestion & Cleaning: thu thập Crossref API + fallback offline (`crossref.py`); chuẩn hóa schema, `age_days`, `text_for_embedding` (`cleaning.py`); Idempotent Repair từ raw snapshot.
  - RAG & Vector: embedding `all-MiniLM-L6-v2`; 3 collection ChromaDB riêng biệt (`papers-baseline`, `papers-corrupted`, `papers-repaired`); QA Agent.
  - Observability & Eval: Quality Gate Great Expectations 1.x + Freshness SLA (`quality.py`); test set 10 câu (`testset.py`); báo cáo đối chiếu 3 trạng thái (`corruption_report.md`).
- **Kết quả:** Baseline Hit Rate 1.0000 / Token F1 1.0000; Corrupted 0.8000 / 0.6568 (GX gate trip + freshness alert); Repaired 1.0000 / 1.0000 khớp baseline.
- **Điều học được / Đóng góp chính:**
  - Thiết kế Idempotent Pipeline, Data Lineage với raw snapshot, và chặn Silent Failure bằng Quality Gate trước serving layer.
