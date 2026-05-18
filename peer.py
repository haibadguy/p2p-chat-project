import socket
import threading
import json
import uuid
import time
import sys
import os
import base64
import argparse

# Import shared modules
from common.message import (
    MSG_TYPE_REGISTER,
    MSG_TYPE_PEER_LIST,
    MSG_TYPE_GET_PEERS,
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_LEAVE,
    MSG_TYPE_CHAT,
    MSG_TYPE_BROADCAST,
    MSG_TYPE_FILE,
    MSG_TYPE_KEY_EXCHANGE,
    MSG_TYPE_KEY_EXCHANGE_REPLY
)
from common.utils import send_json, recv_json
from common.encryption import (
    DiffieHellman,
    encrypt_string,
    decrypt_string,
    encrypt_bytes,
    decrypt_bytes
)

# ANSI Color Codes for premium CLI style
COLOR_RESET = "\033[0m"
COLOR_SYSTEM = "\033[93m"    # Yellow
COLOR_CHAT = "\033[92m"      # Green
COLOR_BROADCAST = "\033[96m" # Cyan
COLOR_FILE = "\033[95m"      # Magenta
COLOR_ERROR = "\033[91m"     # Red
COLOR_BOLD = "\033[1m"

class Peer:
    def __init__(self, ip, port, name, bootstrap_host='127.0.0.1', bootstrap_port=5555):
        self.ip = ip
        self.port = port
        self.name = name
        self.bootstrap_addr = (bootstrap_host, bootstrap_port)
        
        self.peer_list = []          # List of active peers: [{"ip": str, "port": int, "name": str}]
        self.seen_messages = set()    # Set of msg_ids to prevent processing duplicates
        self.shared_keys = {}        # E2EE registry: {(ip, port): bytes_shared_key}
        self.running = True
        self.server_socket = None
        self.lock = threading.Lock()  # Synchronize access to shared resources

    def register_with_bootstrap(self):
        """
        Registers this peer node with the central Bootstrap Server.
        Returns True if successful, False otherwise.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.bootstrap_addr)
            register_msg = {
                "type": MSG_TYPE_REGISTER,
                "ip": self.ip,
                "port": self.port,
                "name": self.name
            }
            if send_json(s, register_msg):
                res = recv_json(s, timeout=5)
                if res and res.get("status") == "success":
                    return True
                else:
                    err_msg = res.get("message") if res else "Không nhận được phản hồi"
                    print(f"{COLOR_ERROR}[Lỗi Đăng Ký] {err_msg}{COLOR_RESET}")
            return False
        except socket.error as e:
            print(f"{COLOR_ERROR}[Lỗi Kết Nối] Không thể kết nối tới Bootstrap Server tại {self.bootstrap_addr[0]}:{self.bootstrap_addr[1]} ({e}){COLOR_RESET}")
            return False
        finally:
            s.close()

    def get_peer_list(self):
        """
        Fetches the latest online peer list from the Bootstrap Server.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.bootstrap_addr)
            req = {
                "type": MSG_TYPE_GET_PEERS,
                "ip": self.ip,
                "port": self.port
            }
            if send_json(s, req):
                res = recv_json(s, timeout=5)
                if res and res.get("type") == MSG_TYPE_PEER_LIST:
                    new_peers = res.get("peers", [])
                    with self.lock:
                        self.peer_list = new_peers
                    return True
            return False
        except Exception:
            return False
        finally:
            s.close()

    def send_heartbeat(self):
        """
        Sends a single heartbeat message to the Bootstrap Server.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.bootstrap_addr)
            req = {
                "type": MSG_TYPE_HEARTBEAT,
                "ip": self.ip,
                "port": self.port
            }
            send_json(s, req)
        except Exception:
            pass
        finally:
            s.close()

    def leave_network(self):
        """
        Informs the Bootstrap Server that this peer is leaving.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.bootstrap_addr)
            req = {
                "type": MSG_TYPE_LEAVE,
                "ip": self.ip,
                "port": self.port
            }
            send_json(s, req)
        except Exception:
            pass
        finally:
            s.close()

    def start_server(self):
        """
        Listens for incoming TCP connections from other peer nodes.
        """
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(5)
        except Exception as e:
            print(f"{COLOR_ERROR}[Lỗi Khởi Động Server] Không thể bind port {self.port}: {e}{COLOR_RESET}")
            self.running = False
            sys.exit(1)

        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                client_thread = threading.Thread(
                    target=self.handle_incoming, 
                    args=(conn, addr), 
                    daemon=True
                )
                client_thread.start()
            except Exception:
                break

    def ensure_shared_key(self, target_ip, target_port):
        """
        Checks if a shared symmetric key exists with the given peer.
        If not, automatically initiates a Diffie-Hellman handshake to establish E2EE.
        Returns the shared key bytes if successful, None otherwise.
        """
        target_port = int(target_port)
        with self.lock:
            if (target_ip, target_port) in self.shared_keys:
                return self.shared_keys[(target_ip, target_port)]
                
        # Perform DH handshake
        print(f"{COLOR_SYSTEM}[E2EE] Đang trao đổi khóa bảo mật Diffie-Hellman với {target_ip}:{target_port}...{COLOR_RESET}")
        dh = DiffieHellman()
        handshake_msg = {
            "type": MSG_TYPE_KEY_EXCHANGE,
            "from": self.name,
            "from_ip": self.ip,
            "from_port": self.port,
            "pub_key": dh.public_key
        }
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((target_ip, target_port))
            if send_json(s, handshake_msg):
                res = recv_json(s, timeout=5)
                if res and res.get("type") == MSG_TYPE_KEY_EXCHANGE_REPLY:
                    peer_pub_key = res.get("pub_key")
                    if peer_pub_key:
                        shared_key = dh.generate_shared_key(peer_pub_key)
                        with self.lock:
                            self.shared_keys[(target_ip, target_port)] = shared_key
                        print(f"{COLOR_SYSTEM}[E2EE] Kênh bảo mật E2EE thiết lập thành công!{COLOR_RESET}")
                        return shared_key
        except Exception as e:
            print(f"{COLOR_ERROR}[E2EE Lỗi] Thất bại khi trao đổi khóa với {target_ip}:{target_port}: {e}{COLOR_RESET}")
        finally:
            s.close()
        return None

    def handle_incoming(self, conn, addr):
        """
        Handles incoming JSON messages from other peers.
        """
        try:
            msg = recv_json(conn, timeout=10)
            if not msg:
                return

            msg_type = msg.get("type")

            # 1. Key Exchange message
            if msg_type == MSG_TYPE_KEY_EXCHANGE:
                sender_name = msg.get("from", "Unknown")
                from_ip = msg.get("from_ip")
                try:
                    from_port = int(msg.get("from_port"))
                except (ValueError, TypeError):
                    return
                peer_pub_key = msg.get("pub_key")

                if from_ip and from_port and peer_pub_key:
                    try:
                        dh_b = DiffieHellman()
                        shared_key = dh_b.generate_shared_key(peer_pub_key)
                        with self.lock:
                            self.shared_keys[(from_ip, from_port)] = shared_key

                        # Reply with own public key
                        reply = {
                            "type": MSG_TYPE_KEY_EXCHANGE_REPLY,
                            "from": self.name,
                            "pub_key": dh_b.public_key
                        }
                        send_json(conn, reply)
                        print(f"\n{COLOR_SYSTEM}[E2EE] Kênh bảo mật E2EE tự động thiết lập với [{sender_name}] ({from_ip}:{from_port})!{COLOR_RESET}")
                        print(">> ", end="", flush=True)
                    except Exception as e:
                        print(f"\n{COLOR_ERROR}[E2EE Lỗi] Thất bại khi trả lời DH: {e}{COLOR_RESET}")
                        print(">> ", end="", flush=True)

            # 2. E2EE Chat Message
            elif msg_type == MSG_TYPE_CHAT:
                sender = msg.get("from", "Unknown")
                try:
                    from_port = int(msg.get("from_port"))
                except (ValueError, TypeError):
                    return
                b64_ciphertext = msg.get("ciphertext")
                b64_iv = msg.get("iv")

                sender_ip = addr[0]
                with self.lock:
                    shared_key = self.shared_keys.get((sender_ip, from_port))

                if shared_key and b64_ciphertext and b64_iv:
                    try:
                        content = decrypt_string(b64_ciphertext, shared_key, b64_iv)
                        print(f"\n{COLOR_CHAT}[1-1 Chat] [{sender}]: {content}{COLOR_RESET}")
                    except Exception as e:
                        print(f"\n{COLOR_ERROR}[E2EE Lỗi] Không thể giải mã tin nhắn từ [{sender}]: {e}{COLOR_RESET}")
                else:
                    print(f"\n{COLOR_ERROR}[E2EE Lỗi] Nhận tin nhắn mã hóa từ [{sender}] nhưng không tìm thấy khóa chung.{COLOR_RESET}")
                print(">> ", end="", flush=True)

            # 3. Plain Broadcast Message
            elif msg_type == MSG_TYPE_BROADCAST:
                sender = msg.get("from", "Unknown")
                content = msg.get("content", "")
                msg_id = msg.get("msg_id")
                ttl = msg.get("ttl", 3)

                with self.lock:
                    if msg_id in self.seen_messages:
                        return
                    self.seen_messages.add(msg_id)

                print(f"\n{COLOR_BROADCAST}[Broadcast] [{sender}]: {content}{COLOR_RESET}")
                print(">> ", end="", flush=True)

                # Forward message to other peers if TTL > 0
                if ttl > 0:
                    with self.lock:
                        peers_to_forward = list(self.peer_list)
                    
                    forward_msg = {
                        "type": MSG_TYPE_BROADCAST,
                        "from": sender,
                        "content": content,
                        "msg_id": msg_id,
                        "ttl": ttl - 1
                    }
                    
                    for p in peers_to_forward:
                        if p["ip"] == addr[0]:
                            continue
                        threading.Thread(
                            target=self.send_direct_message, 
                            args=(p["ip"], p["port"], forward_msg), 
                            daemon=True
                        ).start()

            # 4. E2EE File Message
            elif msg_type == MSG_TYPE_FILE:
                sender = msg.get("from", "Unknown")
                try:
                    from_port = int(msg.get("from_port"))
                except (ValueError, TypeError):
                    return
                filename = msg.get("filename", "unknown_file")
                b64_ciphertext = msg.get("ciphertext")
                b64_iv = msg.get("iv")

                sender_ip = addr[0]
                with self.lock:
                    shared_key = self.shared_keys.get((sender_ip, from_port))

                if shared_key and b64_ciphertext and b64_iv:
                    try:
                        ciphertext = base64.b64decode(b64_ciphertext.encode('utf-8'))
                        iv = base64.b64decode(b64_iv.encode('utf-8'))
                        file_data = decrypt_bytes(ciphertext, shared_key, iv)
                        
                        os.makedirs("received", exist_ok=True)
                        safe_filename = os.path.basename(filename)
                        dest_path = os.path.join("received", safe_filename)
                        with open(dest_path, "wb") as f:
                            f.write(file_data)
                        
                        print(f"\n{COLOR_FILE}[File E2EE] Nhận thành công '{safe_filename}' từ [{sender}]. Lưu tại: {dest_path}{COLOR_RESET}")
                    except Exception as e:
                        print(f"\n{COLOR_ERROR}[E2EE Lỗi] Giải mã file '{filename}' từ [{sender}] thất bại: {e}{COLOR_RESET}")
                else:
                    print(f"\n{COLOR_ERROR}[E2EE Lỗi] Nhận file mã hóa từ [{sender}] nhưng không có khóa bảo mật chung.{COLOR_RESET}")
                print(">> ", end="", flush=True)

        except Exception:
            pass
        finally:
            conn.close()

    def send_direct_message(self, target_ip, target_port, msg_dict):
        """
        Helper function to open a TCP connection, send a JSON object, and close.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((target_ip, int(target_port)))
            send_json(s, msg_dict)
            return True
        except Exception:
            return False
        finally:
            s.close()

    def heartbeat_loop(self):
        """
        Background loop to send periodic heartbeats to the Bootstrap Server.
        """
        while self.running:
            time.sleep(30)
            if self.running:
                self.send_heartbeat()

    def update_peer_list_loop(self):
        """
        Background loop to request updated peer lists from the Bootstrap Server.
        """
        while self.running:
            time.sleep(60)
            if self.running:
                self.get_peer_list()

    def handle_send_command(self, target, content):
        """
        Handles the !send CLI command for 1-1 direct messaging.
        """
        target_ip = None
        target_port = None
        target_name = "Unknown"

        # Resolve peer target
        try:
            idx = int(target) - 1
            with self.lock:
                if 0 <= idx < len(self.peer_list):
                    peer = self.peer_list[idx]
                    target_ip = peer["ip"]
                    target_port = peer["port"]
                    target_name = peer["name"]
                else:
                    print(f"{COLOR_ERROR}[Lỗi] Vị trí Index không hợp lệ. Gõ !peers để xem danh sách.{COLOR_RESET}")
                    return
        except ValueError:
            if ":" in target:
                parts = target.split(":")
                target_ip = parts[0]
                try:
                    target_port = int(parts[1])
                except ValueError:
                    pass
            else:
                print(f"{COLOR_ERROR}[Lỗi] Cú pháp không hợp lệ. Ví dụ: !send 1 Tin nhắn hoặc !send 127.0.0.1:5002 Tin nhắn{COLOR_RESET}")
                return

        if not target_ip or not target_port:
            print(f"{COLOR_ERROR}[Lỗi] Không xác định được địa chỉ Peer đích.{COLOR_RESET}")
            return

        # Ensure E2EE Shared Key exists
        shared_key = self.ensure_shared_key(target_ip, target_port)
        if not shared_key:
            print(f"{COLOR_ERROR}[E2EE Lỗi] Từ chối gửi tin nhắn do không thiết lập được kênh bảo mật E2EE.{COLOR_RESET}")
            return

        # Encrypt the message content
        b64_ciphertext, b64_iv = encrypt_string(content, shared_key)
        chat_msg = {
            "type": MSG_TYPE_CHAT,
            "from": self.name,
            "from_port": self.port,
            "ciphertext": b64_ciphertext,
            "iv": b64_iv,
            "msg_id": str(uuid.uuid4())
        }

        success = self.send_direct_message(target_ip, target_port, chat_msg)
        if success:
            display_name = target_name if target_name != "Unknown" else f"{target_ip}:{target_port}"
            print(f"{COLOR_CHAT}[E2EE Chat 1-1 tới {display_name}]: {content}{COLOR_RESET}")
        else:
            print(f"{COLOR_ERROR}[Lỗi Gửi] Không thể gửi tin nhắn đến {target_name} ({target_ip}:{target_port}). Peer có thể đã offline.{COLOR_RESET}")

    def handle_broadcast_command(self, content):
        """
        Handles the !broadcast CLI command to flood the message to all peers.
        """
        with self.lock:
            active_peers = list(self.peer_list)

        if not active_peers:
            print(f"{COLOR_SYSTEM}[Hệ thống] Không có peer nào online để nhận tin nhắn quảng bá.{COLOR_RESET}")
            return

        msg_id = str(uuid.uuid4())
        broadcast_msg = {
            "type": MSG_TYPE_BROADCAST,
            "from": self.name,
            "content": content,
            "msg_id": msg_id,
            "ttl": 3
        }

        with self.lock:
            self.seen_messages.add(msg_id)

        for p in active_peers:
            threading.Thread(
                target=self.send_direct_message, 
                args=(p["ip"], p["port"], broadcast_msg), 
                daemon=True
            ).start()

        print(f"{COLOR_BROADCAST}[Broadcast] Đã phát tin nhắn tới {len(active_peers)} peers: {content}{COLOR_RESET}")

    def handle_sendfile_command(self, target, filepath):
        """
        Handles the !sendfile CLI command. Reads the local file, encrypts it
        with E2EE, and sends it over TCP.
        """
        if not os.path.exists(filepath):
            print(f"{COLOR_ERROR}[Lỗi] File không tồn tại tại đường dẫn: {filepath}{COLOR_RESET}")
            return

        try:
            file_size = os.path.getsize(filepath)
            if file_size > 2 * 1024 * 1024:
                print(f"{COLOR_ERROR}[Lỗi] Kích thước file lớn hơn 2MB ({file_size / (1024*1024):.2f}MB). Vui lòng gửi file nhỏ hơn.{COLOR_RESET}")
                return
        except Exception as e:
            print(f"{COLOR_ERROR}[Lỗi] Không thể truy cập file: {e}{COLOR_RESET}")
            return

        target_ip = None
        target_port = None
        target_name = "Unknown"

        # Resolve peer target
        try:
            idx = int(target) - 1
            with self.lock:
                if 0 <= idx < len(self.peer_list):
                    peer = self.peer_list[idx]
                    target_ip = peer["ip"]
                    target_port = peer["port"]
                    target_name = peer["name"]
                else:
                    print(f"{COLOR_ERROR}[Lỗi] Vị trí Index không hợp lệ. Gõ !peers để xem danh sách.{COLOR_RESET}")
                    return
        except ValueError:
            if ":" in target:
                parts = target.split(":")
                target_ip = parts[0]
                try:
                    target_port = int(parts[1])
                except ValueError:
                    pass
            else:
                print(f"{COLOR_ERROR}[Lỗi] Cú pháp không hợp lệ. Ví dụ: !sendfile 1 path/to/file hoặc !sendfile 127.0.0.1:5002 path/to/file{COLOR_RESET}")
                return

        if not target_ip or not target_port:
            print(f"{COLOR_ERROR}[Lỗi] Không xác định được địa chỉ Peer đích.{COLOR_RESET}")
            return

        # Ensure E2EE Shared Key exists
        shared_key = self.ensure_shared_key(target_ip, target_port)
        if not shared_key:
            print(f"{COLOR_ERROR}[E2EE Lỗi] Từ chối gửi file do không thiết lập được kênh bảo mật E2EE.{COLOR_RESET}")
            return

        filename = os.path.basename(filepath)
        print(f"{COLOR_SYSTEM}[Hệ thống] Đang mã hóa và chuẩn bị gửi file '{filename}'...{COLOR_RESET}")
        
        try:
            with open(filepath, "rb") as f:
                data_bytes = f.read()
            
            # Encrypt raw bytes with DH key
            ciphertext, iv = encrypt_bytes(data_bytes, shared_key)
            b64_ciphertext = base64.b64encode(ciphertext).decode('utf-8')
            b64_iv = base64.b64encode(iv).decode('utf-8')
        except Exception as e:
            print(f"{COLOR_ERROR}[Lỗi Đọc File] Thao tác đọc file/mã hóa thất bại: {e}{COLOR_RESET}")
            return

        file_msg = {
            "type": MSG_TYPE_FILE,
            "from": self.name,
            "from_port": self.port,
            "filename": filename,
            "ciphertext": b64_ciphertext,
            "iv": b64_iv,
            "msg_id": str(uuid.uuid4())
        }

        success = self.send_direct_message(target_ip, target_port, file_msg)
        if success:
            display_name = target_name if target_name != "Unknown" else f"{target_ip}:{target_port}"
            print(f"{COLOR_FILE}[File E2EE] Gửi thành công file '{filename}' tới {display_name}!{COLOR_RESET}")
        else:
            print(f"{COLOR_ERROR}[Lỗi Gửi File] Không thể kết nối tới Peer {target_name} ({target_ip}:{target_port}) để truyền file.{COLOR_RESET}")

    def print_help(self):
        """
        Prints the helper CLI guide.
        """
        print(f"\n{COLOR_BOLD}{COLOR_SYSTEM}========================= HƯỚNG DẪN CÁC LỆNH ========================={COLOR_RESET}")
        print(f"  {COLOR_BOLD}!peers{COLOR_RESET}                          - Xem danh sách các peer đang hoạt động")
        print(f"  {COLOR_BOLD}!send <index> <tin_nhắn>{COLOR_RESET}        - Gửi tin nhắn E2EE 1-1 cho peer qua số thứ tự")
        print(f"  {COLOR_BOLD}!send <ip>:<port> <tin_nhắn>{COLOR_RESET}     - Gửi tin nhắn E2EE 1-1 cho peer qua IP:Port")
        print(f"  {COLOR_BOLD}!broadcast <tin_nhắn>{COLOR_RESET}           - Phát tin nhắn quảng bá (không mã hóa) toàn mạng")
        print(f"  {COLOR_BOLD}!sendfile <index> <đường_dẫn>{COLOR_RESET}   - Gửi file mã hóa E2EE cho peer qua số thứ tự")
        print(f"  {COLOR_BOLD}!sendfile <ip>:<port> <đường_dẫn>{COLOR_RESET}- Gửi file mã hóa E2EE cho peer qua IP:Port")
        print(f"  {COLOR_BOLD}!leave{COLOR_RESET}                          - Rời khỏi mạng chat và đóng chương trình")
        print(f"  {COLOR_BOLD}!help{COLOR_RESET}                           - Hiển thị bảng hướng dẫn này")
        print(f"{COLOR_BOLD}{COLOR_SYSTEM}======================================================================{COLOR_RESET}")

    def print_peers(self):
        """
        Displays a structured, visually appealing list of online peers.
        """
        with self.lock:
            peers_copy = list(self.peer_list)

        if not peers_copy:
            print(f"{COLOR_SYSTEM}[Hệ thống] Không có peer nào khác đang online tại thời điểm này.{COLOR_RESET}")
            return

        print(f"\n{COLOR_BOLD}{COLOR_SYSTEM}==================== PEERS ONLINE TRONG MẠNG ({len(peers_copy)}) ===================={COLOR_RESET}")
        print(f"  {'Index':<6} | {'Tên Peer':<15} | {'Địa chỉ IP:Port':<22}")
        print(f"  {'-'*6} + {'-'*15} + {'-'*22}")
        for idx, p in enumerate(peers_copy, 1):
            addr_str = f"{p['ip']}:{p['port']}"
            print(f"  [{idx:<4}] | {p['name']:<15} | {addr_str:<22}")
        print(f"{COLOR_BOLD}{COLOR_SYSTEM}======================================================================{COLOR_RESET}")

    def cli_loop(self):
        """
        Main interactive CLI loop reading user input and dispatching commands.
        """
        # Set command prompt
        print(f"\n{COLOR_BOLD}{COLOR_SYSTEM}==================================================")
        print("   HỆ THỐNG CHAT NGANG HÀNG P2P - PEER NODE [E2EE] ")
        print(f"=================================================={COLOR_RESET}")
        print(f"Tên đăng ký  : {COLOR_BOLD}{COLOR_CHAT}{self.name}{COLOR_RESET}")
        print(f"Lắng nghe TCP: {COLOR_BOLD}{COLOR_CHAT}{self.ip}:{self.port}{COLOR_RESET}")
        print(f"Bootstrap    : {COLOR_BOLD}{self.bootstrap_addr[0]}:{self.bootstrap_addr[1]}{COLOR_RESET}")
        print(f"Trạng thái   : {COLOR_BOLD}{COLOR_CHAT}MÃ HÓA ĐẦU CUỐI E2EE ĐÃ KÍCH HOẠT{COLOR_RESET}")
        print(f"Nhập {COLOR_BOLD}!help{COLOR_RESET} để xem các lệnh chat hỗ trợ.")
        print(f"{COLOR_BOLD}{COLOR_SYSTEM}=================================================={COLOR_RESET}\n")

        # Initial peer list fetch
        self.get_peer_list()
        self.print_peers()

        while self.running:
            try:
                line = input(">> ").strip()
                if not line:
                    continue

                if line.startswith("!"):
                    parts = line.split(" ", 2)
                    cmd = parts[0].lower()

                    if cmd == "!help":
                        self.print_help()
                    elif cmd == "!peers":
                        self.get_peer_list()
                        self.print_peers()
                    elif cmd == "!leave":
                        print(f"{COLOR_SYSTEM}[Hệ thống] Đang thông báo rời mạng lên Bootstrap...{COLOR_RESET}")
                        self.running = False
                        break
                    elif cmd == "!send":
                        if len(parts) < 3:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp lệnh sai. Cú pháp: !send <index/ip:port> <tin_nhắn>{COLOR_RESET}")
                            continue
                        target = parts[1]
                        content = parts[2]
                        self.handle_send_command(target, content)
                    elif cmd == "!broadcast":
                        if len(parts) < 2:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp lệnh sai. Cú pháp: !broadcast <tin_nhắn>{COLOR_RESET}")
                            continue
                        content = parts[1] if len(parts) == 2 else line[len("!broadcast "):].strip()
                        self.handle_broadcast_command(content)
                    elif cmd == "!sendfile":
                        if len(parts) < 3:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp lệnh sai. Cú pháp: !sendfile <index/ip:port> <đường_dẫn_file>{COLOR_RESET}")
                            continue
                        target = parts[1]
                        filepath = parts[2]
                        self.handle_sendfile_command(target, filepath)
                    else:
                        print(f"{COLOR_ERROR}[Lỗi] Lệnh không hợp lệ: '{cmd}'. Gõ !help để xem trợ giúp.{COLOR_RESET}")
                else:
                    self.handle_broadcast_command(line)

            except (KeyboardInterrupt, EOFError):
                print(f"\n{COLOR_SYSTEM}[Hệ thống] Nhận được tín hiệu ngắt. Đang thoát...{COLOR_RESET}")
                self.running = False
                break
            except Exception as e:
                print(f"{COLOR_ERROR}[Lỗi CLI] Có lỗi xảy ra trong CLI loop: {e}{COLOR_RESET}")

        # Cleanup and leave
        self.leave_network()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        print(f"{COLOR_SYSTEM}[Hệ thống] Đã tắt client an toàn. Tạm biệt!{COLOR_RESET}")

def main():
    # Setup argument parser
    parser = argparse.ArgumentParser(description="P2P Chat Node")
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="IP address to listen on (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port to listen on")
    parser.add_argument("--name", type=str, help="Name of the peer")
    parser.add_argument("--bootstrap-host", type=str, default="127.0.0.1", help="Bootstrap Server IP (default: 127.0.0.1)")
    parser.add_argument("--bootstrap-port", type=int, default=5555, help="Bootstrap Server Port (default: 5555)")
    
    if sys.platform == "win32":
        os.system("color")

    args = parser.parse_args()

    name = args.name
    if not name:
        name = input("Nhập tên đăng ký của bạn (ví dụ: Alice): ").strip()
        while not name:
            name = input("Tên không được trống. Vui lòng nhập lại: ").strip()

    port = args.port
    if not port:
        port_str = input("Nhập port lắng nghe TCP của bạn (ví dụ: 5001): ").strip()
        while not port_str:
            port_str = input("Port không được trống. Vui lòng nhập lại: ").strip()
        try:
            port = int(port_str)
        except ValueError:
            print("[Lỗi] Port phải là số nguyên hợp lệ.")
            sys.exit(1)

    peer = Peer(
        ip=args.ip,
        port=port,
        name=name,
        bootstrap_host=args.bootstrap_host,
        bootstrap_port=args.bootstrap_port
    )

    print(f"{COLOR_SYSTEM}[Hệ thống] Đang đăng ký với Bootstrap Server tại {peer.bootstrap_addr[0]}:{peer.bootstrap_addr[1]}...{COLOR_RESET}")
    if not peer.register_with_bootstrap():
        print(f"{COLOR_ERROR}[Lỗi] Đăng ký không thành công. Hãy chắc chắn rằng Bootstrap Server đã hoạt động.{COLOR_RESET}")
        sys.exit(1)
    print(f"{COLOR_CHAT}[Hệ thống] Đăng ký thành công!{COLOR_RESET}")

    server_thread = threading.Thread(target=peer.start_server, daemon=True)
    server_thread.start()

    heartbeat_thread = threading.Thread(target=peer.heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    update_thread = threading.Thread(target=peer.update_peer_list_loop, daemon=True)
    update_thread.start()

    peer.cli_loop()

if __name__ == "__main__":
    main()
