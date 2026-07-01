# RemoteCtrl - Hướng dẫn kiểm thử Web và Agent

Tài liệu này dành cho tester nội bộ của team RemoteCtrl. Tester chỉ cần chạy Web Dashboard và Windows Agent để kiểm tra chức năng, không cần cài môi trường lập trình, build source hoặc deploy server.

## 1. Thành phần cần kiểm thử

RemoteCtrl gồm:

- **Web Dashboard**: nơi đăng nhập, chọn Agent và gửi lệnh điều khiển.
- **RemoteCtrl Agent**: ứng dụng Windows chạy trên máy được điều khiển.

Một người có thể mở cả Web và Agent trên hai máy khác nhau. Để kiểm tra đúng luồng thực tế, nên dùng:

- Máy A: mở Web Dashboard.
- Máy B: chạy `RemoteCtrlAgent.exe`.
- Hai máy có thể sử dụng hai mạng Internet khác nhau.

## 2. Thông tin tester sẽ nhận từ team

Trước khi test, tester cần có:

1. URL Web Dashboard, ví dụ `https://remotectrl-public-demo.onrender.com`.
2. Email và mật khẩu đăng nhập test.
3. File `RemoteCtrlAgent.exe`.
4. Mã SHA-256 của file Agent để đối chiếu.
5. Phạm vi chức năng cần test trong phiên.

Enrollment token sẽ được tạo trên Web Dashboard trong quá trình test.

Không chia sẻ URL nội bộ, tài khoản test, mật khẩu hoặc enrollment token ra ngoài team.

## 3. Chuẩn bị máy Agent

1. Lưu `RemoteCtrlAgent.exe` vào một thư mục cố định.
2. Quét file bằng Windows Security.
3. Đối chiếu SHA-256 nếu team có cung cấp:

```powershell
Get-FileHash .\RemoteCtrlAgent.exe -Algorithm SHA256
```

4. Nhấp đúp để mở Agent.

Agent là ứng dụng portable, không cần cài đặt.

Windows SmartScreen có thể cảnh báo vì bản test chưa được ký số. Chỉ chọn `More info` rồi `Run anyway` khi file đến đúng kênh của team và checksum khớp.

## 4. Đăng nhập Web Dashboard

1. Mở URL Web Dashboard bằng Chrome, Edge hoặc Brave.
2. Nhập tài khoản test. (Tài khoản: admin@remotectrl.local || Mật khẩu: RemoteCtrl@2026Demo!)
3. Nhấn **Sign in**.

Nếu web tải chậm ở lần mở đầu, chờ khoảng một phút rồi refresh. Render Free có thể sleep sau thời gian không hoạt động.

Không sử dụng `Demo mode` khi cần kiểm tra Agent thật. Demo mode chỉ hiển thị dữ liệu giả lập.

## 5. Kết nối Agent

### Trên Web Dashboard

1. Nhấn **Create enrollment token**.
2. Sao chép chuỗi bắt đầu bằng `enroll_`.

### Trên RemoteCtrl Agent

1. Nhập **Gateway URL** bằng đúng URL Web Dashboard public.
2. Đặt **Agent name** theo mẫu dễ nhận biết, ví dụ `TesterA-Laptop`.
3. Dán **Enrollment token**.
4. Nhấn **Enroll**.
5. Khi thấy thông báo enroll thành công, nhấn **Connect** nếu Agent chưa tự kết nối.

Không nhập `127.0.0.1` hoặc `localhost` khi test qua Internet.

### Kết quả mong đợi

- Agent hiển thị trạng thái `Connected`.
- Web Dashboard xuất hiện đúng Agent name.
- Chấm trạng thái cạnh Agent chuyển sang màu xanh.
- Thông tin hostname và IP không bị trống bất thường.

Nếu xuất hiện nhiều Agent trùng tên, đổi Agent name rồi báo lại trong kết quả test.

## 6. Local approval

Mọi action điều khiển hoặc đọc dữ liệu từ máy Agent đều cần local approval mặc định, gồm Applications, Processes, Files, Screen, Webcam, Key Capture và Power. Các lệnh dừng truy cập như **Stop Live** hoặc **Stop Session** không cần approval vì chúng làm giảm quyền truy cập.

- Chọn **Deny** để kiểm tra luồng từ chối.
- Chọn **Allow once** để chỉ cho phép command hiện tại.
- Chọn **Allow this action for this session** để cho phép cùng nhóm action trong phiên Agent hiện tại.

Tester cần kiểm tra ba trường hợp:

