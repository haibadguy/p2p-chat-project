# 📘 HƯỚNG DẪN GIẢI THÍCH TOÀN BỘ DỰ ÁN HỆ THỐNG CHAT P2P (ELITE VERSION)

Tài liệu này được biên soạn chi tiết nhằm giúp thành viên trong nhóm hiểu sâu sắc từng dòng code, kiến trúc mạng, cơ chế bảo mật E2EE và tự tin trả lời mọi câu hỏi của Giảng viên khi bảo vệ đồ án.

---

## 1. KIẾN TRÚC TỔNG QUAN HỆ THỐNG

Dự án sử dụng mô hình **Bootstrap-Assisted P2P** (Mạng ngang hàng có hỗ trợ định vị).

```
                     Bootstrap Server (TCP, port 5555)
                     (Chỉ quản lý danh sách peer online)
                               |
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
    Peer A (E2EE)          Peer B (E2EE)          Peer C (E2EE)
   (server & client)      (server & client)      (server & client)
        │                      │                      │
        └──────── Chat & File E2EE trực tiếp ─────────┘
```

### ❓ Tại sao gọi là P2P khi vẫn có Server trung tâm?
*   **Bootstrap Server (Tracker/Registry)** không tham gia vào quá trình truyền tin nhắn hay truyền file. Vai trò duy nhất của nó là **định vị (discovery)**: Khi một peer online, nó đăng ký địa chỉ IP/Port của mình lên server; khi muốn chat, peer lên server lấy danh sách các peer đang online về.
*   **Truyền tin trực tiếp**: Khi Alice muốn nhắn tin hay gửi file cho Bob, Alice sẽ tự động mở một kết nối TCP trực tiếp tới địa chỉ IP và Port của Bob (không đi qua Bootstrap Server). Do đó, luồng dữ liệu chính là **ngang hàng (Peer-to-Peer)** hoàn toàn.

---

## 2. CẤU TRÚC THƯ MỤC & VAI TRÒ CỦA TỪNG FILE

```
p2p-chat-project/
├── bootstrap_server.py      # Máy chủ trung tâm (chạy trước)
├── peer.py                  # Mã nguồn Peer chạy giao diện CLI dòng lệnh
├── peer_gui.py              # Mã nguồn Peer chạy giao diện đồ họa Desktop (Tkinter)
├── common/                  # Thư mục chứa các module dùng chung
│   ├── __init__.py          # Khai báo package python
│   ├── message.py           # Định nghĩa các loại thông điệp JSON
│   ├── utils.py             # Hàm tiện ích gửi/nhận JSON qua socket
│   └── encryption.py        # Trái tim bảo mật: Diffie-Hellman & Stream Cipher
├── received/                # Thư mục tự động tạo để lưu file nhận được
├── test.txt                 # File nháp dùng để kiểm thử gửi file E2EE
└── README.md                # Tài liệu hướng dẫn cài đặt và khởi chạy nhanh
```

### Chi tiết chức năng từng file:
1.  **`common/message.py`**: Định nghĩa các hằng số chuỗi cho các loại gói tin (ví dụ: `"register"`, `"chat"`, `"key_exchange"`, `"file"`). Đảm bảo tính thống nhất về mặt cú pháp dữ liệu.
2.  **`common/utils.py`**:
    *   `send_json(sock, msg_dict)`: Chuyển đổi từ Python `dict` sang chuỗi JSON, nối thêm ký tự xuống dòng `\n` và gửi qua socket.
    *   `recv_json(sock)`: Đọc dữ liệu từ socket cho đến khi gặp ký tự `\n` (dùng `makefile('r')` để đọc theo dòng một cách hiệu quả), giải mã JSON thành `dict`.
3.  **`common/encryption.py`**:
    *   Lớp `DiffieHellman`: Khởi tạo cặp khóa (Private/Public), thực hiện phép toán lũy thừa đồng dư để sinh khóa chung (Shared Secret).
    *   Các hàm `encrypt_string`, `decrypt_string`, `encrypt_bytes`, `decrypt_bytes`: Thực hiện thuật toán mã hóa đối xứng dòng (Stream Cipher) để mã hóa/giải mã tin nhắn và file.
