# Nexus Agent PDF

Thư mục này chứa bản phát hành PDF và nguồn HTML có thể tái tạo của tài liệu
tổng quan toàn bộ hệ thống Nexus Agent.

| File | Vai trò |
| --- | --- |
| `nexus-agent-toan-canh-he-thong-vi.pdf` | Bản đọc/chia sẻ chính bằng tiếng Việt |
| `nexus-agent-toan-canh-he-thong-vi.html` | Nguồn đã dàn trang để kiểm tra và tạo lại PDF |

Tạo lại PDF bằng Google Chrome/Chromium từ thư mục gốc repository:

```bash
google-chrome --headless --disable-gpu --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf=docs/pdf/nexus-agent-toan-canh-he-thong-vi.pdf \
  file://$(pwd)/docs/pdf/nexus-agent-toan-canh-he-thong-vi.html
```

Tài liệu phản ánh source tại commit được ghi trên trang bìa. Khi kiến trúc, API,
workflow hoặc ranh giới runtime thay đổi, hãy cập nhật nguồn HTML và tạo lại PDF.
