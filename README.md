# P2P Chat System – E2EE, Group Chat, File Transfer

![tests](https://github.com/haibadguy/p2p-chat-project/actions/workflows/tests.yml/badge.svg)

Ứng dụng chat ngang hàng (P2P) viết bằng Python 3, theo mô hình Bootstrap-assisted P2P, hỗ trợ mã hóa đầu cuối E2EE, nhắn tin 1-1, broadcast, nhóm chat, gửi file và store-and-forward cho peer offline.

## Kết quả chính

- Nhắn tin 1-1, broadcast và nhóm chat trực tiếp giữa các peer trong LAN.
- Gửi file mã hóa đầu cuối, có ACK/retry và giới hạn kích thước 2 MB.
- Peer offline nhận lại tin nhắn chờ qua cơ chế store-and-forward khi quay lại mạng.
- Có CLI, giao diện Tkinter và bộ kiểm thử tự động chạy trên Python 3.10 và 3.12.

## Demo trực quan

### Giao diện GUI

<img width="1280" height="764" alt="Giao diện chat 1-1" src="https://github.com/user-attachments/assets/b1f6fae9-534c-44d1-9a1c-e15b180373f6" />
<img width="1277" height="559" alt="Giao diện gửi file và nhóm chat" src="https://github.com/user-attachments/assets/b3ddba85-c0fb-4998-a656-00596adf03b9" />

### Store-and-forward khi peer offline

![CLI demo](docs/demo-cli.gif)

```powershell
py demo_churn.py
```

Demo tự dựng bootstrap, Alice, Bob, Carol rồi kiểm tra chat có ACK, gửi file mã hóa, lưu tin khi Carol offline và giao lại khi Carol quay lại mạng.

> GIF trên được dựng lại từ output thực tế của `py demo_churn.py`, không phải quay màn hình.

## Mục lục
- [Tổng quan](#tổng-quan)
- [Kiến trúc hệ thống](#kiến-trúc-hệ-thống)
- [Tính năng chính](#tính-năng-chính)
- [Giao thức nhắn tin](#giao-thức-nhắn-tin)
- [Yêu cầu môi trường](#yêu-cầu-môi-trường)
- [Hướng dẫn chạy chi tiết](#hướng-dẫn-chạy-chi-tiết)
- [Kịch bản chạy trên cùng LAN](#kịch-bản-chạy-trên-cùng-lan)
- [Cấu trúc thư mục](#cấu-trúc-thư-mục)
- [Kiểm thử và demo](#kiểm-thử-và-demo)
- [Ghi chú vận hành](#ghi-chú-vận-hành)
- [Bảo mật](#bảo-mật)
- [Giới hạn của hệ thống](#giới-hạn-của-hệ-thống)
- [Báo cáo và demo](#báo-cáo-và-demo)
- [Đóng góp](#đóng-góp)
- [License](#license)

## Tổng quan

Dự án gồm 3 thành phần chính:
- `bootstrap_server.py`: máy chủ bootstrap để đăng ký peer, lấy danh sách peer, heartbeat, leave và lưu tin nhắn chờ.
- `peer.py`: peer chạy dạng CLI, đóng vai trò vừa là server vừa là client.
- `peer_gui.py`: peer chạy giao diện đồ họa Tkinter/ttk.

Luồng hoạt động tổng quát:
1. Mỗi peer đăng ký vào bootstrap.
2. Bootstrap lưu danh sách peer đang online.
3. Peer trao đổi khóa Diffie-Hellman khi gửi tin nhắn lần đầu.
4. Nội dung chat/file được mã hóa E2EE trước khi gửi.
5. Nếu peer đích offline, bootstrap giữ lại tin nhắn để giao sau.

## Kiến trúc hệ thống

### Bootstrap server
- Lắng nghe TCP mặc định tại cổng `5555`.
- Duy trì registry peer đang online.
- Nhận `register`, `heartbeat`, `leave`.
- Lưu `store-and-forward` cho peer offline.
- Trả về batch tin nhắn chờ khi peer quay lại.

### Peer node
- Mỗi peer là một TCP server cục bộ để nhận tin nhắn từ peer khác.
- Đồng thời là TCP client khi gửi tin nhắn, gửi file hoặc lấy danh sách peer.
- Tự động thực hiện trao đổi khóa E2EE khi cần.

### Giao diện người dùng
- CLI: phù hợp để test nhanh, chạy nhiều terminal.
- GUI: thuận tiện thao tác, hiển thị tên peer, broadcast, chat 1-1, gửi file và nhóm chat.

## Tính năng chính

- E2EE bằng Diffie-Hellman + mã hóa đối xứng.
- Gửi chat 1-1 có ACK/retry.
- Broadcast toàn mạng.
- Group chat: tạo nhóm, thêm peer, gửi tin nhắn nhóm, rời nhóm.
- Gửi file mã hóa đầu cuối.
- Store-and-forward cho peer offline.
- Churn simulation ở CLI để mô phỏng join/leave liên tục.
- Hỗ trợ chạy trên nhiều máy trong cùng mạng LAN.

## Giao thức nhắn tin

Mọi message đều là JSON và kết thúc bằng ký tự xuống dòng `\n`.

### 1. Register
```json
{
  "type": "register",
  "ip": "192.168.1.10",
  "port": 5001,
  "name": "Alice"
}
```

### 2. Key exchange
```json
{
  "type": "key_exchange",
  "from": "Alice",
  "from_ip": "192.168.1.10",
  "from_port": 5001,
  "pub_key": "..."
}
```

### 3. Chat
```json
{
  "type": "chat",
  "from": "Alice",
  "from_ip": "192.168.1.10",
  "from_port": 5001,
  "ciphertext": "<base64>",
  "iv": "<base64>",
  "msg_id": "uuid"
}
```

### 4. File
```json
{
  "type": "file",
  "from": "Alice",
  "from_ip": "192.168.1.10",
  "from_port": 5001,
  "filename": "document.pdf",
  "ciphertext": "<base64>",
  "iv": "<base64>",
  "msg_id": "uuid"
}
```

### 5. Store-and-forward
```json
{
  "type": "store_forward",
  "target_ip": "192.168.1.11",
  "target_port": 5002,
  "message": { ... }
}
```

## Yêu cầu môi trường

- Python 3.10+ (CI kiểm thử trên Python 3.10 và 3.12).
- Không cần thư viện bên ngoài cho runtime chính.
- Nếu dùng GUI, máy phải hỗ trợ Tkinter.
- Khi chạy LAN, nên mở firewall cho:
  - Cổng bootstrap: `5555`
  - Cổng peer: ví dụ `5001`, `5002`, `5003`, ...

### Cài đặt kiểm thử

Runtime của dự án chỉ dùng thư viện chuẩn Python. Nếu muốn chạy test, cài thêm dependency dev:

```powershell
py -m pip install -r requirements-dev.txt
```

Nếu máy không có Tkinter, hãy cài bộ Python đầy đủ kèm Tcl/Tk.

### Mở firewall

**Windows (PowerShell chạy quyền admin):**

```powershell
netsh advfirewall firewall add rule name="P2P Bootstrap" dir=in action=allow protocol=TCP localport=5555
netsh advfirewall firewall add rule name="P2P Peer 5001" dir=in action=allow protocol=TCP localport=5001
```

**Linux (Ubuntu):**

```bash
sudo ufw allow 5555/tcp
sudo ufw allow 5001:5010/tcp
```

## Hướng dẫn chạy chi tiết

### Bước 1: Mở terminal tại thư mục dự án

```powershell
git clone https://github.com/haibadguy/p2p-chat-project.git
cd p2p-chat-project
```

Trên Linux/macOS:

```bash
git clone https://github.com/haibadguy/p2p-chat-project.git
cd p2p-chat-project
```

### Bước 2: Đồng bộ source mới nhất từ `main`

```powershell
git pull origin main
```

```bash
git pull origin main
```

### Bước 3: Chạy bootstrap server

Bootstrap là thành phần bắt buộc phải chạy trước tất cả peer.

```powershell
py bootstrap_server.py
```

```bash
python3 bootstrap_server.py
```

Mặc định bootstrap lắng nghe tại:
- Host: `0.0.0.0`
- Port: `5555`

### Bước 4: Chạy peer CLI

Một peer CLI tối thiểu:

```powershell
py peer.py --name Alice --port 5001
```

```bash
python3 peer.py --name Alice --port 5001
```

Peer thứ hai:

```powershell
py peer.py --name Bob --port 5002
```

Nếu chạy trên máy khác nhau trong LAN, chỉ định rõ IP LAN của máy bootstrap:

```powershell
py peer.py --name Alice --ip 192.168.1.10 --port 5001 --bootstrap-host 192.168.1.10 --bootstrap-port 5555
py peer.py --name Bob --ip 192.168.1.11 --port 5002 --bootstrap-host 192.168.1.10 --bootstrap-port 5555
```

### Bước 5: Chạy peer GUI

```powershell
py peer_gui.py
```

```bash
python3 peer_gui.py
```

Trong form đăng nhập của GUI:
- Tên hiển thị: đặt tùy ý, ví dụ `Alice`
- Cổng lắng nghe: ví dụ `5001`
- IP Bootstrap: nhập IP LAN của máy chạy `bootstrap_server.py`
- Cổng Bootstrap: `5555`

Lưu ý:
- GUI sẽ tự hiển thị IP LAN của chính máy trên sidebar.
- Khi dùng LAN thật, mỗi máy phải dùng một cổng peer khác nhau.

### Bước 6: Gửi tin nhắn và file trong CLI

Các lệnh chính trong CLI:

```powershell
!peers
!send <index/ip:port> <tin nhắn>
!broadcast <tin nhắn>
!sendfile <index/ip:port> <duong_dan_file>
!gcreate <ten_nhom>
!gadd <ten_nhom> <index>
!gsend <ten_nhom> <tin nhắn>
!gleave <ten_nhom>
!glist
!leave
!help
```

Ví dụ:

```powershell
!peers
!send 1 Xin chao Bob
!broadcast Xin chao toan mang
!sendfile 1 test.txt
```

### Bước 7: Churn simulation trên CLI

Chạy peer ở chế độ mô phỏng rời mạng và quay lại:

```powershell
py peer.py --name Alice --port 5001 --churn --churn-online-seconds 20 --churn-offline-seconds 10
```

Tùy chọn:
- `--churn-cycles N`: số lần rời/quay lại mạng.
- `--churn-online-seconds`: thời gian online trước khi rời mạng.
- `--churn-offline-seconds`: thời gian offline trước khi quay lại.

## Kịch bản chạy trên cùng LAN

Giả sử có 3 máy trong cùng mạng nội bộ:

- Máy A chạy bootstrap server: `192.168.1.10`
- Máy B chạy Alice: `192.168.1.11`
- Máy C chạy Bob: `192.168.1.12`

### Trên máy A

```powershell
cd p2p-chat-project
py bootstrap_server.py
```

### Trên máy B

```powershell
cd p2p-chat-project
py peer_gui.py
```

Điền:
- Tên hiển thị: `Alice`
- Cổng lắng nghe: `5001`
- IP Bootstrap: `192.168.1.10`
- Cổng Bootstrap: `5555`

### Trên máy C

```powershell
cd p2p-chat-project
py peer_gui.py
```

Điền:
- Tên hiển thị: `Bob`
- Cổng lắng nghe: `5002`
- IP Bootstrap: `192.168.1.10`
- Cổng Bootstrap: `5555`

### Điều cần nhớ khi chạy LAN

- Không dùng cùng một cổng peer trên nhiều máy.
- Peer phải dùng IP LAN thật của máy đó, không phải `127.0.0.1`.
- Máy chạy bootstrap phải cho phép inbound TCP port `5555`.
- Máy chạy peer phải cho phép inbound TCP port của peer đó.

### Sơ đồ luồng chạy LAN

```mermaid
flowchart LR
  A[Máy A: Bootstrap Server\n192.168.1.10:5555] --> B[Máy B: Alice\n192.168.1.11:5001]
  A --> C[Máy C: Bob\n192.168.1.12:5002]

  B -- Register / Heartbeat --> A
  C -- Register / Heartbeat --> A

  B -- Get peer list --> A
  C -- Get peer list --> A

  B -- Direct chat / file / group --> C
  C -- ACK / reply --> B

  B -- Store-and-forward nếu Bob offline --> A
  C -- Rejoin & fetch pending --> A
  A -- Deliver pending messages --> C
```

Luồng trên minh họa cách bootstrap làm trung tâm điều phối, còn các peer vẫn giao tiếp trực tiếp với nhau sau khi đã biết địa chỉ LAN của nhau.

## Cấu trúc thư mục

```text
bootstrap_server.py   # Bootstrap registry + store-and-forward
peer.py               # Peer CLI
peer_gui.py           # Peer GUI
common/
  encryption.py       # Diffie-Hellman + mã hóa/giải mã
  message.py          # Định nghĩa message type
  utils.py            # JSON socket helpers + tiện ích IP
demo_churn.py         # Demo churn + store-and-forward
requirements-dev.txt   # Dependency cho test
tests/                # Pytest suite
received/             # Nơi lưu file nhận được
docs/
  bao-cao.pdf          # Báo cáo dự án
  demo-cli.gif         # GIF demo store-and-forward (dựng từ output thật)
```

## Kiểm thử và demo

### Cài dependency test

```powershell
py -m pip install -r requirements-dev.txt
```

### Chạy toàn bộ test

```powershell
py -m pytest -q
```

### Chạy test riêng phần peer

```powershell
py -m pytest -q tests/test_p2p_features.py
```

### Chạy demo churn + store-and-forward

```powershell
py demo_churn.py
```

Demo này sẽ tự dựng bootstrap, Alice, Bob, Carol, rồi kiểm tra:
- đăng ký peer
- chat có ACK
- gửi file mã hóa
- store-and-forward khi peer offline
- peer quay lại và nhận tin nhắn chờ

## Ghi chú vận hành

- File nhận được sẽ nằm trong thư mục `received/`.
- Nếu GUI không mở được trên máy hiện tại, hãy dùng CLI để kiểm tra luồng mạng.
- Nếu chạy LAN mà peer không thấy nhau, kiểm tra firewall và IP bootstrap đã nhập đúng chưa.
- Nếu chỉ có một peer online, danh sách peer khác có thể rỗng vì hệ thống không hiện chính peer đang đăng ký.

## Bảo mật

- Mỗi cặp peer trao đổi khóa Diffie-Hellman trên nhóm MODP 2048-bit của RFC 3526 (group 14, safe prime) với generator `g = 2`; khóa chung `B^a mod p` được băm SHA-256 thành khóa phiên 256-bit.
- Nội dung chat và file được mã hóa tại peer gửi trước khi truyền, nên bootstrap chỉ chuyển tiếp bản mã của tin nhắn chờ.
- Mã hóa đối xứng hiện tại là XOR với keystream SHA-256 sinh từ `SHA-256(key || IV || counter)`, với IV ngẫu nhiên 16 byte; đây là cơ chế tự triển khai, không phải AES-GCM hay ChaCha20-Poly1305 và không cung cấp xác thực/toàn vẹn bản mã.
- Trao đổi khóa chưa xác thực danh tính peer và không có chữ ký/fingerprint đáng tin cậy, vì vậy hệ thống chưa chống được tấn công man-in-the-middle. Không dùng ứng dụng này để truyền dữ liệu nhạy cảm trong môi trường không tin cậy.
- Khóa phiên chỉ lưu trong bộ nhớ (in-memory), không ghi ra đĩa; khi peer khởi động lại, khóa cũ mất và phải trao đổi khóa lại.

## Giới hạn của hệ thống

- Hiện tại chỉ hoạt động tốt trong môi trường **mạng LAN**.
- Chưa hỗ trợ **NAT traversal**; muốn đi qua Internet cần cấu hình port forwarding thủ công.
- **Bootstrap server** là điểm tập trung duy nhất, nên nếu nó dừng thì peer mới không thể vào mạng.
- Gửi file rất lớn có thể làm tăng độ trễ do cơ chế ACK/retry.
- File được nạp toàn bộ vào bộ nhớ và giới hạn 2 MB; chưa hỗ trợ truyền theo luồng (streaming) hoặc tiếp tục truyền khi gián đoạn.
- Chống nhận trùng `msg_id` hiện mới áp dụng cho broadcast/group; chat 1-1 và file có thể bị xử lý lặp lại nếu ACK bị mất và bên gửi retry.
- Kết nối peer đóng đột ngột được xử lý bằng timeout/retry nhưng chưa có cơ chế phát hiện hoặc phục hồi kết nối toàn diện.
- **Khóa phiên không tồn tại qua lần khởi động lại.** Mỗi lần chạy lại, peer sinh cặp khóa Diffie-Hellman mới, nên tin nhắn đang chờ gửi tới peer vừa restart sẽ không giải mã được và bị bỏ qua. Đây là hệ quả của thiết kế E2EE (không ghi khóa phiên ra đĩa), không phải lỗi.
- **Chưa có giao thức re-handshake tự động.** Để giao được tin chờ sau khi peer restart cần: phát hiện public key thay đổi, trao đổi khóa lại và yêu cầu bên gửi gửi lại. Đây là hướng phát triển tiếp theo.
- Chưa lưu lịch sử tin nhắn cục bộ sau khi thoát ứng dụng.

## Báo cáo và demo

- [Đọc báo cáo dự án](docs/bao-cao.pdf)
- Ảnh giao diện và demo store-and-forward: xem mục [Demo trực quan](#demo-trực-quan) ở đầu README.
- Kịch bản quay GIF bằng ScreenToGif: phần GUI quay chat 1-1, gửi file, tạo nhóm và gửi tin nhắn nhóm; phần store-and-forward quay khi chạy `py demo_churn.py`.

## Đóng góp

- Tạo branch mới từ `main`.
- Commit nên ngắn gọn, đúng phạm vi, ví dụ `feat:`, `fix:`, `docs:`.
- Giữ thay đổi tập trung, tránh sửa lan man.

## License

[MIT License](LICENSE).