4.  **`bootstrap_server.py`**: Lắng nghe cổng 5555. Chứa luồng chính nhận kết nối và một luồng chạy nền (`sweep_inactive_peers`) quét dọn các peer không gửi heartbeat quá 60 giây.
5.  **`peer.py` & `peer_gui.py`**: Chứa logic của nút mạng. Chạy đa luồng để vừa làm server lắng nghe tin nhắn đến, vừa làm client gửi tin nhắn đi và gửi heartbeat lên Bootstrap.

---

## 3. CÁC LUỒNG HOẠT ĐỘNG CỐT LÕI (CORE WORKFLOWS)

### 3.1 Quy trình Tham gia mạng & Phát hiện Peer (Peer Discovery)
1.  Khi một Peer (ví dụ: Alice) khởi chạy, nó gửi một thông điệp đăng ký kiểu `MSG_TYPE_REGISTER` lên Bootstrap Server kèm theo tên, IP và Port lắng nghe của nó.
2.  Bootstrap Server lưu thông tin Alice vào danh sách bộ nhớ tạm kèm theo thời gian hiện tại (`last_heartbeat`).
3.  Alice định kỳ (mỗi 30 giây) gửi gói tin `heartbeat` lên Bootstrap để báo rằng mình vẫn online.
4.  Alice định kỳ (mỗi 60 giây) gửi gói tin `get_peers` lên Bootstrap để lấy về danh sách các peer đang hoạt động khác và hiển thị lên CLI/GUI.
5.  Nếu Alice tắt chương trình đột ngột, sau 60 giây Bootstrap sẽ phát hiện thiếu heartbeat và xóa Alice khỏi danh sách. Nếu Alice bấm thoát an toàn (`!leave`), chương trình sẽ gửi gói tin `leave` để xóa ngay lập tức.

### 3.2 Quy trình bắt tay E2EE và Trao đổi khóa Diffie-Hellman (DHKE)
Đây là phần **đắt giá nhất** của đồ án. Quá trình bắt tay trao đổi khóa diễn ra ngầm và hoàn toàn tự động khi Peer A gửi tin nhắn/file đầu tiên cho Peer B:

```
    Peer A (Alice)                                    Peer B (Bob)
          |                                                 |
          | ---- 1. Gửi KEY_EXCHANGE (Public Key A) ------> |
          |                                                 |
          | <--- 2. Trả về KEY_EXCHANGE_REPLY (Pub Key B) - |
          |                                                 |
   Tính toán khóa chung:                             Tính toán khóa chung:
   Shared Key = B^a mod p                            Shared Key = A^b mod p
```

#### Toán học đằng sau Diffie-Hellman:
Hệ thống sử dụng một số nguyên tố lớn an toàn 512-bit $p$ (`DH_PRIME`) và số cơ sở $g = 2$ (`DH_GENERATOR`).
1.  Alice tự sinh khóa bí mật ngẫu nhiên $a$ (256-bit). Tính khóa công khai:
    $$A = g^a \pmod p$$
2.  Bob tự sinh khóa bí mật ngẫu nhiên $b$ (256-bit). Tính khóa công khai:
    $$B = g^b \pmod p$$
3.  Khi bắt tay, Alice gửi $A$ cho Bob, Bob gửi $B$ cho Alice.
4.  Alice tính toán:
    $$\text{Khóa chung} = B^a \pmod p = (g^b)^a \pmod p = g^{ab} \pmod p$$
5.  Bob tính toán:
    $$\text{Khóa chung} = A^b \pmod p = (g^a)^b \pmod p = g^{ab} \pmod p$$
6.  Cả hai đều tính ra cùng một giá trị khóa chung bí mật mà **không cần phải truyền khóa này qua mạng**. 
7.  Khóa chung được đưa qua hàm băm mật mã `SHA-256` để tạo ra khóa đối xứng 256-bit (`32 bytes`) dùng cho mã hóa dữ liệu.

