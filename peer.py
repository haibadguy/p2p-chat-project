import socket
import threading
import json
import uuid
import time
import sys
import os
import base64
import argparse

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
    MSG_TYPE_KEY_EXCHANGE_REPLY,
    MSG_TYPE_ACK,
    MSG_TYPE_GROUP_CREATE,
    MSG_TYPE_GROUP_JOIN,
    MSG_TYPE_GROUP_LEAVE,
    MSG_TYPE_GROUP_MSG,
    MSG_TYPE_GROUP_LIST,
    MSG_TYPE_GROUP_SYNC,
    MSG_TYPE_PEER_JOINED,
    MSG_TYPE_PEER_LEFT
)
from common.utils import send_json, recv_json, send_reliable
from common.encryption import (
    DiffieHellman,
    encrypt_string,
    decrypt_string,
    encrypt_bytes,
    decrypt_bytes
)

COLOR_RESET = "\033[0m"
COLOR_SYSTEM = "\033[93m"
COLOR_CHAT = "\033[92m"
COLOR_BROADCAST = "\033[96m"
COLOR_FILE = "\033[95m"
COLOR_ERROR = "\033[91m"
COLOR_BOLD = "\033[1m"
COLOR_GROUP = "\033[94m"


class Peer:
    def __init__(self, ip, port, name, bootstrap_host='127.0.0.1', bootstrap_port=5555):
        self.ip = ip
        self.port = port
        self.name = name
        self.bootstrap_addr = (bootstrap_host, bootstrap_port)

        self.peer_list = []
        self.seen_messages = set()
        self.shared_keys = {}
        self.running = True
        self.server_socket = None
        self.lock = threading.Lock()

        # Group chat: {group_name: {"members": [(ip, port, name), ...], "creator": name}}
        self.groups = {}

    # ==================== BOOTSTRAP COMMUNICATION ====================

    def register_with_bootstrap(self):
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

    # ==================== SERVER & NETWORKING ====================

    def start_server(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(10)
        except Exception as e:
            print(f"{COLOR_ERROR}[Lỗi Khởi Động Server] Không thể bind port {self.port}: {e}{COLOR_RESET}")
            self.running = False
            sys.exit(1)

        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_incoming, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def send_direct_message(self, target_ip, target_port, msg_dict):
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

    def send_with_ack(self, target_ip, target_port, msg_dict, max_retries=3):
        """
        Reliable send: transmits msg_dict and waits for ACK.
        Retries with exponential backoff on failure.
        """
        success, _ = send_reliable(target_ip, target_port, msg_dict, max_retries=max_retries)
        return success

    def ensure_shared_key(self, target_ip, target_port):
        target_port = int(target_port)
        with self.lock:
            if (target_ip, target_port) in self.shared_keys:
                return self.shared_keys[(target_ip, target_port)]

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

    # ==================== INCOMING MESSAGE HANDLER ====================

    def _send_ack(self, conn, msg_id):
        """Sends an ACK response back through the same connection."""
        ack = {"type": MSG_TYPE_ACK, "msg_id": msg_id}
        send_json(conn, ack)

    def handle_incoming(self, conn, addr):
        try:
            msg = recv_json(conn, timeout=10)
            if not msg:
                return

            msg_type = msg.get("type")

            if msg_type == MSG_TYPE_KEY_EXCHANGE:
                self._handle_key_exchange(conn, addr, msg)

            elif msg_type == MSG_TYPE_CHAT:
                self._handle_chat(conn, addr, msg)

            elif msg_type == MSG_TYPE_BROADCAST:
                self._handle_broadcast(conn, addr, msg)

            elif msg_type == MSG_TYPE_FILE:
                self._handle_file(conn, addr, msg)

            elif msg_type == MSG_TYPE_GROUP_CREATE:
                self._handle_group_create(conn, addr, msg)

            elif msg_type == MSG_TYPE_GROUP_JOIN:
                self._handle_group_join(conn, addr, msg)

            elif msg_type == MSG_TYPE_GROUP_LEAVE:
                self._handle_group_leave(conn, addr, msg)

            elif msg_type == MSG_TYPE_GROUP_MSG:
                self._handle_group_msg(conn, addr, msg)

            elif msg_type == MSG_TYPE_GROUP_SYNC:
                self._handle_group_sync(conn, addr, msg)

            elif msg_type == MSG_TYPE_PEER_JOINED:
                name = msg.get("peer_name", "Unknown")
                print(f"\n{COLOR_SYSTEM}[Mạng] Peer [{name}] vừa tham gia mạng.{COLOR_RESET}")
                print(">> ", end="", flush=True)
                self.get_peer_list()

            elif msg_type == MSG_TYPE_PEER_LEFT:
                name = msg.get("peer_name", "Unknown")
                print(f"\n{COLOR_SYSTEM}[Mạng] Peer [{name}] vừa rời khỏi mạng.{COLOR_RESET}")
                print(">> ", end="", flush=True)
                self.get_peer_list()

        except Exception:
            pass
        finally:
            conn.close()

    def _handle_key_exchange(self, conn, addr, msg):
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

    def _handle_chat(self, conn, addr, msg):
        sender = msg.get("from", "Unknown")
        msg_id = msg.get("msg_id")
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
                # Send ACK before displaying
                if msg_id:
                    self._send_ack(conn, msg_id)
                print(f"\n{COLOR_CHAT}[1-1 Chat] [{sender}]: {content}{COLOR_RESET}")
            except Exception as e:
                print(f"\n{COLOR_ERROR}[E2EE Lỗi] Không thể giải mã tin nhắn từ [{sender}]: {e}{COLOR_RESET}")
        else:
            print(f"\n{COLOR_ERROR}[E2EE Lỗi] Nhận tin nhắn mã hóa từ [{sender}] nhưng không tìm thấy khóa chung.{COLOR_RESET}")
        print(">> ", end="", flush=True)

    def _handle_broadcast(self, conn, addr, msg):
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

    def _handle_file(self, conn, addr, msg):
        sender = msg.get("from", "Unknown")
        msg_id = msg.get("msg_id")
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

                if msg_id:
                    self._send_ack(conn, msg_id)
                print(f"\n{COLOR_FILE}[File E2EE] Nhận thành công '{safe_filename}' từ [{sender}]. Lưu tại: {dest_path}{COLOR_RESET}")
            except Exception as e:
                print(f"\n{COLOR_ERROR}[E2EE Lỗi] Giải mã file '{filename}' từ [{sender}] thất bại: {e}{COLOR_RESET}")
        else:
            print(f"\n{COLOR_ERROR}[E2EE Lỗi] Nhận file mã hóa từ [{sender}] nhưng không có khóa bảo mật chung.{COLOR_RESET}")
        print(">> ", end="", flush=True)

    # ==================== GROUP CHAT HANDLERS ====================

    def _handle_group_create(self, conn, addr, msg):
        group_name = msg.get("group_name")
        creator = msg.get("from", "Unknown")
        creator_ip = msg.get("from_ip")
        creator_port = msg.get("from_port")
        msg_id = msg.get("msg_id")

        with self.lock:
            if group_name not in self.groups:
                self.groups[group_name] = {
                    "members": [(creator_ip, int(creator_port), creator)],
                    "creator": creator
                }

        if msg_id:
            self._send_ack(conn, msg_id)
        print(f"\n{COLOR_GROUP}[Nhóm] Nhóm '{group_name}' được tạo bởi [{creator}].{COLOR_RESET}")
        print(">> ", end="", flush=True)

    def _handle_group_join(self, conn, addr, msg):
        group_name = msg.get("group_name")
        joiner = msg.get("from", "Unknown")
        joiner_ip = msg.get("from_ip")
        joiner_port = int(msg.get("from_port", 0))
        msg_id = msg.get("msg_id")

        with self.lock:
            if group_name in self.groups:
                members = self.groups[group_name]["members"]
                if not any(m[0] == joiner_ip and m[1] == joiner_port for m in members):
                    members.append((joiner_ip, joiner_port, joiner))

        if msg_id:
            self._send_ack(conn, msg_id)
        print(f"\n{COLOR_GROUP}[Nhóm] [{joiner}] đã tham gia nhóm '{group_name}'.{COLOR_RESET}")
        print(">> ", end="", flush=True)

    def _handle_group_leave(self, conn, addr, msg):
        group_name = msg.get("group_name")
        leaver = msg.get("from", "Unknown")
        leaver_ip = msg.get("from_ip")
        leaver_port = int(msg.get("from_port", 0))
        msg_id = msg.get("msg_id")

        with self.lock:
            if group_name in self.groups:
                self.groups[group_name]["members"] = [
                    m for m in self.groups[group_name]["members"]
                    if not (m[0] == leaver_ip and m[1] == leaver_port)
                ]

        if msg_id:
            self._send_ack(conn, msg_id)
        print(f"\n{COLOR_GROUP}[Nhóm] [{leaver}] đã rời nhóm '{group_name}'.{COLOR_RESET}")
        print(">> ", end="", flush=True)

    def _handle_group_msg(self, conn, addr, msg):
        group_name = msg.get("group_name")
        sender = msg.get("from", "Unknown")
        content = msg.get("content", "")
        msg_id = msg.get("msg_id")

        with self.lock:
            if msg_id in self.seen_messages:
                return
            self.seen_messages.add(msg_id)

        if msg_id:
            self._send_ack(conn, msg_id)

        print(f"\n{COLOR_GROUP}[Nhóm:{group_name}] [{sender}]: {content}{COLOR_RESET}")
        print(">> ", end="", flush=True)

        # Forward to other group members
        with self.lock:
            group = self.groups.get(group_name)
            if not group:
                return
            members = list(group["members"])

        forward_msg = dict(msg)
        for m_ip, m_port, m_name in members:
            if m_ip == self.ip and m_port == self.port:
                continue
            if m_ip == addr[0]:
                continue
            threading.Thread(
                target=self.send_direct_message,
                args=(m_ip, m_port, forward_msg),
                daemon=True
            ).start()

    def _handle_group_sync(self, conn, addr, msg):
        """Receives group membership data from the creator."""
        group_name = msg.get("group_name")
        members = msg.get("members", [])
        creator = msg.get("creator", "Unknown")
        msg_id = msg.get("msg_id")

        with self.lock:
            self.groups[group_name] = {
                "members": [(m[0], int(m[1]), m[2]) for m in members],
                "creator": creator
            }

        if msg_id:
            self._send_ack(conn, msg_id)

    # ==================== BACKGROUND LOOPS ====================

    def heartbeat_loop(self):
        while self.running:
            time.sleep(15)
            if self.running:
                self.send_heartbeat()

    def update_peer_list_loop(self):
        time.sleep(2)
        while self.running:
            self.get_peer_list()
            time.sleep(10)

    # ==================== PEER RESOLUTION HELPER ====================

    def _resolve_peer(self, target):
        """Resolves a CLI target (index or ip:port) to (ip, port, name)."""
        try:
            idx = int(target) - 1
            with self.lock:
                if 0 <= idx < len(self.peer_list):
                    p = self.peer_list[idx]
                    return p["ip"], p["port"], p["name"]
                else:
                    print(f"{COLOR_ERROR}[Lỗi] Index không hợp lệ. Gõ !peers để xem danh sách.{COLOR_RESET}")
                    return None, None, None
        except ValueError:
            if ":" in target:
                parts = target.split(":")
                try:
                    return parts[0], int(parts[1]), "Unknown"
                except ValueError:
                    pass
            print(f"{COLOR_ERROR}[Lỗi] Cú pháp không hợp lệ.{COLOR_RESET}")
            return None, None, None

    # ==================== CLI COMMAND HANDLERS ====================

    def handle_send_command(self, target, content):
        target_ip, target_port, target_name = self._resolve_peer(target)
        if not target_ip:
            return

        shared_key = self.ensure_shared_key(target_ip, target_port)
        if not shared_key:
            print(f"{COLOR_ERROR}[E2EE Lỗi] Từ chối gửi tin nhắn do không thiết lập được kênh bảo mật E2EE.{COLOR_RESET}")
            return

        b64_ciphertext, b64_iv = encrypt_string(content, shared_key)
        chat_msg = {
            "type": MSG_TYPE_CHAT,
            "from": self.name,
            "from_port": self.port,
            "ciphertext": b64_ciphertext,
            "iv": b64_iv,
            "msg_id": str(uuid.uuid4())
        }

        success = self.send_with_ack(target_ip, target_port, chat_msg)
        display_name = target_name if target_name != "Unknown" else f"{target_ip}:{target_port}"
        if success:
            print(f"{COLOR_CHAT}[E2EE Chat 1-1 tới {display_name}]: {content} {COLOR_SYSTEM}(ACK){COLOR_RESET}")
        else:
            print(f"{COLOR_ERROR}[Lỗi Gửi] Không nhận được ACK từ {display_name} sau 3 lần thử. Peer có thể đã offline.{COLOR_RESET}")

    def handle_broadcast_command(self, content):
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

        target_ip, target_port, target_name = self._resolve_peer(target)
        if not target_ip:
            return

        shared_key = self.ensure_shared_key(target_ip, target_port)
        if not shared_key:
            print(f"{COLOR_ERROR}[E2EE Lỗi] Từ chối gửi file do không thiết lập được kênh bảo mật E2EE.{COLOR_RESET}")
            return

        filename = os.path.basename(filepath)
        print(f"{COLOR_SYSTEM}[Hệ thống] Đang mã hóa và chuẩn bị gửi file '{filename}'...{COLOR_RESET}")

        try:
            with open(filepath, "rb") as f:
                data_bytes = f.read()
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

        success = self.send_with_ack(target_ip, target_port, file_msg)
        display_name = target_name if target_name != "Unknown" else f"{target_ip}:{target_port}"
        if success:
            print(f"{COLOR_FILE}[File E2EE] Gửi thành công file '{filename}' tới {display_name}! {COLOR_SYSTEM}(ACK){COLOR_RESET}")
        else:
            print(f"{COLOR_ERROR}[Lỗi Gửi File] Không nhận được ACK từ {display_name} sau 3 lần thử.{COLOR_RESET}")

    # ==================== GROUP CHAT CLI COMMANDS ====================

    def handle_group_create(self, group_name):
        with self.lock:
            if group_name in self.groups:
                print(f"{COLOR_ERROR}[Lỗi] Nhóm '{group_name}' đã tồn tại.{COLOR_RESET}")
                return
            self.groups[group_name] = {
                "members": [(self.ip, self.port, self.name)],
                "creator": self.name
            }
        print(f"{COLOR_GROUP}[Nhóm] Đã tạo nhóm '{group_name}' thành công. Bạn là thành viên đầu tiên.{COLOR_RESET}")

    def _sync_group_to_member(self, target_ip, target_port, group_name):
        """Sends full group membership info to a peer."""
        with self.lock:
            group = self.groups.get(group_name)
            if not group:
                return
            members_list = [(m[0], m[1], m[2]) for m in group["members"]]
            creator = group["creator"]

        sync_msg = {
            "type": MSG_TYPE_GROUP_SYNC,
            "group_name": group_name,
            "members": members_list,
            "creator": creator,
            "msg_id": str(uuid.uuid4())
        }
        self.send_direct_message(target_ip, int(target_port), sync_msg)

    def handle_group_add(self, group_name, target):
        """Adds a peer to a group and notifies all members."""
        target_ip, target_port, target_name = self._resolve_peer(target)
        if not target_ip:
            return

        with self.lock:
            if group_name not in self.groups:
                print(f"{COLOR_ERROR}[Lỗi] Nhóm '{group_name}' không tồn tại. Tạo nhóm trước bằng !gcreate.{COLOR_RESET}")
                return
            members = self.groups[group_name]["members"]
            if any(m[0] == target_ip and m[1] == int(target_port) for m in members):
                print(f"{COLOR_ERROR}[Lỗi] Peer đã là thành viên nhóm '{group_name}'.{COLOR_RESET}")
                return
            members.append((target_ip, int(target_port), target_name))

        # Notify the new member
        join_msg = {
            "type": MSG_TYPE_GROUP_JOIN,
            "group_name": group_name,
            "from": self.name,
            "from_ip": self.ip,
            "from_port": self.port,
            "msg_id": str(uuid.uuid4())
        }

        # Notify all existing members about the new peer
        with self.lock:
            all_members = list(self.groups[group_name]["members"])

        for m_ip, m_port, m_name in all_members:
            if m_ip == self.ip and m_port == self.port:
                continue
            self.send_direct_message(m_ip, m_port, join_msg)
            self._sync_group_to_member(m_ip, m_port, group_name)

        print(f"{COLOR_GROUP}[Nhóm] Đã thêm [{target_name}] vào nhóm '{group_name}'.{COLOR_RESET}")

    def handle_group_send(self, group_name, content):
        with self.lock:
            group = self.groups.get(group_name)
            if not group:
                print(f"{COLOR_ERROR}[Lỗi] Nhóm '{group_name}' không tồn tại.{COLOR_RESET}")
                return
            members = list(group["members"])

        msg_id = str(uuid.uuid4())
        with self.lock:
            self.seen_messages.add(msg_id)

        group_msg = {
            "type": MSG_TYPE_GROUP_MSG,
            "group_name": group_name,
            "from": self.name,
            "content": content,
            "msg_id": msg_id
        }

        sent_count = 0
        for m_ip, m_port, m_name in members:
            if m_ip == self.ip and m_port == self.port:
                continue
            success = self.send_direct_message(m_ip, m_port, group_msg)
            if success:
                sent_count += 1

        print(f"{COLOR_GROUP}[Nhóm:{group_name}] Đã gửi tới {sent_count}/{len(members)-1} thành viên: {content}{COLOR_RESET}")

    def handle_group_leave_cmd(self, group_name):
        with self.lock:
            group = self.groups.get(group_name)
            if not group:
                print(f"{COLOR_ERROR}[Lỗi] Bạn không ở trong nhóm '{group_name}'.{COLOR_RESET}")
                return
            members = list(group["members"])
            del self.groups[group_name]

        leave_msg = {
            "type": MSG_TYPE_GROUP_LEAVE,
            "group_name": group_name,
            "from": self.name,
            "from_ip": self.ip,
            "from_port": self.port,
            "msg_id": str(uuid.uuid4())
        }

        for m_ip, m_port, m_name in members:
            if m_ip == self.ip and m_port == self.port:
                continue
            self.send_direct_message(m_ip, m_port, leave_msg)

        print(f"{COLOR_GROUP}[Nhóm] Đã rời khỏi nhóm '{group_name}'.{COLOR_RESET}")

    def handle_group_list(self):
        with self.lock:
            groups_copy = dict(self.groups)

        if not groups_copy:
            print(f"{COLOR_SYSTEM}[Hệ thống] Bạn chưa tham gia nhóm nào.{COLOR_RESET}")
            return

        print(f"\n{COLOR_BOLD}{COLOR_GROUP}==================== DANH SÁCH NHÓM ===================={COLOR_RESET}")
        for gname, gdata in groups_copy.items():
            member_names = [m[2] for m in gdata["members"]]
            print(f"  {COLOR_BOLD}{gname}{COLOR_RESET} ({len(member_names)} thành viên): {', '.join(member_names)}")
        print(f"{COLOR_BOLD}{COLOR_GROUP}========================================================{COLOR_RESET}")

    # ==================== CLI DISPLAY ====================

    def print_help(self):
        print(f"\n{COLOR_BOLD}{COLOR_SYSTEM}========================= HƯỚNG DẪN CÁC LỆNH ========================={COLOR_RESET}")
        print(f"  {COLOR_BOLD}--- Tin nhắn ---{COLOR_RESET}")
        print(f"  {COLOR_BOLD}!peers{COLOR_RESET}                          - Xem danh sách các peer đang hoạt động")
        print(f"  {COLOR_BOLD}!send <index> <tin_nhắn>{COLOR_RESET}        - Gửi tin nhắn E2EE 1-1 (có ACK)")
        print(f"  {COLOR_BOLD}!broadcast <tin_nhắn>{COLOR_RESET}           - Phát tin nhắn quảng bá toàn mạng")
        print(f"  {COLOR_BOLD}!sendfile <index> <đường_dẫn>{COLOR_RESET}   - Gửi file mã hóa E2EE (có ACK)")
        print(f"  {COLOR_BOLD}--- Nhóm chat ---{COLOR_RESET}")
        print(f"  {COLOR_BOLD}!gcreate <tên_nhóm>{COLOR_RESET}             - Tạo nhóm chat mới")
        print(f"  {COLOR_BOLD}!gadd <tên_nhóm> <index>{COLOR_RESET}        - Thêm peer vào nhóm")
        print(f"  {COLOR_BOLD}!gsend <tên_nhóm> <tin_nhắn>{COLOR_RESET}    - Gửi tin nhắn vào nhóm")
        print(f"  {COLOR_BOLD}!gleave <tên_nhóm>{COLOR_RESET}              - Rời khỏi nhóm")
        print(f"  {COLOR_BOLD}!glist{COLOR_RESET}                          - Xem danh sách các nhóm đang tham gia")
        print(f"  {COLOR_BOLD}--- Hệ thống ---{COLOR_RESET}")
        print(f"  {COLOR_BOLD}!leave{COLOR_RESET}                          - Rời khỏi mạng chat")
        print(f"  {COLOR_BOLD}!help{COLOR_RESET}                           - Hiển thị bảng hướng dẫn này")
        print(f"{COLOR_BOLD}{COLOR_SYSTEM}======================================================================{COLOR_RESET}")

    def print_peers(self):
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

    # ==================== MAIN CLI LOOP ====================

    def cli_loop(self):
        print(f"\n{COLOR_BOLD}{COLOR_SYSTEM}==================================================")
        print("   HỆ THỐNG CHAT NGANG HÀNG P2P - PEER NODE [E2EE] ")
        print(f"=================================================={COLOR_RESET}")
        print(f"Tên đăng ký  : {COLOR_BOLD}{COLOR_CHAT}{self.name}{COLOR_RESET}")
        print(f"Lắng nghe TCP: {COLOR_BOLD}{COLOR_CHAT}{self.ip}:{self.port}{COLOR_RESET}")
        print(f"Bootstrap    : {COLOR_BOLD}{self.bootstrap_addr[0]}:{self.bootstrap_addr[1]}{COLOR_RESET}")
        print(f"Trạng thái   : {COLOR_BOLD}{COLOR_CHAT}MÃ HÓA ĐẦU CUỐI E2EE ĐÃ KÍCH HOẠT{COLOR_RESET}")
        print(f"Nhập {COLOR_BOLD}!help{COLOR_RESET} để xem các lệnh chat hỗ trợ.")
        print(f"{COLOR_BOLD}{COLOR_SYSTEM}=================================================={COLOR_RESET}\n")

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
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !send <index/ip:port> <tin_nhắn>{COLOR_RESET}")
                            continue
                        self.handle_send_command(parts[1], parts[2])
                    elif cmd == "!broadcast":
                        if len(parts) < 2:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !broadcast <tin_nhắn>{COLOR_RESET}")
                            continue
                        content = parts[1] if len(parts) == 2 else line[len("!broadcast "):].strip()
                        self.handle_broadcast_command(content)
                    elif cmd == "!sendfile":
                        if len(parts) < 3:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !sendfile <index/ip:port> <đường_dẫn_file>{COLOR_RESET}")
                            continue
                        self.handle_sendfile_command(parts[1], parts[2])
                    elif cmd == "!gcreate":
                        if len(parts) < 2:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !gcreate <tên_nhóm>{COLOR_RESET}")
                            continue
                        self.handle_group_create(parts[1])
                    elif cmd == "!gadd":
                        if len(parts) < 3:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !gadd <tên_nhóm> <index>{COLOR_RESET}")
                            continue
                        self.handle_group_add(parts[1], parts[2])
                    elif cmd == "!gsend":
                        if len(parts) < 3:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !gsend <tên_nhóm> <tin_nhắn>{COLOR_RESET}")
                            continue
                        self.handle_group_send(parts[1], parts[2])
                    elif cmd == "!gleave":
                        if len(parts) < 2:
                            print(f"{COLOR_ERROR}[Lỗi] Cú pháp: !gleave <tên_nhóm>{COLOR_RESET}")
                            continue
                        self.handle_group_leave_cmd(parts[1])
                    elif cmd == "!glist":
                        self.handle_group_list()
                    else:
                        print(f"{COLOR_ERROR}[Lỗi] Lệnh không hợp lệ: '{cmd}'. Gõ !help để xem trợ giúp.{COLOR_RESET}")
                else:
                    self.handle_broadcast_command(line)

            except (KeyboardInterrupt, EOFError):
                print(f"\n{COLOR_SYSTEM}[Hệ thống] Nhận được tín hiệu ngắt. Đang thoát...{COLOR_RESET}")
                self.running = False
                break
            except Exception as e:
                print(f"{COLOR_ERROR}[Lỗi CLI] Có lỗi xảy ra: {e}{COLOR_RESET}")

        self.leave_network()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        print(f"{COLOR_SYSTEM}[Hệ thống] Đã tắt client an toàn. Tạm biệt!{COLOR_RESET}")


def main():
    parser = argparse.ArgumentParser(description="P2P Chat Node")
    parser.add_argument("--ip", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--name", type=str)
    parser.add_argument("--bootstrap-host", type=str, default="127.0.0.1")
    parser.add_argument("--bootstrap-port", type=int, default=5555)

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
