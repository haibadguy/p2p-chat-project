import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
import socket
import threading
import queue
import json
import uuid
import time
import os
import base64
import sys

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

# Modern Dark Mode Palette (Catppuccin Mocha Inspired)
BG_MAIN = "#1E1E2E"       # Deep dark purple-gray
BG_SIDEBAR = "#181825"    # Darker sidebar
BG_CHAT = "#11111B"       # Pure dark text area
BG_INPUT = "#313244"      # Lighter field background
TEXT_MAIN = "#CDD6F4"     # Light pastel white
TEXT_MUTED = "#A6ADC8"    # Gray text
ACCENT_PURPLE = "#CBA6F7" # Vibrant pastel purple
ACCENT_GREEN = "#A6E3A1"  # Neon sage green
ACCENT_CYAN = "#89DCEB"   # Sky blue
ACCENT_MAGENTA = "#F5C2E7"# Hot pink
ACCENT_RED = "#F38BA8"    # Soft red

class PeerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P Encrypted Messenger")
        self.root.geometry("900x600")
        self.root.configure(bg=BG_MAIN)
        
        # P2P Network states
        self.ip = "127.0.0.1"
        self.port = 5001
        self.name = ""
        self.bootstrap_host = "127.0.0.1"
        self.bootstrap_port = 5555
        
        self.peer_list = []
        self.seen_messages = set()
        self.shared_keys = {}
        self.running = False
        self.server_socket = None
        self.lock = threading.Lock()
        
        # Thread-safe GUI Update Queue
        self.gui_queue = queue.Queue()
        
        # Configure custom TTK styling
        self.setup_styles()
        
        # Build Registration Screen first
        self.build_login_screen()

        # Start periodic GUI queue checker (runs in Main Thread)
        self.root.after(100, self.process_queue)

    def setup_styles(self):
        """
        Configures the custom dark theme styles for TTK widgets.
        """
        style = ttk.Style()
        style.theme_use("clam")
        
        # Frame styles
        style.configure("TFrame", background=BG_MAIN)
        style.configure("Sidebar.TFrame", background=BG_SIDEBAR)
        
        # Label styles
        style.configure("TLabel", background=BG_MAIN, foreground=TEXT_MAIN, font=("Segoe UI", 10))
        style.configure("Header.TLabel", background=BG_MAIN, foreground=ACCENT_PURPLE, font=("Segoe UI", 16, "bold"))
        style.configure("SidebarHeader.TLabel", background=BG_SIDEBAR, foreground=TEXT_MAIN, font=("Segoe UI", 11, "bold"))
        
        # Entry styling
        style.configure("TEntry", fieldbackground=BG_INPUT, foreground=TEXT_MAIN, borderwidth=1, relief="flat")
        
        # Custom flat button styles
        style.configure("TButton", font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat", padding=6)
        style.map("TButton",
                  background=[("active", ACCENT_PURPLE), ("!disabled", BG_INPUT)],
                  foreground=[("active", BG_MAIN), ("!disabled", TEXT_MAIN)])
                  
        style.configure("Primary.TButton", background=ACCENT_PURPLE, foreground=BG_MAIN)
        style.map("Primary.TButton", background=[("active", "#B4BEFE")])

        style.configure("Danger.TButton", background=ACCENT_RED, foreground=BG_MAIN)
        style.map("Danger.TButton", background=[("active", "#EBA0B0")])

        # Treeview (Peer List) styling
        style.configure("Treeview", 
                        background=BG_SIDEBAR, 
                        fieldbackground=BG_SIDEBAR, 
                        foreground=TEXT_MAIN,
                        font=("Segoe UI", 10),
                        rowheight=26,
                        borderwidth=0)
        style.configure("Treeview.Heading", 
                        background=BG_INPUT, 
                        foreground=TEXT_MAIN, 
                        font=("Segoe UI", 10, "bold"),
                        relief="flat")
        style.map("Treeview", background=[("selected", BG_INPUT)])

    def build_login_screen(self):
        """
        Builds the initial connection gateway / register window.
        """
        self.login_frame = ttk.Frame(self.root, padding=30)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")
        
        # Header Logo
        header = ttk.Label(self.login_frame, text="P2P ENCRYPTED MESSENGER", style="Header.TLabel")
        header.grid(row=0, column=0, columnspan=2, pady=(0, 20), sticky="center")
        
        # Name Entry
        ttk.Label(self.login_frame, text="Tên hiển thị:").grid(row=1, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar(value="Alice")
        self.name_entry = ttk.Entry(self.login_frame, textvariable=self.name_var, width=25)
        self.name_entry.grid(row=1, column=1, pady=5)
        
        # Listening Port Entry
        ttk.Label(self.login_frame, text="Cổng lắng nghe (Port):").grid(row=2, column=0, sticky="w", pady=5)
        self.port_var = tk.StringVar(value="5001")
        self.port_entry = ttk.Entry(self.login_frame, textvariable=self.port_var, width=25)
        self.port_entry.grid(row=2, column=1, pady=5)

        # Bootstrap IP Entry
        ttk.Label(self.login_frame, text="IP Bootstrap Server:").grid(row=3, column=0, sticky="w", pady=5)
        self.boot_host_var = tk.StringVar(value="127.0.0.1")
        self.boot_host_entry = ttk.Entry(self.login_frame, textvariable=self.boot_host_var, width=25)
        self.boot_host_entry.grid(row=3, column=1, pady=5)

        # Bootstrap Port Entry
        ttk.Label(self.login_frame, text="Cổng Bootstrap:").grid(row=4, column=0, sticky="w", pady=5)
        self.boot_port_var = tk.StringVar(value="5555")
        self.boot_port_entry = ttk.Entry(self.login_frame, textvariable=self.boot_port_var, width=25)
        self.boot_port_entry.grid(row=4, column=1, pady=5)
        
        # Join Network Button
        join_btn = ttk.Button(self.login_frame, text="Kết nối vào mạng", style="Primary.TButton", command=self.handle_join)
        join_btn.grid(row=5, column=0, columnspan=2, pady=(20, 0), sticky="we")

    def handle_join(self):
        """
        Processes connection inputs and registers with the Bootstrap Server.
        """
        self.name = self.name_var.get().strip()
        boot_host = self.boot_host_var.get().strip()
        
        if not self.name:
            messagebox.showerror("Lỗi", "Tên hiển thị không được rỗng!")
            return
            
        try:
            self.port = int(self.port_var.get().strip())
            self.bootstrap_port = int(self.boot_port_var.get().strip())
        except ValueError:
            messagebox.showerror("Lỗi", "Cổng (Port) phải là số nguyên hợp lệ!")
            return
            
        self.bootstrap_host = boot_host
        
        # Attempt registration in a separate connection to prevent UI lockup
        success = self.register_with_bootstrap()
        if not success:
            messagebox.showerror("Lỗi kết nối", f"Không thể đăng ký với Bootstrap Server tại {self.bootstrap_host}:{self.bootstrap_port}.\nHãy kiểm tra xem Server đã chạy chưa.")
            return
            
        # Start server socket background thread
        self.running = True
        self.server_thread = threading.Thread(target=self.start_server, daemon=True)
        self.server_thread.start()
        
        # Start daemon loop threads
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        self.update_thread = threading.Thread(target=self.update_peer_list_loop, daemon=True)
        self.update_thread.start()
        
        # Switch screen
        self.login_frame.destroy()
        self.build_main_screen()
        
        # Add welcome system log
        self.gui_queue.put(("log", f"[Hệ thống] Đăng ký thành công! Chào mừng {self.name} đến với mạng chat E2EE.", ACCENT_GREEN))
        self.gui_queue.put(("update_peers",))

    def build_main_screen(self):
        """
        Builds the split-screen Chat Dashboard UI.
        """
        # Outer grid configuration
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        self.main_container = ttk.Frame(self.root)
        self.main_container.grid(row=0, column=0, sticky="nsew")
        self.main_container.columnconfigure(1, weight=1)
        self.main_container.rowconfigure(0, weight=1)
        
        # 1. LEFT SIDEBAR PANEL (Width: 280)
        sidebar = ttk.Frame(self.main_container, style="Sidebar.TFrame", padding=15, width=280)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)
        
        # Current User Info
        info_label = ttk.Label(
            sidebar, 
            text=f"👤 {self.name}\n📍 Port: {self.port}", 
            style="SidebarHeader.TLabel", 
            justify="left", 
            background=BG_SIDEBAR
        )
        info_label.grid(row=0, column=0, sticky="w", pady=(0, 15))
        
        # Peer registry title
        peers_title = ttk.Label(sidebar, text="PEERS ĐANG ONLINE", style="SidebarHeader.TLabel", background=BG_SIDEBAR, foreground=ACCENT_PURPLE)
        peers_title.grid(row=1, column=0, sticky="w", pady=(0, 5))
        
        # Treeview list showing peers
        self.peer_tree = ttk.Treeview(sidebar, columns=("name", "addr"), show="headings", style="Treeview")
        self.peer_tree.heading("name", text="Tên Peer")
        self.peer_tree.heading("addr", text="Địa chỉ IP:Port")
        self.peer_tree.column("name", width=100, anchor="w")
        self.peer_tree.column("addr", width=150, anchor="center")
        self.peer_tree.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        
        # Sidebar control buttons
        refresh_btn = ttk.Button(sidebar, text="Làm mới danh sách", command=self.handle_refresh)
        refresh_btn.grid(row=3, column=0, sticky="we", pady=5)
        
        leave_btn = ttk.Button(sidebar, text="Rời khỏi mạng", style="Danger.TButton", command=self.handle_leave_gui)
        leave_btn.grid(row=4, column=0, sticky="we", pady=5)

        # 2. RIGHT CHAT PANEL
        chat_panel = ttk.Frame(self.main_container, padding=15)
        chat_panel.grid(row=0, column=1, sticky="nsew")
        chat_panel.columnconfigure(0, weight=1)
        chat_panel.rowconfigure(1, weight=1)
        
        # Dashboard header details
        self.chat_header = ttk.Label(
            chat_panel, 
            text="Chọn một peer trong danh sách bên trái để gửi tin nhắn E2EE", 
            font=("Segoe UI", 11, "bold"),
            foreground=ACCENT_CYAN
        )
        self.chat_header.grid(row=0, column=0, sticky="w", pady=(0, 10))
        
        # Scrolled Text Box for Chat History
        self.chat_box = ScrolledText(
            chat_panel, 
            bg=BG_CHAT, 
            fg=TEXT_MAIN, 
            insertbackground=TEXT_MAIN,
            font=("Segoe UI", 10),
            state="disabled",
            wrap="word",
            borderwidth=0
        )
        self.chat_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 15))
        
        # Setup tags for colored styling in history
        self.chat_box.tag_config("system", foreground=COLOR_SYSTEM)
        self.chat_box.tag_config("chat_in", foreground=COLOR_CHAT, font=("Segoe UI", 10, "bold"))
        self.chat_box.tag_config("chat_out", foreground="#C1FFC1")
        self.chat_box.tag_config("broadcast", foreground=COLOR_BROADCAST, font=("Segoe UI", 10, "italic"))
        self.chat_box.tag_config("file", foreground=COLOR_FILE, font=("Segoe UI", 10, "bold"))
        self.chat_box.tag_config("error", foreground=COLOR_ERROR, font=("Segoe UI", 10, "bold"))
        
        # 3. BOTTOM MESSAGE INPUT GATE
        input_frame = ttk.Frame(chat_panel)
        input_frame.grid(row=2, column=0, sticky="we")
        input_frame.columnconfigure(0, weight=1)
        
        # Entry Field
        self.msg_entry = ttk.Entry(input_frame, font=("Segoe UI", 11))
        self.msg_entry.grid(row=0, column=0, sticky="we", ipady=4, padx=(0, 10))
        self.msg_entry.bind("<Return>", lambda e: self.send_chat())
        
        # Button controls
        btn_frame = ttk.Frame(chat_panel)
        btn_frame.grid(row=3, column=0, sticky="we", pady=(8, 0))
        
        send_btn = ttk.Button(btn_frame, text="Gửi E2EE 1-1", style="Primary.TButton", command=self.send_chat)
        send_btn.grid(row=0, column=0, padx=(0, 10))
        
        broadcast_btn = ttk.Button(btn_frame, text="Gửi Broadcast", command=self.send_broadcast)
        broadcast_btn.grid(row=0, column=1, padx=(0, 10))
        
        sendfile_btn = ttk.Button(btn_frame, text="Gửi File E2EE", command=self.send_file)
        sendfile_btn.grid(row=0, column=2)

        # Monitor peer tree selection changes
        self.peer_tree.bind("<<TreeviewSelect>>", self.on_peer_select)

    def on_peer_select(self, event):
        """
        Updates the chat header showing selected peer.
        """
        selected = self.peer_tree.selection()
        if selected:
            item = self.peer_tree.item(selected[0])
            name, addr = item["values"]
            self.chat_header.config(text=f"🔒 Kênh E2EE an toàn với: {name} ({addr})", foreground=ACCENT_GREEN)
        else:
            self.chat_header.config(text="Chọn một peer trong danh sách bên trái để gửi tin nhắn E2EE", foreground=ACCENT_CYAN)

    # ==================== P2P NETWORK LOGIC ====================

    def register_with_bootstrap(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            register_msg = {
                "type": MSG_TYPE_REGISTER,
                "ip": self.ip,
                "port": self.port,
                "name": self.name
            }
            if send_json(s, register_msg):
                res = recv_json(s, timeout=4)
                if res and res.get("status") == "success":
                    return True
            return False
        except Exception:
            return False
        finally:
            s.close()

    def get_peer_list(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            req = {
                "type": MSG_TYPE_GET_PEERS,
                "ip": self.ip,
                "port": self.port
            }
            if send_json(s, req):
                res = recv_json(s, timeout=4)
                if res and res.get("type") == MSG_TYPE_PEER_LIST:
                    new_peers = res.get("peers", [])
                    with self.lock:
                        self.peer_list = new_peers
                    self.gui_queue.put(("update_peers",))
                    return True
            return False
        except Exception:
            return False
        finally:
            s.close()

    def send_heartbeat(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
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
        s.settimeout(3)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
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

    def heartbeat_loop(self):
        while self.running:
            time.sleep(30)
            if self.running:
                self.send_heartbeat()

    def update_peer_list_loop(self):
        while self.running:
            time.sleep(60)
            if self.running:
                self.get_peer_list()

    def start_server(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(5)
        except Exception as e:
            self.gui_queue.put(("log", f"[Hệ thống Lỗi] Lỗi khởi chạy Server: {e}", ACCENT_RED))
            self.running = False
            return

        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_incoming, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def ensure_shared_key(self, target_ip, target_port):
        target_port = int(target_port)
        with self.lock:
            if (target_ip, target_port) in self.shared_keys:
                return self.shared_keys[(target_ip, target_port)]
                
        self.gui_queue.put(("log", f"[E2EE] Đang thiết lập kênh Diffie-Hellman với {target_ip}:{target_port}...", ACCENT_PURPLE))
        
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
                        self.gui_queue.put(("log", f"[E2EE] Kênh mã hóa bảo mật đã kết nối với {target_ip}:{target_port}!", ACCENT_GREEN))
                        return shared_key
        except Exception as e:
            self.gui_queue.put(("log", f"[E2EE Lỗi] Trao đổi khóa thất bại: {e}", ACCENT_RED))
        finally:
            s.close()
        return None

    def handle_incoming(self, conn, addr):
        try:
            msg = recv_json(conn, timeout=10)
            if not msg:
                return

            msg_type = msg.get("type")

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

                        # Reply public key
                        reply = {
                            "type": MSG_TYPE_KEY_EXCHANGE_REPLY,
                            "from": self.name,
                            "pub_key": dh_b.public_key
                        }
                        send_json(conn, reply)
                        self.gui_queue.put(("log", f"[E2EE] Đã đồng bộ khóa Diffie-Hellman tự động với [{sender_name}] ({from_ip}:{from_port})!", ACCENT_GREEN))
                    except Exception as e:
                        self.gui_queue.put(("log", f"[E2EE Lỗi] Lỗi phản hồi handshake: {e}", ACCENT_RED))

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
                        self.gui_queue.put(("msg_in", sender, content))
                    except Exception as e:
                        self.gui_queue.put(("log", f"[E2EE Lỗi] Không thể giải mã tin từ [{sender}]: {e}", ACCENT_RED))
                else:
                    self.gui_queue.put(("log", f"[E2EE Lỗi] Nhận tin mã hóa từ [{sender}] nhưng không có khóa bảo mật chung.", ACCENT_RED))

            elif msg_type == MSG_TYPE_BROADCAST:
                sender = msg.get("from", "Unknown")
                content = msg.get("content", "")
                msg_id = msg.get("msg_id")
                ttl = msg.get("ttl", 3)

                with self.lock:
                    if msg_id in self.seen_messages:
                        return
                    self.seen_messages.add(msg_id)

                self.gui_queue.put(("broadcast_in", sender, content))

                # Forward if TTL > 0
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
                        
                        self.gui_queue.put(("log", f"📁 [File E2EE] Nhận thành công '{safe_filename}' từ [{sender}]. Lưu tại: {dest_path}", ACCENT_MAGENTA))
                    except Exception as e:
                        self.gui_queue.put(("log", f"[Lỗi Nhận File] Lỗi giải mã file '{filename}' từ [{sender}]: {e}", ACCENT_RED))
                else:
                    self.gui_queue.put(("log", f"[E2EE Lỗi] Nhận file mã hóa từ [{sender}] nhưng không có khóa chung.", ACCENT_RED))

        except Exception:
            pass
        finally:
            conn.close()

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

    # ==================== GUI ACTIONS ====================

    def handle_refresh(self):
        """
        Manually triggers get_peer_list from GUI button.
        """
        threading.Thread(target=self.get_peer_list, daemon=True).start()

    def handle_leave_gui(self):
        """
        Triggered when closing or leaving from GUI.
        """
        if messagebox.askyesno("Xác nhận", "Bạn có chắc chắn muốn rời khỏi mạng chat?"):
            self.running = False
            self.leave_network()
            if self.server_socket:
                try:
                    self.server_socket.close()
                except Exception:
                    pass
            self.root.quit()

    def send_chat(self):
        """
        Handles sending a 1-1 encrypted message.
        """
        # 1. Check selection
        selected = self.peer_tree.selection()
        if not selected:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn một Peer trong danh sách bên trái để gửi tin 1-1!")
            return
            
        content = self.msg_entry.get().strip()
        if not content:
            return
            
        # Clear input box
        self.msg_entry.delete(0, tk.END)
        
        # Get target peer details
        item = self.peer_tree.item(selected[0])
        target_name, addr_str = item["values"]
        target_ip, target_port = addr_str.split(":")
        target_port = int(target_port)
        
        # Asynchronously send message to prevent UI freezing
        def send_task():
            shared_key = self.ensure_shared_key(target_ip, target_port)
            if not shared_key:
                self.gui_queue.put(("log", "[E2EE Lỗi] Từ chối gửi tin nhắn: Không thể thiết lập kênh bảo mật E2EE.", ACCENT_RED))
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
            
            success = self.send_direct_message(target_ip, target_port, chat_msg)
            if success:
                self.gui_queue.put(("msg_out", target_name, content))
            else:
                self.gui_queue.put(("log", f"[Lỗi] Không thể gửi tin nhắn đến {target_name} ({target_ip}:{target_port}). Peer có thể đã offline.", ACCENT_RED))
                
        threading.Thread(target=send_task, daemon=True).start()

    def send_broadcast(self):
        """
        Handles sending unencrypted broadcast message.
        """
        content = self.msg_entry.get().strip()
        if not content:
            return
            
        self.msg_entry.delete(0, tk.END)
        
        def send_task():
            with self.lock:
                active_peers = list(self.peer_list)
                
            if not active_peers:
                self.gui_queue.put(("log", "[Hệ thống] Không có peer nào online để nhận broadcast.", ACCENT_PURPLE))
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
                
            self.gui_queue.put(("broadcast_out", content))
            
        threading.Thread(target=send_task, daemon=True).start()

    def send_file(self):
        """
        Handles E2EE file sending.
        """
        selected = self.peer_tree.selection()
        if not selected:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn một Peer trong danh sách bên trái để gửi file!")
            return
            
        filepath = filedialog.askopenfilename()
        if not filepath:
            return
            
        try:
            file_size = os.path.getsize(filepath)
            if file_size > 2 * 1024 * 1024:
                messagebox.showerror("Lỗi", "Kích thước file lớn hơn 2MB. Vui lòng gửi file nhỏ hơn.")
                return
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đọc file: {e}")
            return
            
        item = self.peer_tree.item(selected[0])
        target_name, addr_str = item["values"]
        target_ip, target_port = addr_str.split(":")
        target_port = int(target_port)
        
        filename = os.path.basename(filepath)
        self.gui_queue.put(("log", f"[Hệ thống] Đang mã hóa và chuẩn bị gửi file '{filename}'...", ACCENT_PURPLE))
        
        def send_file_task():
            shared_key = self.ensure_shared_key(target_ip, target_port)
            if not shared_key:
                self.gui_queue.put(("log", "[E2EE Lỗi] Từ chối gửi file: Không thể thiết lập kênh bảo mật.", ACCENT_RED))
                return
                
            try:
                with open(filepath, "rb") as f:
                    data_bytes = f.read()
                
                ciphertext, iv = encrypt_bytes(data_bytes, shared_key)
                b64_ciphertext = base64.b64encode(ciphertext).decode('utf-8')
                b64_iv = base64.b64encode(iv).decode('utf-8')
            except Exception as e:
                self.gui_queue.put(("log", f"[Lỗi Đọc File] Không thể đọc/mã hóa file: {e}", ACCENT_RED))
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
                self.gui_queue.put(("log", f"📁 [File E2EE] Đã gửi thành công '{filename}' tới {target_name}!", ACCENT_MAGENTA))
            else:
                self.gui_queue.put(("log", f"[Lỗi] Không thể kết nối để gửi file tới {target_name}.", ACCENT_RED))
                
        threading.Thread(target=send_file_task, daemon=True).start()

    # ==================== THREAD-SAFE QUEUE WORKER ====================

    def process_queue(self):
        """
        Main thread worker that pops and executes GUI updates asynchronously.
        """
        try:
            while True:
                item = self.gui_queue.get_nowait()
                action = item[0]
                
                if action == "log":
                    text, color = item[1], item[2]
                    self.append_to_chat(text + "\n", color)
                    
                elif action == "msg_in":
                    sender, content = item[1], item[2]
                    text = f"🔒 [E2EE Chat 1-1] [{sender}]: {content}\n"
                    self.append_to_chat(text, ACCENT_GREEN)
                    
                elif action == "msg_out":
                    target_name, content = item[1], item[2]
                    text = f"🔒 [Đã gửi E2EE 1-1 tới {target_name}]: {content}\n"
                    self.append_to_chat(text, "#C1FFC1")
                    
                elif action == "broadcast_in":
                    sender, content = item[1], item[2]
                    text = f"📢 [Broadcast] [{sender}]: {content}\n"
                    self.append_to_chat(text, ACCENT_CYAN)
                    
                elif action == "broadcast_out":
                    content = item[1]
                    text = f"📢 [Đã phát Broadcast]: {content}\n"
                    self.append_to_chat(text, ACCENT_CYAN)
                    
                elif action == "update_peers":
                    # Clear tree and insert updated peer list
                    self.peer_tree.delete(*self.peer_tree.get_children())
                    with self.lock:
                        peers_copy = list(self.peer_list)
                    for p in peers_copy:
                        self.peer_tree.insert("", tk.END, values=(p["name"], f"{p['ip']}:{p['port']}"))
                        
                self.gui_queue.task_done()
        except queue.Empty:
            pass
            
        # Re-schedule check in 100ms
        if self.running or not hasattr(self, 'main_container'):
            self.root.after(100, self.process_queue)

    def append_to_chat(self, text, color_code):
        """
        Helper that appends formatted colorized text to the chat ScrolledText.
        """
        self.chat_box.config(state="normal")
        
        # Determine tag type
        tag = "text"
        if color_code == ACCENT_GREEN:
            tag = "chat_in"
        elif color_code == "#C1FFC1":
            tag = "chat_out"
        elif color_code == ACCENT_CYAN:
            tag = "broadcast"
        elif color_code == ACCENT_MAGENTA:
            tag = "file"
        elif color_code == ACCENT_RED:
            tag = "error"
        elif color_code == ACCENT_PURPLE or color_code == COLOR_SYSTEM:
            tag = "system"
            
        self.chat_box.insert(tk.END, text, tag)
        self.chat_box.config(state="disabled")
        self.chat_box.see(tk.END)

def main():
    root = tk.Tk()
    
    # Custom window close handler
    app = PeerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.handle_leave_gui)
    
    root.mainloop()

if __name__ == "__main__":
    main()