### 3.3 Thuật toán Mã hóa dòng đối xứng (Symmetric Stream Cipher)
Hệ thống tự viết thuật toán mã hóa dòng dựa trên cấu trúc hoạt động của **AES-CTR**:
*   **Mã hóa**:
    1.  Sinh một Vector khởi tạo ngẫu nhiên 16-byte (`IV`) bằng `os.urandom(16)`.
    2.  Với mỗi khối dữ liệu 32-byte, tăng biến đếm `counter` lên 1.
    3.  Tạo dòng khóa bằng cách băm:
        $$\text{Keystream Block} = \text{SHA-256}(\text{Symmetric Key} \parallel \text{IV} \parallel \text{Counter})$$
    4.  Thực hiện phép toán XOR (`^`) giữa byte dữ liệu thô (`plaintext`) với byte của dòng khóa (`keystream`) để tạo ra bản mã (`ciphertext`).
    5.  Chuyển `ciphertext` và `iv` thành chuỗi Base64 để truyền an toàn qua định dạng JSON.
*   **Giải mã**: Người nhận dùng chung Khóa đối xứng và `IV` được gửi kèm trong gói tin JSON, chạy lại thuật toán XOR tương tự để thu về dữ liệu thô ban đầu (phép toán XOR có tính chất đối xứng nghịch đảo: $(P \oplus K) \oplus K = P$).

---

## 4. GIẢI THÍCH SÂU VỀ KỸ THUẬT LẬP TRÌNH (ADVANCED DEVELOPMENTS)

### 4.1 Cơ chế Đa luồng (Multi-threading)
Để một Peer hoạt động trơn tru mà không bị đứng nghẽn mạng, hệ thống phân chia thành các luồng (Thread) độc lập chạy nền:
1.  **Main Thread (Luồng chính)**: Chạy vòng lặp giao diện GUI (`root.mainloop()`) hoặc vòng lặp CLI nhập lệnh (`cli_loop`). Luồng này chịu trách nhiệm tương tác trực tiếp với người dùng.
2.  **Server Thread (Luồng Server mạng)**: Gọi phương thức `start_server()`, lắng nghe socket TCP trên port riêng của Peer. Mỗi khi có Peer khác kết nối tới để gửi tin nhắn/file, luồng này sẽ `accept()` và tạo ra một luồng phụ riêng biệt (`handle_incoming`) để xử lý gói tin đó, tránh chặn các kết nối đến sau.
3.  **Heartbeat Thread**: Chạy vòng lặp `heartbeat_loop` ngủ 30 giây rồi tự động gửi tín hiệu "tôi còn sống" lên Bootstrap Server.
4.  **Update Peer Thread**: Chạy vòng lặp `update_peer_list_loop` ngủ 60 giây rồi tự động kết nối Bootstrap Server để lấy danh sách peer mới nhất.

### 4.2 Thiết kế Thread-safe GUI trong `peer_gui.py` (Chống Treo/UI Freezing)
**Lưu ý cực kỳ quan trọng:** Thư viện đồ họa Tkinter của Python là **đơn luồng (single-threaded)**. Nếu bạn cố gắng cập nhật trực tiếp giao diện (như ghi thêm chữ vào khung chat, cập nhật bảng danh sách peer) từ luồng nhận tin nhắn TCP, chương trình sẽ lập tức bị crash hoặc treo đơ giao diện.

#### Giải pháp thiết kế vượt trội của hệ thống:
*   Sử dụng một hàng đợi an toàn đa luồng: `self.gui_queue = queue.Queue()`.
*   Khi luồng nhận tin nhắn TCP (`handle_incoming`) nhận được tin nhắn hoặc file, nó giải mã dữ liệu xong và chỉ làm một nhiệm vụ: đẩy dữ liệu vào hàng đợi:
    ```python
    self.gui_queue.put(("msg_in", sender, content))
    ```