1. Deny: command phải chuyển sang `denied`, không thực thi action.
2. Allow once: command chạy, nhưng lần sau cùng action vẫn hỏi lại.
3. Allow for session: lần sau cùng nhóm action không hiện dialog, nhưng Audit phải ghi `session_cached`.

Không chấp thuận action ngoài kịch bản test đã thống nhất.

## 7. Test Applications

### Danh sách ứng dụng

1. Chọn **Applications**.
2. Nhấn **Refresh Applications**.

Kết quả mong đợi:

- Hiển thị các cửa sổ đang mở thật trên máy Agent.
- Mỗi hàng có window title, process name và PID.
- Không hiển thị raw JSON làm nội dung chính.

### Mở ứng dụng

1. Nhấn **Notepad**.
2. Kiểm tra Notepad mở trên máy Agent.
3. Lặp lại với Calculator, Paint hoặc Explorer nếu cần.

### Đóng ứng dụng

1. Refresh Applications.
2. Tìm đúng ứng dụng vừa mở.
3. Nhấn **Stop** và xác nhận.

Kết quả mong đợi: đúng ứng dụng đóng, các ứng dụng khác không bị ảnh hưởng.

## 8. Test Processes

1. Chọn **Processes**.
2. Nhấn **Refresh Processes**.

Kết quả mong đợi:

- Danh sách hiển thị process name, PID, RAM và status.
- Tên process đọc được, không bị cắt hoặc đè lên cột khác.
- Process hệ thống có nút **Guarded** và không thể kill.
- Process thường có nút **Kill**.

Để test Kill an toàn:

1. Mở Notepad trên máy Agent.
2. Refresh Processes.
3. Tìm `notepad.exe`.
4. Nhấn **Kill** và xác nhận.

Kết quả mong đợi: Notepad đóng và command chuyển sang `succeeded`.

Không kill process hệ thống hoặc process chứa dữ liệu chưa lưu.

## 9. Test Screen

1. Chọn **Screen**.
2. Nhấn **Start Live**.
3. Trên Agent, chọn **Yes**.
4. Di chuyển một cửa sổ trên máy Agent.

Kết quả mong đợi:

- Viewer cập nhật liên tục, không chỉ trả một ảnh tĩnh.
- Nội dung thay đổi trên Agent xuất hiện trên Web.
- FPS, frame count và latency được cập nhật.
- Nút fullscreen mở viewer toàn màn hình.
- Nhấn `Esc` thoát fullscreen.
- **Stop Live** dừng tăng frame count.

Sau khi test xong, bắt buộc nhấn **Stop Live**.

## 10. Test Files

### Chuẩn bị trên Agent

1. Tạo một thư mục test riêng.
2. Tạo một file `.txt` không chứa dữ liệu cá nhân.
3. Trong Agent, nhấn **Add Allowed Folder** và chọn thư mục test.

### Thực hiện trên Web

1. Chọn **Files**.
2. Nhấn **Browse Allowed Folder**.
3. Mở thư mục con bằng **Open**.
4. Nhấn **Download** trên file test.
5. Approve tại Agent nếu được hỏi.

Kết quả mong đợi:

- Chỉ thấy allowed folders.
- Tên file, đường dẫn, loại và dung lượng hiển thị đúng.
- Không thể duyệt tùy ý vào thư mục hệ thống ngoài allowed roots.
- File tải xuống mở được và nội dung không bị hỏng.

## 11. Test Webcam

1. Đóng ứng dụng khác đang sử dụng camera.
2. Chọn **Webcam**.
3. Nhấn **Start Live**.
4. Approve tại Agent.

Kết quả mong đợi:

- Viewer hiển thị hình ảnh từ camera thật.
- Không hiển thị nội dung màn hình Agent.
- Frame count tăng liên tục.
- Nhấn **Stop Live** thì stream dừng.

Nếu Webcam hiển thị màn hình thay vì camera, ghi nhận bug và báo rõ version Agent đang dùng.

## 12. Test Key Capture

1. Chọn **Key Capture**.
2. Nhấn **Start Visible Session**.
3. Approve tại Agent.
4. Gõ nội dung test trong cửa sổ `RemoteCtrl Visible Key Capture`.
5. Nhấn **Export Text** trên Web.

Kết quả mong đợi:

- Chỉ nội dung gõ trong cửa sổ Visible Key Capture được trả về.
- Nội dung gõ trong Notepad hoặc trình duyệt không được capture.
- **Stop Session** đóng hoặc kết thúc phiên đúng cách.

