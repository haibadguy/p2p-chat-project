# 📌 KẾ HOẠCH HOÀN CHỈNH – HỆ THỐNG CHAT NGANG HÀNG (P2P)

**Thời gian:** 7 ngày (mỗi ngày 4 giờ)  
**Ngôn ngữ:** Python 3 (chỉ thư viện chuẩn: `socket`, `threading`, `json`, `uuid`, `time`)  
**Mục tiêu:** Xây dựng ứng dụng chat P2P với bootstrap server, peer discovery, chat 1‑1, broadcast flooding, heartbeat và xử lý offline.

---

## 1. TỔNG QUAN VÀ KIẾN TRÚC

### 1.1 Mô hình kiến trúc (bootstrap‑assisted P2P)

```
                     Bootstrap Server (TCP, port 5555)
                     (quản lý danh sách peer online)
                              |
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
    Peer A (TCP)          Peer B (TCP)          Peer C (TCP)
   (server & client)     (server & client)     (server & client)
        │                     │                     │
        └──────── Chat trực tiếp (P2P) ─────────────┘
```

- **Bootstrap server** chỉ làm nhiệm vụ:
  - Nhận đăng ký (register), heartbeat, leave.
  - Trả danh sách peer đang online.
- **Peer** vừa là server (lắng nghe kết nối TCP đến), vừa là client (kết nối đến bootstrap và đến peer khác).
- **Giao tiếp tin nhắn**:
  - Chat 1‑1: mở kết nối TCP mới → gửi → đóng.
  - Broadcast flooding: mỗi tin có `msg_id` (UUID) và `ttl` (3). Peer chưa thấy tin → hiển thị → forward giảm TTL.
- **Phát hiện offline**: heartbeat gửi đến bootstrap mỗi 30 giây; bootstrap xoá peer nếu quá 60 giây không nhận heartbeat.

### 1.2 Tại sao chọn phương án này?
- ✅ Đúng yêu cầu đề bài (cho phép dùng bootstrap/tracker).
- ✅ Dễ triển khai, ít rủi ro (không cần UDP multicast, NAT traversal).
- ✅ Code đơn giản, dễ debug, dễ demo.
- ✅ Vẫn đảm bảo bản chất P2P (chat trực tiếp peer–peer).

---

## 2. CẤU TRÚC THƯ MỤC

```
p2p-chat/
├── bootstrap_server.py          # Chạy riêng
├── peer.py                      # Mỗi peer chạy file này
├── common/
│   ├── __init__.py
│   ├── message.py               # Định nghĩa các loại message
│   └── utils.py                 # Hàm tiện ích (gửi/nhận json)
├── README.md
├── .gitignore
└── docs/
    └── architecture.png         # (tuỳ chọn) sơ đồ kiến trúc
```

**Lưu ý:** Chỉ dùng thư viện chuẩn, không cài thêm `cryptography`, `msgpack`, `npyscreen` nếu không cần.

---

## 3. LỘ TRÌNH CHI TIẾT 7 NGÀY

> **AI NOTES:**  
> - Mỗi ngày kết thúc phải commit code và đạt **tiêu chí “Done”**.  
> - Dùng `threading` cho các tác vụ nền (server accept, heartbeat, kiểm tra timeout).  
> - Xử lý lỗi cơ bản (`socket.error`, `json.JSONDecodeError`, `ConnectionRefusedError`).  
> - Gửi/nhận JSON có kết thúc bằng `\n` để phân tách.  
> - Dùng `socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)` để tránh lỗi “Address already in use”.  
> - Luôn đặt timeout (5 giây) cho các socket kết nối ra ngoài.  
> - **Commit sau mỗi ngày** với message rõ ràng.

### 🔹 Ngày 1 – Bootstrap server

**File:** `bootstrap_server.py`

