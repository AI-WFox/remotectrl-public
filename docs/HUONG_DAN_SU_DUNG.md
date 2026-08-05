# Hướng dẫn kiểm thử RemoteCtrl cho thành viên nhóm

Tài liệu này dành cho tester nội bộ. Không chia sẻ tài khoản, enrollment token,
Agent credential hoặc dữ liệu máy thật ra ngoài nhóm.

## 1. Thành phần cần có

- URL Web Dashboard/Gateway.
- Tài khoản test do nhóm cấp. Tài liệu không lưu mật khẩu thật.
- `RemoteCtrlAgent-Setup.exe` từ GitHub Releases hoặc thư mục `release/`.
- `RemoteCtrlAgent-Setup.exe.sha256` để kiểm tra tính toàn vẹn.

Kiểm checksum:

```powershell
Get-FileHash .\RemoteCtrlAgent-Setup.exe -Algorithm SHA256
```

Giá trị phải trùng file `.sha256`.

## 2. Cài và enroll Agent

1. Cài Agent trên Windows 10/11 x64 và mở **RemoteCtrl Agent**.
2. Trong Web, đăng nhập rồi chọn **Create enrollment token**.
3. Trong Agent Settings, nhập public HTTPS Gateway URL, Agent name và token mới.
4. Chọn **Enroll**, sau đó **Connect**.
5. Xác nhận Web hiển thị đúng Agent name và trạng thái online.

Không dùng `127.0.0.1` hoặc `localhost` khi Web/Gateway chạy trên Render.
Enrollment token mặc định dùng một lần; nếu đã dùng, phải tạo token mới.

## 3. Quy tắc consent

Mọi thao tác đọc dữ liệu, điều khiển máy, bắt đầu hoặc dừng session đều yêu cầu
local approval trên máy Agent.

- **Deny**: từ chối command.
- **Allow once**: chỉ cho command hiện tại.
- **Allow for this session**: chỉ cache đúng command type và resource đã duyệt trong phiên kết nối hiện tại; quyền cho một app/path không áp dụng sang app/path khác.
- Đóng dialog bằng **X** được xử lý như Deny.
- Start và Stop là hai quyền riêng; cho phép Start không tự cho phép Stop.

Activity Capture phải luôn visible. Không nhập mật khẩu hoặc dữ liệu riêng tư trong
phiên test.

## 4. Checklist module

### Applications
- Refresh hiển thị visible windows thật.
- `Focus existing` đưa cửa sổ đang có lên trước.
- `New instance` tạo cửa sổ mới nếu ứng dụng hỗ trợ.
- Stop đúng PID và không làm mất các row khác.

### Processes
- Running apps và background processes tách riêng.
- PID, CPU, RAM và status đọc được.
- Protected process hiển thị Guarded và không kill được.

### Screen
- Capture Still tạo screenshot sau approval.
- Start Live cập nhật frame/FPS/latency liên tục.
- Stop Live cần approval, dừng stream và xóa frame cuối.
- Fullscreen hoạt động.

### Webcam
- Chạy Check Cameras trước.
- Snapshot và Live dùng camera thật, không phải screen.
- Permission denied/no camera phải trả lỗi rõ.
- Tester không cần cài OpenCV riêng; desktop dùng WebView2 camera API.

### Files
- Agent user phải thêm folder trong **Access & Privacy**.
- Web chỉ thấy allowed roots và descendants.
- Breadcrumb không đi lên folder cha ngoài allowed root.
- Download phải tạo file thật trong browser.
- Remove folder trên Agent phải xóa root tương ứng trên Web.

### Activity Capture
- Start/Stop đều cần approval.
- Active window, click, shortcut và text xuất hiện realtime trên Web.
- Backspace cập nhật cùng text segment.
- Local Stop phải chuyển Web về idle.
- Export tải file activity về browser.

### Power
- Refresh hiển thị CPU, uptime và battery nếu hệ điều hành cung cấp.
- Mặc định là dry-run.
- Real power chỉ chạy khi local user bật **Allow real power actions** và approve.
- Không bật real power trên máy đang dùng cho QA tự động.

## 5. Multi-Agent và audit

- Chọn Agent A rồi gửi command: Agent B không được nhận dialog hay action.
- Web không tự nhảy về Agent cũ sau realtime refresh.
- Command Timeline phải chuyển về `succeeded`, `failed` hoặc `denied`.
- Audit phải ghi command creation, approval mode, result và target Agent.

## 6. Xử lý lỗi

- **Gateway unavailable**: kiểm tra `<gateway-url>/api/health`; Web không tự chuyển
  sang dữ liệu demo.
- **Agent offline**: kiểm tra URL, Internet và chọn Connect/Reconnect.
- **Pending approval**: mở Agent và tìm approval window trên taskbar.
- **Token invalid/used**: tạo enrollment token mới.
- **Camera lỗi**: đóng ứng dụng khác đang giữ camera và kiểm tra Windows Camera Privacy.
- **Stream lag**: giảm FPS/quality và kiểm tra latency mạng.

## 7. Kết thúc phiên

1. Stop Screen, Webcam và Activity Capture bằng approval.
2. Đóng ứng dụng test.
3. Disconnect Agent.
4. Xóa file test/download không còn cần.
5. Gửi bug report gồm thời gian, Agent name, command type, ảnh lỗi và cách tái hiện.