Không nhập mật khẩu hoặc dữ liệu thật trong phiên test.

## 13. Test Power

Các action gồm **Shutdown**, **Restart** và **Logout**.

Bản demo mặc định dùng `dry_run`, vì vậy máy Agent không được tắt hoặc restart thật.

Quy trình:

1. Chọn một Power action.
2. Xác nhận trên Web.
3. Approve tại Agent.

Kết quả mong đợi:

- Kết quả hiển thị `dry_run`.
- Audit ghi đúng action.
- Máy Agent không thực sự shutdown, restart hoặc logout.

Nếu máy thực hiện Power action thật trong bài test mặc định, dừng test và báo lỗi mức nghiêm trọng ngay.

## 14. Test Timeline và Audit

Sau mỗi nhóm chức năng, kiểm tra:

- **Command Timeline** có đúng command type.
- Trạng thái chuyển hợp lý: `queued` → `pending_approval` hoặc `running` → `succeeded`.
- Stop Live/Stop Session không tạo approval prompt.
- Lệnh bị từ chối chuyển thành `denied`.
- Lệnh lỗi chuyển thành `failed` và có thông báo rõ.
- **Audit Trail** có event tạo command, approval, result, stream start và stream stop.
- Timeline của module đang chọn không hiển thị nhầm kết quả module khác.

## 15. Test quản lý Agent

### Clear offline

1. Đóng một Agent để nó chuyển offline.
2. Nhấn **Clear offline**.

Kết quả mong đợi: Agent offline bị xóa, Agent online vẫn còn.

### Remove Agent online

1. Chọn một Agent đang online.
2. Nhấn **Remove** và xác nhận.

Kết quả mong đợi:

- Agent biến mất khỏi Dashboard.
- Kết nối Agent bị ngắt.
- Agent đó không tự kết nối lại bằng identity cũ.
- Muốn sử dụng lại phải enroll bằng token mới.

Chỉ thực hiện bài test này ở cuối phiên.

## 16. Kết thúc phiên test

1. Dừng Screen và Webcam live stream.
2. Dừng Key Capture session.
3. Đóng các ứng dụng test đã mở.
4. Đóng hoặc Pause Agent.
5. Xóa file test đã tải nếu không còn cần.
6. Sign out khỏi Web Dashboard.
7. Gửi test report cho team.

## 17. Xử lý lỗi nhanh

### Web không mở hoặc báo Gateway unavailable

- Chờ khoảng một phút rồi refresh.
- Mở `<gateway-url>/api/health`.
- Báo lại URL, thời điểm và ảnh lỗi nếu health check không trả `status: ok`.

### Agent offline

- Kiểm tra Agent vẫn mở và không ở trạng thái `Paused`.
- Kiểm tra Gateway URL public.
- Nhấn **Connect**.
- Refresh Web Dashboard.

### Enroll thất bại

- Tạo enrollment token mới.
- Copy token lại, không kèm khoảng trắng.
- Nếu vẫn lỗi, chụp trạng thái Agent và báo Gateway URL đang dùng.

### Lệnh đứng ở `pending_approval`

Đưa cửa sổ Agent lên trước và kiểm tra hộp thoại approval đang chờ.

### Stream đứng hình

- Nhấn **Stop Live**.
- Chờ vài giây rồi Start lại.
- Đảm bảo không có stream khác đang chạy.
- Ghi lại FPS, latency và thời điểm xảy ra lỗi.

## 18. Checklist test nhanh

- [ ] Đăng nhập Web thật, không dùng Demo mode.
- [ ] Enroll và Connect Agent thành công.
- [ ] Agent online đúng tên và hostname.
- [ ] Applications list/start/stop hoạt động.
- [ ] Processes list và Kill Notepad hoạt động.
- [ ] Protected process hiển thị Guarded.
- [ ] Screen stream liên tục và fullscreen được.
- [ ] Files chỉ truy cập allowed folders.
- [ ] Webcam hiển thị camera thật.
- [ ] Key Capture chỉ ghi trong cửa sổ visible session.
- [ ] Power trả dry-run và không tác động máy.
- [ ] Deny approval không thực thi action.
- [ ] Allow once vẫn hỏi lại ở lần sau.
- [ ] Allow for session không hỏi lại trong cùng phiên và Audit ghi `session_cached`.
- [ ] Timeline và Audit ghi đúng.
- [ ] Clear offline và Remove hoạt động.
- [ ] Dừng mọi stream và sign out sau khi test.