**Công việc chi tiết:**
1. Tạo TCP socket, bind `('0.0.0.0', 5555)`, `listen(5)`.
2. Vòng lặp chính `accept()` – mỗi kết nối tạo thread `handle_client`.
3. Dùng `threading.Lock` để đồng bộ truy cập danh sách peer.
4. Danh sách peer: mỗi peer là dict: `{ip, port, name, last_heartbeat}`.
5. Xử lý các loại JSON message:
   - `{"type": "register", "ip": "...", "port": 1234, "name": "Alice"}`
   - `{"type": "get_peers"}` (trả về danh sách peer, trừ chính nó)
   - `{"type": "heartbeat", "ip": "...", "port": 1234}`
   - `{"type": "leave", "ip": "...", "port": 1234}`
6. Thread nền: mỗi 10 giây kiểm tra `last_heartbeat`, xoá peer nếu `now - last_heartbeat > 60`.

**Tiêu chí “Done”:**
- Chạy `bootstrap_server.py` → không lỗi, lắng nghe port 5555.
- Dùng script nhỏ (hoặc `telnet`) gửi JSON đăng ký → server in ra “Registered”.
- Gửi `get_peers` → nhận được `{"peers": []}` hoặc danh sách.
- Gửi heartbeat → cập nhật `last_heartbeat`.
- Gửi leave → peer biến mất khỏi danh sách.

**Commit:** `git commit -m "Day1: bootstrap server with register, heartbeat, leave"`

---

### 🔹 Ngày 2 – Peer kết nối bootstrap + nhận danh sách

**File:** `peer.py`

**Cấu trúc class `Peer`:**
```python
class Peer:
    def __init__(self, ip, port, name, bootstrap_host='localhost', bootstrap_port=5555):
        self.ip = ip
        self.port = port
        self.name = name
        self.bootstrap_addr = (bootstrap_host, bootstrap_port)
        self.peer_list = []          # [{'ip':..., 'port':..., 'name':...}, ...]
        self.running = True
        self.server_socket = None
        self.seen_messages = set()    # lưu msg_id đã nhận (chống lặp)
        self.lock = threading.Lock()  # đồng bộ truy cập peer_list
```

**Các phương thức cần viết:**
- `register_with_bootstrap()`: kết nối đến bootstrap → gửi `register` → nhận danh sách → gán vào `self.peer_list`.
- `start_server()`: tạo socket server, bind `(self.ip, self.port)`, `listen(5)`, vòng lặp accept tạo thread `handle_incoming`.
- `handle_incoming(conn)`: nhận JSON, xử lý (sẽ phát triển ngày 3).
- `update_peer_list(peers)`: cập nhật `self.peer_list`.
- `heartbeat_loop()`: mỗi 30 giây gửi heartbeat đến bootstrap.
- `update_peer_list_loop()`: mỗi 60 giây gửi `get_peers` để cập nhật danh sách peer mới.

**Tiêu chí “Done”:**
- Chạy bootstrap server (terminal 1).
- Chạy peer A: `python peer.py --ip 127.0.0.1 --port 5001 --name Alice`.
- Peer A kết nối thành công, in ra danh sách peer (ban đầu rỗng).
- Chạy peer B (port 5002) → peer A nhận được danh sách có B.

**Commit:** `git commit -m "Day2: peer can register and get peer list from bootstrap"`

---

### 🔹 Ngày 3 – Chat 1-1 trực tiếp

**Công việc:**
- Thêm luồng nhập lệnh CLI (dùng `input` trong thread chính).
- Lệnh: `!send <index> <message>` hoặc `!send <ip> <port> <message>`.
- Hàm `send_message(target_ip, target_port, content)`:
  - Tạo socket TCP mới, kết nối đến `target_ip:target_port` (timeout 5s).
  - Gửi JSON: `{"type": "chat", "from": self.name, "content": content, "msg_id": str(uuid.uuid4())}` + `\n`.
  - Đóng kết nối.
- `handle_incoming`: nếu `type == "chat"` → in ra `[<from>]: <content>`.

**Tiêu chí “Done”:**
- Peer A gửi `!send 127.0.0.1 5002 "Hello Bob"` → Peer B nhận và in ra.
- Hai peer chat qua lại được.