*   Trong luồng giao diện chính (Main Thread), sử dụng phương thức `.after()` của Tkinter để lên lịch chạy một hàm kiểm tra định kỳ sau mỗi 100ms:
    ```python
    self.root.after(100, self.process_queue)
    ```
*   Hàm `process_queue` chạy hoàn toàn trên Main Thread. Nó kiểm tra nếu hàng đợi `gui_queue` có dữ liệu thì bốc ra (pop) và thực hiện cập nhật lên giao diện. Cơ chế non-blocking này giúp ứng dụng GUI chạy mượt mà 100% không bao giờ bị đơ.

---

## 5. BỘ CÂU HỎI VÀ ĐÁP ÁN BẢO VỆ ĐỒ ÁN (DEFENSE Q&A GUIDE)

Dưới đây là 5 câu hỏi "hóc búa" nhất mà các Thầy/Cô thường dùng để chấm điểm 9, 10. Hãy đọc kỹ cách trả lời dưới đây:

### 💬 Câu hỏi 1: Hệ thống của em có một Bootstrap Server trung tâm. Vậy đây có thực sự là hệ thống P2P không? Hay bản chất vẫn là Client-Server?
*   **Cách trả lời ghi điểm tuyệt đối:** 
    > "Dạ thưa Thầy/Cô, hệ thống của nhóm em là **mô hình mạng lai Bootstrap-Assisted P2P** (mạng ngang hàng có hỗ trợ). Vai trò của Bootstrap Server ở đây **chỉ là định vị (Peer Discovery)** tương tự như dịch vụ danh bạ điện thoại hay dịch vụ DNS. Nó chỉ quản lý thông tin địa chỉ (IP, Port) của các nút đang online chứ **hoàn toàn không trung chuyển bất kỳ một tin nhắn hay file dữ liệu nào**.
    > Khi hai nút (ví dụ: Alice và Bob) nhắn tin hoặc truyền file cho nhau, kết nối TCP được thiết lập trực tiếp giữa Alice và Bob. Dữ liệu truyền tải đi trực tiếp từ card mạng nút này sang card mạng nút kia mà không hề đi qua Server trung tâm. Vì luồng xử lý và truyền tải dữ liệu chính diễn ra trực tiếp giữa các nút mạng ngang hàng, nên bản chất hệ thống của nhóm em là **hệ thống phân tán P2P hoàn toàn** ạ."

### 💬 Câu hỏi 2: Tại sao các em lại sử dụng giao thức TCP thay vì UDP cho việc truyền tin nhắn và truyền file trực tiếp?
*   **Cách trả lời ghi điểm tuyệt đối:**
    > "Dạ thưa Thầy/Cô, nhóm em chọn **TCP (Transmission Control Protocol)** vì các lý do sau:
    > 1. **Tính tin cậy cao**: TCP đảm bảo gói tin được truyền đi thành công, không bị mất mát dữ liệu và tự động truyền lại nếu có lỗi đường truyền. Điều này cực kỳ quan trọng đối với truyền file nhị phân và tin nhắn văn bản.
    > 2. **Đảm bảo thứ tự**: TCP đảm bảo các gói tin đến đích đúng thứ tự gửi, giúp nội dung chat không bị đảo lộn.
    > 3. **Hướng kết nối**: Giúp hai peer duy trì trạng thái kết nối rõ ràng trong suốt quá trình bắt tay trao đổi khóa Diffie-Hellman và truyền tải file mã hóa.
    > Mặc dù UDP có tốc độ nhanh hơn nhưng không đảm bảo độ tin cậy và thứ tự gói tin, nên đối với ứng dụng chat và truyền file bảo mật E2EE thì TCP là lựa chọn tối ưu nhất ạ."

