# Requirements Freeze — Design Decision

**Ngày:** 2026-08-31  
**Trạng thái:** Approved for documentation  
**Tài liệu đầu ra:** `docs/requirements.md`

## Mục đích

Chuyển phần Day 1 và Scope trong kiến trúc V4 thành một baseline yêu cầu có thể truy vết, kiểm thử và dùng trực tiếp để thiết kế ERD, tool contracts và API contracts ở bước kế tiếp.

## Các hướng đã cân nhắc

1. **Sao chép nguyên scope từ V4:** nhanh nhưng thiếu requirement ID, hành vi lỗi và acceptance criteria.
2. **Viết SRS học thuật đầy đủ ngay:** bao quát nhưng tạo nhiều tài liệu và quyết định chưa cần thiết trước khi kiểm chứng feasibility.
3. **MVP spec thực dụng:** giữ quyết định V4, bổ sung actor, priority, source of truth, use case, requirement ID và acceptance criteria.

Chọn hướng 3 vì đủ chặt để triển khai và đánh giá nhưng không thiết kế sớm chi tiết của các bước sau.

## Thiết kế tài liệu

`docs/requirements.md` là nguồn chuẩn duy nhất cho scope MVP v1 và gồm:

- problem/product statement;
- mục tiêu, ràng buộc và phạm vi loại trừ;
- phân lớp P0/P1;
- actor và hệ thống bên ngoài;
- ma trận nguồn sự thật;
- 10 use case cốt lõi;
- functional/non-functional requirements có ID;
- acceptance criteria cho bước 1 và tiêu chí thành công của MVP;
- quy tắc quản lý thay đổi.

## Nguyên tắc biên

- Requirements mô tả hệ thống phải làm gì; chưa chốt bảng, endpoint hoặc JSON schema cụ thể.
- LLM là lớp điều phối/ngôn ngữ, không phải nguồn sự thật giao dịch.
- P0 là luồng demo chính; P1 vẫn thuộc MVP và phải hoàn thành trước nghiệm thu cuối.
- Các tiêu chí an toàn quan trọng dùng mục tiêu bằng 0 trên bộ test nghiệm thu: misuse công cụ nghiêm trọng, rò rỉ chéo phiên và xác nhận thanh toán sai.
- Chưa đặt latency làm release gate khi hạ tầng/model chưa được benchmark; hệ thống bắt buộc phải đo p50/p95 để chốt threshold sau feasibility.

## Đầu ra cho bước kế tiếp

Baseline này cung cấp input để chốt:

1. Shared enums và state machine.
2. Database ERD.
3. Tool JSON schemas.
4. OpenAPI contracts.
5. Traceability từ requirement sang test và evaluation metric.