**Commit:** `git commit -m "Day3: direct 1-1 messaging between peers"`

---

### 🔹 Ngày 4 – Broadcast flooding (chuyển tiếp tin nhắn)

**Cơ chế:**
- Mỗi peer có `seen_messages = set()`.
- Lệnh CLI: `!broadcast <message>` → gửi đến tất cả peer trong `peer_list` (trừ chính nó) với `ttl = 3` và `msg_id` mới.
- Định dạng broadcast: `{"type": "broadcast", "from": self.name, "content": "...", "msg_id": "...", "ttl": 3}`.
- Trong `handle_incoming`, nếu `type == "broadcast"`:
  - Nếu `msg_id` trong `seen_messages` → bỏ qua.
  - Nếu chưa thấy: thêm vào `seen_messages`, in ra `[Broadcast from {from}]: {content}`.
  - Nếu `ttl > 0`: tạo bản sao, giảm `ttl` đi 1, forward đến tất cả peer trong `peer_list` (trừ peer đã gửi tin này cho mình). Forward bằng cách gọi `send_message` (mở kết nối mới) hoặc gửi trực tiếp nếu đã có kết nối.

**Tiêu chí “Done”:**
- 3 peer A, B, C: A gửi `!broadcast "Hello all"` → B nhận, C nhận (qua B forward).
- Kiểm tra không bị lặp (cùng `msg_id` chỉ xuất hiện một lần).

**Commit:** `git commit -m "Day4: broadcast flooding with TTL and deduplication"`

---

### 🔹 Ngày 5 – Heartbeat, phát hiện offline và cập nhật danh sách

**Bootstrap (đã có cơ bản):**  
- Giữ nguyên thread xoá peer sau 60s không heartbeat.

**Peer:**
- Thêm `heartbeat_loop()`: mỗi 30 giây, gửi `{"type": "heartbeat", "ip": self.ip, "port": self.port}` đến bootstrap.
- Lệnh CLI: `!peers` → in `self.peer_list` đẹp (kèm index, ip, port, name).
- Lệnh CLI: `!leave` → gửi `leave` đến bootstrap → thoát chương trình.
- Định kỳ (mỗi 60 giây) gửi `get_peers` đến bootstrap để cập nhật `peer_list`.

**Tiêu chí “Done”:**
- 3 peer online. Tắt 1 peer (Ctrl+C) → sau 60 giây, `!peers` của các peer còn lại không thấy peer đó.
- Dùng `!leave` → peer biến mất khỏi danh sách ngay lập tức.

**Commit:** `git commit -m "Day5: heartbeat and offline detection"`

---

### 🔹 Ngày 6 – Tính năng nâng cao (ưu tiên gửi file)

**Chỉ làm nếu đã hoàn thành MVP (các ngày 1‑5).**

**Chọn 1 trong các tính năng (theo đề bài khuyến khích):**
- **Gửi file đơn giản** (dễ hơn mã hóa, vẫn ấn tượng):
  - Lệnh `!sendfile <index> <filepath>`.
  - Đọc file nhị phân, base64 encode, gửi JSON `{"type": "file", "from": self.name, "to_name": "...", "filename": "...", "data": base64_string}`.
  - Peer nhận: giải base64, ghi file vào thư mục `received/`.
  - Giới hạn file < 1MB, không chunk, không ACK (đủ để demo).
- **Hoặc broadcast toàn mạng** (đã làm ngày 4 – coi như hoàn thành).
- **Không bắt buộc** làm mã hóa hay GUI.

**Tiêu chí “Done” (nếu chọn gửi file):**
- Peer A gửi file `.txt` đến B → B nhận và lưu đúng nội dung.

**Commit:** `git commit -m "Day6: optional file transfer"`

---

### 🔹 Ngày 7 – Kiểm thử, viết báo cáo, quay video