### 💬 Câu hỏi 3: Thuật toán mã hóa của các em hoạt động thế nào? Tại sao các em không sử dụng các thư viện mật mã chuẩn có sẵn như `PyCryptodome`?
*   **Cách trả lời ghi điểm tuyệt đối:**
    > "Dạ thưa Thầy/Cô, mục tiêu của nhóm em là xây dựng một hệ thống **Zero-Dependency (hoàn toàn chỉ dùng thư viện chuẩn Python)** để dễ dàng cài đặt, chạy demo trên mọi máy tính của nhà trường mà không gặp lỗi thiếu thư viện ngoài.
    > Do đó, nhóm em đã tự triển khai thuật toán mã hóa đầu cuối E2EE bằng cách:
    > 1. Dùng giao thức bắt tay trao đổi khóa **Diffie-Hellman** dựa trên phép toán lũy thừa đồng dư số nguyên tố lớn an toàn 512-bit để tự sinh ra khóa đối xứng dùng chung (Symmetric Key) mà không cần truyền khóa qua mạng.
    > 2. Tự viết thuật toán **mã hóa đối xứng dòng (Symmetric Stream Cipher)** hoạt động tương tự như chế độ **AES-CTR** bằng cách dùng hàm băm `SHA-256` kết hợp khóa đối xứng chung, vector khởi tạo ngẫu nhiên `IV` 16-byte và một biến đếm `counter` để tạo ra dòng keystream liên tục, sau đó thực hiện phép toán XOR (`^`) với dữ liệu thô.
    > Cơ chế này vẫn đảm bảo tính an toàn mật mã học cực cao vì keystream thay đổi liên tục theo từng khối và từng IV ngẫu nhiên, giúp ngăn chặn hiệu quả các cuộc tấn công nghe lén (Man-in-the-middle) hay tấn công phân tích tần suất mã hóa ạ."

### 💬 Câu hỏi 4: Làm thế nào để em giải quyết bài toán Churn (các peer tham gia và rời mạng liên tục một cách không báo trước)?
*   **Cách trả lời ghi điểm tuyệt đối:**
    > "Dạ thưa Thầy/Cô, nhóm em đã thiết lập cơ chế **Heartbeat & Sweep** để giải quyết bài toán Churn:
    > 1. Mỗi peer khi chạy sẽ có một luồng chạy nền tự động gửi một tín hiệu heartbeat nhỏ lên Bootstrap Server sau mỗi 30 giây để báo cáo trạng thái hoạt động.
    > 2. Trên Bootstrap Server, nhóm em chạy một luồng quét dọn nền (`sweep_inactive_peers`) hoạt động liên tục mỗi 10 giây. Luồng này sẽ kiểm tra thời gian heartbeat cuối của tất cả các peer, nếu có peer nào quá 60 giây không gửi heartbeat (do mất mạng đột ngột hoặc crash ứng dụng), server sẽ tự động xóa peer đó ra khỏi danh sách online.
    > 3. Các peer còn lại định kỳ kéo danh sách từ server về sẽ tự động cập nhật lại danh sách của mình, đảm bảo tính động và khả năng tự phục hồi của hệ thống khi cấu trúc mạng thay đổi liên tục ạ."

### 💬 Câu hỏi 5: Nếu Bootstrap Server bị sập đột ngột khi các peer đang hoạt động, hệ thống chat có tiếp tục chạy được không?
*   **Cách trả lời ghi điểm tuyệt đối:**
    > "Dạ thưa Thầy/Cô, đây chính là ưu điểm lớn của kiến trúc P2P lai:
    > *   Nếu Bootstrap Server bị sập, các peer mới sẽ **không thể tham gia** vào mạng được nữa và các peer hiện tại sẽ **không thể tìm thêm peer mới (Discovery bị vô hiệu hóa)**.
    > *   Tuy nhiên, đối với các peer **đã online và đã lưu danh sách peer trong bộ nhớ cache** từ trước đó, họ **vẫn hoàn toàn nhắn tin chat 1-1, trao đổi khóa Diffie-Hellman và truyền file trực tiếp cho nhau bình thường** vì kết nối TCP được thiết lập trực tiếp giữa các Peer với nhau chứ không đi qua Bootstrap Server. Điều này thể hiện tính chịu lỗi và tính bền vững cao của kiến trúc phân tán P2P ạ!"

---
Chúc các bạn bảo vệ đồ án thành công rực rỡ và đạt điểm số tối đa! 🚀