**Công việc cụ thể:**
1. **Kiểm thử toàn bộ hệ thống** với 3‑4 peer trên cùng máy (dùng localhost, port 5001, 5002, 5003, 5004):
   - Register, peer list.
   - Chat 1‑1.
   - Broadcast flooding (đảm bảo không lặp).
   - Heartbeat + offline detection.
   - (Nếu có) Gửi file.
2. **Quay video demo** (tối đa 5 phút), thể hiện rõ từng chức năng. Upload lên YouTube (chế độ không công khai – unlisted).
3. **Viết README.md**:
   - Cách cài đặt (chạy `bootstrap_server.py`, sau đó mỗi peer chạy `peer.py` với các tham số).
   - Các lệnh CLI: `!send`, `!broadcast`, `!peers`, `!leave`.
   - Link video demo.
4. **Viết báo cáo** (theo mẫu đề bài):
   - Kiến trúc hệ thống (kèm sơ đồ).
   - Giao thức trao đổi thông điệp (JSON).
   - Cơ chế peer discovery (bootstrap server, heartbeat).
   - Xử lý lỗi và thử nghiệm.
   - **Phần “Giới hạn hệ thống”** (chỉ hoạt động trong LAN, bootstrap là single point of failure, chưa hỗ trợ NAT traversal, flooding chưa tối ưu băng thông). Đây là điểm cộng.
5. **Commit và push** lên GitHub.

**Commit cuối:** `git commit -m "Day7: final testing, report, video demo"`

---

## 4. NGUỒN THAM KHẢO VÀ LIÊN KẾT

### 4.1 Repo mẫu tham khảo (theo ngôn ngữ)

| Tên Repo | Ngôn ngữ | Mô tả | Link GitHub |
|----------|----------|-------|-------------|
| **BrayanZuluaga/PeerToPeer** | Python | Code đơn giản, peer tự phát hiện lẫn nhau (thủ công), dùng socket TCP/IP, threading. Rất phù hợp để học cấu trúc peer cơ bản. | [🔗 Link](https://github.com/BrayanZuluaga/PeerToPeer) |
| **F1xw/p2p-chat** | Python | CLI đẹp (npyscreen), dùng JSON, có lệnh `/connect`, `/nick`. Cấu trúc module rõ ràng. | [🔗 Link](https://github.com/F1xw/p2p-chat) |
| **CarbonIt-Labs/carbonit-messenger** | Python | Mã hóa end-to-end, xoay vòng khóa, hàng đợi tin nhắn offline, ACK – rất mạnh cho phần nâng cao. | [🔗 Link](https://github.com/CarbonIt-Labs/carbonit-messenger) |
| **RalphAKing/ptp-chat** | Python | Mã hóa RSA, GUI Tkinter, connection token. Thích hợp nếu muốn làm giao diện. | [🔗 Link](https://github.com/RalphAKing/ptp-chat) |
| **ahmedbakr7/p2p-python-chatapp** | Python | Có registry server (central), MongoDB, hỗ trợ group chat. Dễ học cách quản lý danh sách user. | [🔗 Link](https://github.com/ahmedbakr7/p2p-python-chatapp) |
| **sud295/sock-message** | Python | Bầu chọn leader theo Raft, heartbeat, mã hóa RSA. Rất tham khảo cho kiến trúc không điểm trung tâm. | [🔗 Link](https://github.com/sud295/sock-message) |
| **cipherswami/pychatudp** | Python | P2P qua UDP, đa luồng, decentralized. Gọn nhẹ (10KB). | [🔗 Link](https://github.com/cipherswami/pychatudp) |
| **DanielMPMatCom/P2P-Chat-Distributed-Systems-Project** | Python | Dùng CHORD ring, Docker, RSA, bcrypt, multicast discovery. Nâng cao nhưng rất chất lượng. | [🔗 Link](https://github.com/DanielMPMatCom/P2P-Chat-Distributed-Systems-Project) |
| **laike9m/PyPunchP2P** | Python | NAT traversal, STUN/TURN, P2P chat qua Internet. Dù cũ (2014) nhưng nguyên lý vẫn giá trị. | [🔗 Link](https://github.com/laike9m/PyPunchP2P) |
| **01alekseev/Petoron-P2P-Messenger** | Python | Zero dependencies, pure Python, end-to-end encryption, offline-capable, header obfuscation. | [🔗 Link](https://github.com/01alekseev/Petoron-P2P-Messenger) |
| **Zerva5/P2P-Encrypted-Chat** | Python | Nhóm project có encrypt, có video demo kèm theo. | [🔗 Link](https://github.com/Zerva5/P2P-Encrypted-Chat) |

**Khuyến nghị:** Dùng `BrayanZuluaga/PeerToPeer` làm khung, thêm bootstrap server từ `ahmedbakr7/p2p-python-chatapp`, tham khảo broadcast flooding từ `sud295/sock-message`.

### 4.2 Tài nguyên học tập (Video/Blog)

- **Video P2P Chat App in Python (hacked101):** hướng dẫn socket + threading.
- **Building a P2P Chat Application in Go (Medium):** hiểu UDP broadcast + TCP (có thể dịch sang Python).
- **“Viết ứng dụng Chat sử dụng giao thức P2P” (Thảo Meo TV, VB.NET):** video tiếng Việt, giải thích mô hình dễ hiểu.
- **P2P通信实战：如何用Python快速搭建一个简单的点对点聊天 application (CSDN):** bài viết tiếng Trung, code Python, có NAT traversal.
- **Petoron P2P Messenger (P-P2P-M) – DEV.to:** bài viết giới thiệu P2P messenger không phụ thuộc, offline-capable.

---

## 5. CHECKLIST HOÀN THÀNH DỰ ÁN

- [ ] **Bootstrap server**:
  - [ ] Nhận register, trả peer list.
  - [ ] Nhận heartbeat, cập nhật last_heartbeat.
  - [ ] Nhận leave, xoá peer.
  - [ ] Thread nền xoá peer timeout (60s).
- [ ] **Peer**:
  - [ ] Kết nối bootstrap, đăng ký, nhận peer list.
  - [ ] Server thread lắng nghe TCP.
  - [ ] Chat 1‑1 (gửi qua kết nối mới).
  - [ ] Broadcast flooding (msg_id, TTL, seen set).
  - [ ] Heartbeat gửi đến bootstrap (30s).
  - [ ] Lệnh CLI: `!send`, `!broadcast`, `!peers`, `!leave`.
- [ ] **Xử lý lỗi**:
  - [ ] Bắt lỗi mất kết nối, peer offline.
  - [ ] Cập nhật peer list khi có thay đổi.
- [ ] **Tính năng nâng cao (ít nhất 1)**:
  - [ ] Gửi file (base64) hoặc broadcast toàn mạng (đã có).
- [ ] **Kiểm thử & tài liệu**:
  - [ ] Chạy thử 3 peer thành công.
  - [ ] README.md hướng dẫn cài đặt, chạy, link video.
  - [ ] Báo cáo (kiến trúc, giao thức, peer discovery, xử lý lỗi, giới hạn).
  - [ ] Video demo (YouTube unlisted).

---

## 6. LƯU Ý CUỐI CHO AI KHI SINH CODE

- **Dùng JSON với ký tự newline (`\n`) để phân tách message.**  
  Mỗi lần `sendall(json.dumps(msg) + "\n")`.
- **Xử lý thread an toàn:** Dùng `threading.Lock` khi truy cập `peer_list` or `seen_messages` (nếu có nhiều thread cùng đọc/ghi).
- **Dùng `socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)`** để tránh lỗi “Address already in use”.
- **Luôn đặt timeout** cho các socket kết nối ra ngoài (5 giây) để không bị treo.
- **Commit sau mỗi ngày** với message rõ ràng.

---

**Bây giờ, AI có thể bắt đầu sinh code theo từng ngày, bám sát các tiêu chí “Done”. Chúc thành công! 🚀**
