import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import socket
import threading
import queue
import uuid
import time
import os
import base64

from common.message import (
    MSG_TYPE_REGISTER, MSG_TYPE_PEER_LIST, MSG_TYPE_GET_PEERS,
    MSG_TYPE_HEARTBEAT, MSG_TYPE_LEAVE, MSG_TYPE_CHAT, MSG_TYPE_BROADCAST,
    MSG_TYPE_FILE, MSG_TYPE_KEY_EXCHANGE, MSG_TYPE_KEY_EXCHANGE_REPLY,
    MSG_TYPE_ACK, MSG_TYPE_GROUP_CREATE, MSG_TYPE_GROUP_JOIN,
    MSG_TYPE_GROUP_LEAVE, MSG_TYPE_GROUP_MSG, MSG_TYPE_GROUP_LIST,
    MSG_TYPE_GROUP_SYNC, MSG_TYPE_PEER_JOINED, MSG_TYPE_PEER_LEFT
)
from common.utils import send_json, recv_json, send_reliable
from common.encryption import (
    DiffieHellman, encrypt_string, decrypt_string, encrypt_bytes, decrypt_bytes
)

# ======================== THEME ========================
BG_WHITE = "#FFFFFF"
BG_SIDEBAR = "#F7F7FA"
BG_HOVER = "#EEEEF5"
BG_SELECTED = "#E0E0F0"
BG_BUBBLE_IN = "#F0F0F5"
BG_BUBBLE_OUT = "#5B5FC7"
BG_INPUT = "#F2F2F5"
BG_LOGIN = "#F0F0F8"

TEXT_DARK = "#1E1E2E"
TEXT_MUTED = "#8C8CA0"
TEXT_WHITE = "#FFFFFF"
TEXT_ACCENT = "#5B5FC7"
GREEN = "#22C55E"
RED = "#EF4444"

FONT = "Segoe UI"
BUBBLE_WRAP = 380


class PeerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P Messenger")
        self.root.geometry("1060x700")
        self.root.configure(bg=BG_WHITE)
        self.root.minsize(900, 600)

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
        self.groups = {}

        # Separate chat histories
        self.broadcast_history = []
        self.peer_chats = {}
        self.group_chats = {}

        # Current view
        self.current_view = "broadcast"
        self.current_peer_key = None
        self.current_peer_name = None
        self.current_group = None

        # Sidebar selection tracking
        self._selected_frame = None

        self.gui_queue = queue.Queue()
        self._build_login()
        self.root.after(100, self._process_queue)

    # ======================== LOGIN ========================

    def _build_login(self):
        self.login_frame = tk.Frame(self.root, bg=BG_WHITE)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.login_frame, text="P2P Messenger", bg=BG_WHITE,
                 fg=TEXT_ACCENT, font=(FONT, 22, "bold")).pack(pady=(0, 2))
        tk.Label(self.login_frame, text="End-to-end Encrypted", bg=BG_WHITE,
                 fg=TEXT_MUTED, font=(FONT, 10)).pack(pady=(0, 25))

        fields = tk.Frame(self.login_frame, bg=BG_WHITE)
        fields.pack()

        for i, (label, default) in enumerate([
            ("Tên hiển thị", "Alice"), ("Cổng lắng nghe", "5001"),
            ("IP Bootstrap", "127.0.0.1"), ("Cổng Bootstrap", "5555")
        ]):
            tk.Label(fields, text=label, bg=BG_WHITE, fg=TEXT_DARK,
                     font=(FONT, 10), anchor="w").grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            var = tk.StringVar(value=default)
            entry = tk.Entry(fields, textvariable=var, font=(FONT, 11), width=22,
                             bg=BG_INPUT, fg=TEXT_DARK, relief="flat", bd=0,
                             highlightthickness=1, highlightcolor=TEXT_ACCENT,
                             highlightbackground="#D0D0D8")
            entry.grid(row=i, column=1, pady=4, ipady=5)
            if i == 0: self._var_name = var
            elif i == 1: self._var_port = var
            elif i == 2: self._var_bhost = var
            elif i == 3: self._var_bport = var

        btn = tk.Button(self.login_frame, text="Kết nối vào mạng", bg=TEXT_ACCENT,
                        fg=TEXT_WHITE, font=(FONT, 11, "bold"), relief="flat",
                        cursor="hand2", padx=30, pady=8, command=self._handle_join)
        btn.pack(pady=(20, 0))

    def _handle_join(self):
        self.name = self._var_name.get().strip()
        if not self.name:
            messagebox.showerror("Lỗi", "Tên không được rỗng!")
            return
        try:
            self.port = int(self._var_port.get().strip())
            self.bootstrap_port = int(self._var_bport.get().strip())
        except ValueError:
            messagebox.showerror("Lỗi", "Cổng phải là số nguyên!")
            return
        self.bootstrap_host = self._var_bhost.get().strip()

        if not self._register_with_bootstrap():
            messagebox.showerror("Lỗi", "Không thể kết nối Bootstrap Server.")
            return

        self.running = True
        threading.Thread(target=self._start_server, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()

        self.login_frame.destroy()
        self._build_main()

        self.broadcast_history.append(("system", f"Chào mừng {self.name} đến mạng P2P!"))
        self._render_view()
        threading.Thread(target=self._get_peer_list, daemon=True).start()

    # ======================== MAIN LAYOUT ========================

    def _build_main(self):
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_chat_area()

    def _build_sidebar(self):
        self.sidebar = tk.Frame(self.root, bg=BG_SIDEBAR, width=260)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # Header
        hdr = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        hdr.pack(fill="x", padx=18, pady=(18, 10))
        tk.Label(hdr, text="P2P Messenger", bg=BG_SIDEBAR, fg=TEXT_ACCENT,
                 font=(FONT, 14, "bold")).pack(anchor="w")
        tk.Label(hdr, text="End-to-end Encrypted", bg=BG_SIDEBAR, fg=TEXT_MUTED,
                 font=(FONT, 9)).pack(anchor="w")

        sep = tk.Frame(self.sidebar, bg="#E0E0E8", height=1)
        sep.pack(fill="x", padx=12, pady=4)

        # Broadcast nav item
        self.broadcast_btn = self._sidebar_nav_item("Broadcast", self._select_broadcast)
        self.broadcast_btn.pack(fill="x")
        self._highlight_sidebar(self.broadcast_btn)

        sep2 = tk.Frame(self.sidebar, bg="#E0E0E8", height=1)
        sep2.pack(fill="x", padx=12, pady=4)

        # Peers section
        tk.Label(self.sidebar, text="PEERS ONLINE", bg=BG_SIDEBAR, fg=TEXT_MUTED,
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=18, pady=(8, 2))
        self.peer_list_frame = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        self.peer_list_frame.pack(fill="x")

        sep3 = tk.Frame(self.sidebar, bg="#E0E0E8", height=1)
        sep3.pack(fill="x", padx=12, pady=4)

        # Groups section
        tk.Label(self.sidebar, text="NHÓM CHAT", bg=BG_SIDEBAR, fg=TEXT_MUTED,
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=18, pady=(8, 2))
        self.group_list_frame = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        self.group_list_frame.pack(fill="x")

        create_grp = tk.Button(self.sidebar, text="+ Tạo nhóm mới", bg=BG_SIDEBAR,
                               fg=TEXT_ACCENT, font=(FONT, 10), relief="flat",
                               cursor="hand2", anchor="w", command=self._gui_create_group)
        create_grp.pack(fill="x", padx=18, pady=2)

        # Bottom spacer + leave button
        spacer = tk.Frame(self.sidebar, bg=BG_SIDEBAR)
        spacer.pack(fill="both", expand=True)

        leave_btn = tk.Button(self.sidebar, text="Rời khỏi mạng", bg=RED,
                              fg=TEXT_WHITE, font=(FONT, 10, "bold"), relief="flat",
                              cursor="hand2", padx=10, pady=6, command=self._handle_leave)
        leave_btn.pack(fill="x", padx=18, pady=(0, 18))

    def _sidebar_nav_item(self, text, command):
        f = tk.Frame(self.sidebar, bg=BG_SIDEBAR, cursor="hand2")
        lbl = tk.Label(f, text=text, bg=BG_SIDEBAR, fg=TEXT_DARK,
                       font=(FONT, 11), padx=18, pady=8, anchor="w")
        lbl.pack(fill="x")
        for w in (f, lbl):
            w.bind("<Button-1>", lambda e, cmd=command, frame=f: (cmd(), self._highlight_sidebar(frame)))
        return f

    def _highlight_sidebar(self, frame):
        if self._selected_frame and self._widget_exists(self._selected_frame):
            self._set_bg(self._selected_frame, BG_SIDEBAR)
        if self._widget_exists(frame):
            self._set_bg(frame, BG_SELECTED)
        self._selected_frame = frame

    def _widget_exists(self, widget):
        try:
            widget.winfo_exists()
            return True
        except tk.TclError:
            return False

    def _set_bg(self, widget, color):
        try:
            if not widget.winfo_exists():
                return
            widget.configure(bg=color)
            for child in widget.winfo_children():
                self._set_bg(child, color)
        except tk.TclError:
            pass

    def _build_chat_area(self):
        self.chat_container = tk.Frame(self.root, bg=BG_WHITE)
        self.chat_container.grid(row=0, column=1, sticky="nsew")
        self.chat_container.columnconfigure(0, weight=1)
        self.chat_container.rowconfigure(1, weight=1)

        # Header bar
        self.header_frame = tk.Frame(self.chat_container, bg=BG_WHITE, height=60)
        self.header_frame.grid(row=0, column=0, sticky="ew")
        self.header_frame.grid_propagate(False)
        self.header_frame.columnconfigure(0, weight=1)

        sep = tk.Frame(self.chat_container, bg="#E8E8EE", height=1)
        sep.grid(row=0, column=0, sticky="sew")

        self.header_name = tk.Label(self.header_frame, text="Broadcast", bg=BG_WHITE,
                                    fg=TEXT_DARK, font=(FONT, 14, "bold"), anchor="w")
        self.header_name.grid(row=0, column=0, sticky="w", padx=20, pady=(10, 0))

        self.header_status = tk.Label(self.header_frame, text="Thông báo hệ thống & broadcast",
                                      bg=BG_WHITE, fg=TEXT_MUTED, font=(FONT, 9), anchor="w")
        self.header_status.grid(row=1, column=0, sticky="w", padx=20)

        self.header_add_btn = tk.Button(self.header_frame, text="+ Thêm peer", bg=TEXT_ACCENT,
                                        fg=TEXT_WHITE, font=(FONT, 9, "bold"), relief="flat",
                                        cursor="hand2", padx=10, pady=3,
                                        command=self._gui_add_peer_to_group)
        self.header_add_btn.grid(row=0, column=1, rowspan=2, padx=20, pady=10)
        self.header_add_btn.grid_remove()

        # Chat display (Text widget for embedding bubbles)
        self.chat_text = tk.Text(self.chat_container, bg=BG_WHITE, fg=TEXT_DARK,
                                 font=(FONT, 10), state="disabled", wrap="word",
                                 borderwidth=0, highlightthickness=0, padx=5, pady=10)
        self.chat_text.grid(row=1, column=0, sticky="nsew")
        self.chat_text.tag_config("right", justify="right")
        self.chat_text.tag_config("center", justify="center")

        chat_scroll = ttk.Scrollbar(self.chat_container, command=self.chat_text.yview)
        chat_scroll.grid(row=1, column=1, sticky="ns")
        self.chat_text.configure(yscrollcommand=chat_scroll.set)

        # Input bar
        sep2 = tk.Frame(self.chat_container, bg="#E8E8EE", height=1)
        sep2.grid(row=2, column=0, columnspan=2, sticky="ew")

        input_bar = tk.Frame(self.chat_container, bg=BG_WHITE, height=56)
        input_bar.grid(row=3, column=0, columnspan=2, sticky="ew")
        input_bar.columnconfigure(1, weight=1)

        self.file_btn = tk.Button(input_bar, text="\U0001F4CE", font=(FONT, 14),
                                  bg=BG_WHITE, fg=TEXT_MUTED, relief="flat",
                                  cursor="hand2", command=self._send_file)
        self.file_btn.grid(row=0, column=0, padx=(12, 4), pady=10)

        self.msg_entry = tk.Entry(input_bar, font=(FONT, 11), bg=BG_INPUT,
                                  fg=TEXT_DARK, relief="flat", bd=0,
                                  highlightthickness=1, highlightcolor=TEXT_ACCENT,
                                  highlightbackground="#D8D8E0")
        self.msg_entry.grid(row=0, column=1, sticky="ew", ipady=8, pady=10)
        self.msg_entry.bind("<Return>", lambda e: self._send_message())

        send_btn = tk.Button(input_bar, text="\u27A4", font=(FONT, 16),
                             bg=TEXT_ACCENT, fg=TEXT_WHITE, relief="flat",
                             cursor="hand2", padx=12, pady=2, command=self._send_message)
        send_btn.grid(row=0, column=2, padx=(8, 12), pady=10)

    # ======================== SIDEBAR PEER/GROUP LISTS ========================

    def _refresh_peer_sidebar(self):
        # If selected frame belongs to peer list, clear selection reference
        if self._selected_frame and not self._widget_exists(self._selected_frame):
            self._selected_frame = None
        for w in self.peer_list_frame.winfo_children():
            w.destroy()
        with self.lock:
            peers = list(self.peer_list)
        for p in peers:
            f = self._create_peer_row(p["name"], p["ip"], p["port"])
            # Re-highlight if this peer is currently selected
            if self.current_view == "peer" and self.current_peer_key == (p["ip"], int(p["port"])):
                self._set_bg(f, BG_SELECTED)
                self._selected_frame = f

    def _create_peer_row(self, name, ip, port):
        f = tk.Frame(self.peer_list_frame, bg=BG_SIDEBAR, cursor="hand2")
        f.pack(fill="x", padx=10, pady=1)

        dot = tk.Label(f, text="\u25CF", fg=GREEN, bg=BG_SIDEBAR, font=(FONT, 10))
        dot.pack(side="left", padx=(8, 6))

        info = tk.Frame(f, bg=BG_SIDEBAR)
        info.pack(side="left", fill="x", expand=True)
        n_lbl = tk.Label(info, text=name, bg=BG_SIDEBAR, fg=TEXT_DARK,
                         font=(FONT, 10, "bold"), anchor="w")
        n_lbl.pack(anchor="w")
        s_lbl = tk.Label(info, text="Online", bg=BG_SIDEBAR, fg=GREEN,
                         font=(FONT, 8), anchor="w")
        s_lbl.pack(anchor="w")

        for w in (f, dot, info, n_lbl, s_lbl):
            w.bind("<Button-1>", lambda e, i=ip, p=port, nm=name, fr=f:
                   (self._select_peer(i, p, nm), self._highlight_sidebar(fr)))
        return f

    def _refresh_group_sidebar(self):
        if self._selected_frame and not self._widget_exists(self._selected_frame):
            self._selected_frame = None
        for w in self.group_list_frame.winfo_children():
            w.destroy()
        with self.lock:
            groups = dict(self.groups)
        for gname, gdata in groups.items():
            f = self._create_group_row(gname, len(gdata["members"]))
            if self.current_view == "group" and self.current_group == gname:
                self._set_bg(f, BG_SELECTED)
                self._selected_frame = f

    def _create_group_row(self, gname, count):
        f = tk.Frame(self.group_list_frame, bg=BG_SIDEBAR, cursor="hand2")
        f.pack(fill="x", padx=10, pady=1)

        icon = tk.Label(f, text="\U0001F465", bg=BG_SIDEBAR, font=(FONT, 10))
        icon.pack(side="left", padx=(8, 6))

        info = tk.Frame(f, bg=BG_SIDEBAR)
        info.pack(side="left", fill="x", expand=True)
        n_lbl = tk.Label(info, text=gname, bg=BG_SIDEBAR, fg=TEXT_DARK,
                         font=(FONT, 10, "bold"), anchor="w")
        n_lbl.pack(anchor="w")
        s_lbl = tk.Label(info, text=f"{count} thành viên", bg=BG_SIDEBAR,
                         fg=TEXT_MUTED, font=(FONT, 8), anchor="w")
        s_lbl.pack(anchor="w")

        for w in (f, icon, info, n_lbl, s_lbl):
            w.bind("<Button-1>", lambda e, g=gname, fr=f:
                   (self._select_group(g), self._highlight_sidebar(fr)))
        return f

    # ======================== VIEW SELECTION ========================

    def _select_broadcast(self):
        self.current_view = "broadcast"
        self.current_peer_key = None
        self.current_group = None
        self.header_name.config(text="Broadcast")
        self.header_status.config(text="Thông báo hệ thống & tin nhắn broadcast", fg=TEXT_MUTED)
        self.header_add_btn.grid_remove()
        self._render_view()

    def _select_peer(self, ip, port, name):
        self.current_view = "peer"
        self.current_peer_key = (ip, int(port))
        self.current_peer_name = name
        self.current_group = None
        self.header_name.config(text=name)
        self.header_status.config(text=f"\u25CF Secure P2P Connection", fg=GREEN)
        self.header_add_btn.grid_remove()
        self._render_view()

    def _select_group(self, gname):
        self.current_view = "group"
        self.current_group = gname
        self.current_peer_key = None
        with self.lock:
            grp = self.groups.get(gname)
            count = len(grp["members"]) if grp else 0
        self.header_name.config(text=gname)
        self.header_status.config(text=f"{count} thành viên", fg=TEXT_MUTED)
        self.header_add_btn.grid()
        self._render_view()

    # ======================== CHAT DISPLAY ========================

    def _clear_chat(self):
        self.chat_text.config(state="normal")
        for child in self.chat_text.winfo_children():
            child.destroy()
        self.chat_text.delete("1.0", "end")
        self.chat_text.config(state="disabled")

    def _render_view(self):
        self._clear_chat()
        if self.current_view == "broadcast":
            for item in self.broadcast_history:
                self._render_item(item)
        elif self.current_view == "peer" and self.current_peer_key:
            for item in self.peer_chats.get(self.current_peer_key, []):
                self._render_item(item)
        elif self.current_view == "group" and self.current_group:
            for item in self.group_chats.get(self.current_group, []):
                self._render_item(item)

    def _render_item(self, item):
        t = item[0]
        if t == "in":
            self._add_bubble(item[2], sender=item[1])
        elif t == "out":
            self._add_bubble(item[1], is_outgoing=True)
        elif t == "system":
            self._add_system(item[1])
        elif t == "broadcast_in":
            self._add_bubble(item[2], sender=item[1])
        elif t == "broadcast_out":
            self._add_bubble(item[1], is_outgoing=True)

    def _add_bubble(self, text, sender=None, is_outgoing=False):
        self.chat_text.config(state="normal")

        if not is_outgoing and sender:
            lbl = tk.Label(self.chat_text, text=sender, bg=BG_WHITE, fg=TEXT_MUTED,
                           font=(FONT, 9, "bold"), anchor="w")
            self.chat_text.window_create("end", window=lbl, padx=18, pady=10)
            self.chat_text.insert("end", "\n")

        if is_outgoing:
            bubble = tk.Label(self.chat_text, text=text, bg=BG_BUBBLE_OUT, fg=TEXT_WHITE,
                              font=(FONT, 10), wraplength=BUBBLE_WRAP, justify="left",
                              padx=14, pady=10, anchor="w")
            mark = self.chat_text.index("end-1c")
            self.chat_text.window_create("end", window=bubble, padx=18, pady=3)
            self.chat_text.insert("end", "\n")
            self.chat_text.tag_add("right", mark, "end-1c")
        else:
            bubble = tk.Label(self.chat_text, text=text, bg=BG_BUBBLE_IN, fg=TEXT_DARK,
                              font=(FONT, 10), wraplength=BUBBLE_WRAP, justify="left",
                              padx=14, pady=10, anchor="w")
            self.chat_text.window_create("end", window=bubble, padx=18, pady=3)
            self.chat_text.insert("end", "\n")

        self.chat_text.config(state="disabled")
        self.chat_text.see("end")

    def _add_system(self, text):
        self.chat_text.config(state="normal")
        lbl = tk.Label(self.chat_text, text=text, bg=BG_WHITE, fg=TEXT_MUTED,
                       font=(FONT, 9, "italic"), anchor="center")
        mark = self.chat_text.index("end-1c")
        self.chat_text.window_create("end", window=lbl, padx=18, pady=8)
        self.chat_text.insert("end", "\n")
        self.chat_text.tag_add("center", mark, "end-1c")
        self.chat_text.config(state="disabled")
        self.chat_text.see("end")

    # ======================== SEND ACTIONS ========================

    def _send_message(self):
        content = self.msg_entry.get().strip()
        if not content:
            return
        self.msg_entry.delete(0, "end")

        if self.current_view == "broadcast":
            self._do_send_broadcast(content)
        elif self.current_view == "peer" and self.current_peer_key:
            self._do_send_chat(content)
        elif self.current_view == "group" and self.current_group:
            self._do_send_group(content)

    def _do_send_chat(self, content):
        ip, port = self.current_peer_key
        name = self.current_peer_name

        def task():
            shared_key = self._ensure_shared_key(ip, port)
            if not shared_key:
                self.gui_queue.put(("log", "[E2EE] Không thể thiết lập kênh bảo mật."))
                return
            b64_ct, b64_iv = encrypt_string(content, shared_key)
            msg = {"type": MSG_TYPE_CHAT, "from": self.name, "from_port": self.port,
                   "ciphertext": b64_ct, "iv": b64_iv, "msg_id": str(uuid.uuid4())}
            ok = self._send_with_ack(ip, port, msg)
            if ok:
                self.gui_queue.put(("msg_out", name, content, ip, port))
            else:
                self.gui_queue.put(("log", f"[Lỗi] Không nhận được ACK từ {name}."))
        threading.Thread(target=task, daemon=True).start()

    def _do_send_broadcast(self, content):
        def task():
            with self.lock:
                peers = list(self.peer_list)
            if not peers:
                self.gui_queue.put(("log", "Không có peer nào online."))
                return
            msg_id = str(uuid.uuid4())
            msg = {"type": MSG_TYPE_BROADCAST, "from": self.name,
                   "content": content, "msg_id": msg_id, "ttl": 3}
            with self.lock:
                self.seen_messages.add(msg_id)
            for p in peers:
                threading.Thread(target=self._send_direct,
                                 args=(p["ip"], p["port"], msg), daemon=True).start()
            self.gui_queue.put(("broadcast_out", content))
        threading.Thread(target=task, daemon=True).start()

    def _do_send_group(self, content):
        gname = self.current_group

        def task():
            with self.lock:
                grp = self.groups.get(gname)
                if not grp:
                    return
                members = list(grp["members"])
            msg_id = str(uuid.uuid4())
            with self.lock:
                self.seen_messages.add(msg_id)
            msg = {"type": MSG_TYPE_GROUP_MSG, "group_name": gname,
                   "from": self.name, "content": content, "msg_id": msg_id}
            sent = 0
            for m_ip, m_port, _ in members:
                if m_ip == self.ip and m_port == self.port:
                    continue
                if self._send_direct(m_ip, m_port, msg):
                    sent += 1
            total = max(len(members) - 1, 1)
            self.gui_queue.put(("group_out", gname, content, sent, total))
        threading.Thread(target=task, daemon=True).start()

    def _send_file(self):
        if self.current_view != "peer" or not self.current_peer_key:
            messagebox.showinfo("Thông báo", "Chọn một peer để gửi file.")
            return
        filepath = filedialog.askopenfilename()
        if not filepath:
            return
        try:
            if os.path.getsize(filepath) > 2 * 1024 * 1024:
                messagebox.showerror("Lỗi", "File lớn hơn 2MB.")
                return
        except Exception as e:
            messagebox.showerror("Lỗi", str(e))
            return
        ip, port = self.current_peer_key
        name = self.current_peer_name
        filename = os.path.basename(filepath)

        def task():
            shared_key = self._ensure_shared_key(ip, port)
            if not shared_key:
                self.gui_queue.put(("log", "[E2EE] Không thể thiết lập kênh bảo mật."))
                return
            try:
                with open(filepath, "rb") as f:
                    data = f.read()
                ct, iv = encrypt_bytes(data, shared_key)
                b64_ct = base64.b64encode(ct).decode()
                b64_iv = base64.b64encode(iv).decode()
            except Exception as e:
                self.gui_queue.put(("log", f"[Lỗi] Đọc/mã hóa file thất bại: {e}"))
                return
            msg = {"type": MSG_TYPE_FILE, "from": self.name, "from_port": self.port,
                   "filename": filename, "ciphertext": b64_ct, "iv": b64_iv,
                   "msg_id": str(uuid.uuid4())}
            ok = self._send_with_ack(ip, port, msg)
            if ok:
                self.gui_queue.put(("msg_out", name, f"[File] {filename}", ip, port))
            else:
                self.gui_queue.put(("log", f"[Lỗi] Gửi file tới {name} thất bại."))
        threading.Thread(target=task, daemon=True).start()

    # ======================== GROUP GUI ACTIONS ========================

    def _gui_create_group(self):
        gname = simpledialog.askstring("Tạo nhóm", "Tên nhóm mới:", parent=self.root)
        if not gname or not gname.strip():
            return
        gname = gname.strip()
        with self.lock:
            if gname in self.groups:
                messagebox.showinfo("Thông báo", f"Nhóm '{gname}' đã tồn tại.")
                return
            self.groups[gname] = {"members": [(self.ip, self.port, self.name)], "creator": self.name}
        self.broadcast_history.append(("system", f"Đã tạo nhóm '{gname}'."))
        self.gui_queue.put(("update_groups",))
        if self.current_view == "broadcast":
            self._render_view()

    def _gui_add_peer_to_group(self):
        if not self.current_group:
            return
        gname = self.current_group
        popup = tk.Toplevel(self.root)
        popup.title(f"Thêm peer vào '{gname}'")
        popup.geometry("320x400")
        popup.configure(bg=BG_WHITE)
        popup.transient(self.root)
        popup.grab_set()

        tk.Label(popup, text=f"Chọn peer:", bg=BG_WHITE, fg=TEXT_DARK,
                 font=(FONT, 11, "bold")).pack(pady=(15, 8))

        lb = tk.Listbox(popup, font=(FONT, 10), bg=BG_INPUT, fg=TEXT_DARK,
                        selectbackground=TEXT_ACCENT, selectforeground=TEXT_WHITE,
                        relief="flat", highlightthickness=0)
        lb.pack(fill="both", expand=True, padx=18, pady=5)

        with self.lock:
            peers = list(self.peer_list)
            grp = self.groups.get(gname, {})
            members = grp.get("members", [])

        available = []
        for p in peers:
            if not any(m[0] == p["ip"] and m[1] == p["port"] for m in members):
                available.append(p)
                lb.insert("end", f"  {p['name']}  ({p['ip']}:{p['port']})")

        def add():
            sel = lb.curselection()
            if not sel:
                return
            p = available[sel[0]]
            popup.destroy()
            self._do_add_peer_to_group(gname, p["ip"], p["port"], p["name"])

        tk.Button(popup, text="Thêm vào nhóm", bg=TEXT_ACCENT, fg=TEXT_WHITE,
                  font=(FONT, 10, "bold"), relief="flat", cursor="hand2",
                  padx=20, pady=6, command=add).pack(pady=12)

    def _do_add_peer_to_group(self, gname, t_ip, t_port, t_name):
        t_port = int(t_port)
        with self.lock:
            if gname not in self.groups:
                return
            members = self.groups[gname]["members"]
            if any(m[0] == t_ip and m[1] == t_port for m in members):
                return
            members.append((t_ip, t_port, t_name))

        def task():
            with self.lock:
                all_m = list(self.groups[gname]["members"])
                m_list = [(m[0], m[1], m[2]) for m in all_m]
                creator = self.groups[gname]["creator"]
            join_msg = {"type": MSG_TYPE_GROUP_JOIN, "group_name": gname,
                        "from": self.name, "from_ip": self.ip, "from_port": self.port,
                        "msg_id": str(uuid.uuid4())}
            for m_ip, m_port, _ in all_m:
                if m_ip == self.ip and m_port == self.port:
                    continue
                self._send_direct(m_ip, m_port, join_msg)
                sync = {"type": MSG_TYPE_GROUP_SYNC, "group_name": gname,
                        "members": m_list, "creator": creator, "msg_id": str(uuid.uuid4())}
                self._send_direct(m_ip, m_port, sync)
            self.gui_queue.put(("log", f"Đã thêm [{t_name}] vào nhóm '{gname}'."))
            self.gui_queue.put(("update_groups",))
        threading.Thread(target=task, daemon=True).start()

    def _handle_leave(self):
        if messagebox.askyesno("Xác nhận", "Rời khỏi mạng P2P?"):
            self.running = False
            self._leave_network()
            if self.server_socket:
                try:
                    self.server_socket.close()
                except Exception:
                    pass
            self.root.quit()

    def _handle_refresh(self):
        threading.Thread(target=self._get_peer_list, daemon=True).start()

    # ======================== NETWORK (unchanged logic) ========================

    def _register_with_bootstrap(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            if send_json(s, {"type": MSG_TYPE_REGISTER, "ip": self.ip, "port": self.port, "name": self.name}):
                res = recv_json(s, timeout=4)
                return res and res.get("status") == "success"
            return False
        except Exception:
            return False
        finally:
            s.close()

    def _get_peer_list(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            if send_json(s, {"type": MSG_TYPE_GET_PEERS, "ip": self.ip, "port": self.port}):
                res = recv_json(s, timeout=4)
                if res and res.get("type") == MSG_TYPE_PEER_LIST:
                    with self.lock:
                        self.peer_list = res.get("peers", [])
                    self.gui_queue.put(("update_peers",))
                    return True
            return False
        except Exception:
            return False
        finally:
            s.close()

    def _send_heartbeat(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            send_json(s, {"type": MSG_TYPE_HEARTBEAT, "ip": self.ip, "port": self.port})
        except Exception:
            pass
        finally:
            s.close()

    def _leave_network(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((self.bootstrap_host, self.bootstrap_port))
            send_json(s, {"type": MSG_TYPE_LEAVE, "ip": self.ip, "port": self.port})
        except Exception:
            pass
        finally:
            s.close()

    def _heartbeat_loop(self):
        while self.running:
            time.sleep(15)
            if self.running:
                self._send_heartbeat()

    def _update_loop(self):
        time.sleep(2)
        while self.running:
            self._get_peer_list()
            time.sleep(10)

    def _start_server(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(10)
        except Exception:
            self.running = False
            return
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self._handle_incoming, args=(conn, addr), daemon=True).start()
            except Exception:
                break

    def _send_direct(self, ip, port, msg):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((ip, int(port)))
            send_json(s, msg)
            return True
        except Exception:
            return False
        finally:
            s.close()

    def _send_with_ack(self, ip, port, msg, retries=3):
        ok, _ = send_reliable(ip, port, msg, max_retries=retries)
        return ok

    def _ensure_shared_key(self, ip, port):
        port = int(port)
        with self.lock:
            if (ip, port) in self.shared_keys:
                return self.shared_keys[(ip, port)]
        self.gui_queue.put(("log", f"[E2EE] Đang trao đổi khóa với {ip}:{port}..."))
        dh = DiffieHellman()
        msg = {"type": MSG_TYPE_KEY_EXCHANGE, "from": self.name,
               "from_ip": self.ip, "from_port": self.port, "pub_key": dh.public_key}
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((ip, port))
            if send_json(s, msg):
                res = recv_json(s, timeout=5)
                if res and res.get("type") == MSG_TYPE_KEY_EXCHANGE_REPLY:
                    pk = res.get("pub_key")
                    if pk:
                        key = dh.generate_shared_key(pk)
                        with self.lock:
                            self.shared_keys[(ip, port)] = key
                        self.gui_queue.put(("log", f"[E2EE] Kênh bảo mật đã thiết lập!"))
                        return key
        except Exception as e:
            self.gui_queue.put(("log", f"[E2EE] Trao đổi khóa thất bại: {e}"))
        finally:
            s.close()
        return None

    # ======================== INCOMING HANDLERS ========================

    def _send_ack(self, conn, msg_id):
        send_json(conn, {"type": MSG_TYPE_ACK, "msg_id": msg_id})

    def _handle_incoming(self, conn, addr):
        try:
            msg = recv_json(conn, timeout=10)
            if not msg:
                return
            t = msg.get("type")

            if t == MSG_TYPE_KEY_EXCHANGE:
                self._on_key_exchange(conn, msg)
            elif t == MSG_TYPE_CHAT:
                self._on_chat(conn, addr, msg)
            elif t == MSG_TYPE_BROADCAST:
                self._on_broadcast(conn, addr, msg)
            elif t == MSG_TYPE_FILE:
                self._on_file(conn, addr, msg)
            elif t == MSG_TYPE_GROUP_CREATE:
                self._on_group_create(conn, msg)
            elif t == MSG_TYPE_GROUP_JOIN:
                self._on_group_join(conn, msg)
            elif t == MSG_TYPE_GROUP_LEAVE:
                self._on_group_leave(conn, msg)
            elif t == MSG_TYPE_GROUP_MSG:
                self._on_group_msg(conn, addr, msg)
            elif t == MSG_TYPE_GROUP_SYNC:
                self._on_group_sync(conn, msg)
            elif t == MSG_TYPE_PEER_JOINED:
                self.gui_queue.put(("peer_event", f"[{msg.get('peer_name','?')}] vừa tham gia mạng."))
                self._get_peer_list()
            elif t == MSG_TYPE_PEER_LEFT:
                self.gui_queue.put(("peer_event", f"[{msg.get('peer_name','?')}] vừa rời khỏi mạng."))
                self._get_peer_list()
        except Exception:
            pass
        finally:
            conn.close()

    def _on_key_exchange(self, conn, msg):
        from_ip = msg.get("from_ip")
        try:
            from_port = int(msg.get("from_port"))
        except (ValueError, TypeError):
            return
        pk = msg.get("pub_key")
        if from_ip and from_port and pk:
            try:
                dh = DiffieHellman()
                key = dh.generate_shared_key(pk)
                with self.lock:
                    self.shared_keys[(from_ip, from_port)] = key
                send_json(conn, {"type": MSG_TYPE_KEY_EXCHANGE_REPLY, "from": self.name, "pub_key": dh.public_key})
                self.gui_queue.put(("log", f"[E2EE] Đã đồng bộ khóa với [{msg.get('from','?')}]."))
            except Exception as e:
                self.gui_queue.put(("log", f"[E2EE] Lỗi handshake: {e}"))

    def _on_chat(self, conn, addr, msg):
        sender = msg.get("from", "?")
        mid = msg.get("msg_id")
        try:
            fp = int(msg.get("from_port"))
        except (ValueError, TypeError):
            return
        sip = addr[0]
        with self.lock:
            key = self.shared_keys.get((sip, fp))
        ct, iv = msg.get("ciphertext"), msg.get("iv")
        if key and ct and iv:
            try:
                content = decrypt_string(ct, key, iv)
                if mid:
                    self._send_ack(conn, mid)
                self.gui_queue.put(("msg_in", sender, content, sip, fp))
            except Exception:
                pass

    def _on_broadcast(self, conn, addr, msg):
        sender = msg.get("from", "?")
        content = msg.get("content", "")
        mid = msg.get("msg_id")
        ttl = msg.get("ttl", 3)
        with self.lock:
            if mid in self.seen_messages:
                return
            self.seen_messages.add(mid)
        self.gui_queue.put(("broadcast_in", sender, content))
        if ttl > 0:
            with self.lock:
                fwd_peers = list(self.peer_list)
            fwd = dict(msg)
            fwd["ttl"] = ttl - 1
            for p in fwd_peers:
                if p["ip"] == addr[0]:
                    continue
                threading.Thread(target=self._send_direct,
                                 args=(p["ip"], p["port"], fwd), daemon=True).start()

    def _on_file(self, conn, addr, msg):
        sender = msg.get("from", "?")
        mid = msg.get("msg_id")
        try:
            fp = int(msg.get("from_port"))
        except (ValueError, TypeError):
            return
        fname = msg.get("filename", "file")
        sip = addr[0]
        with self.lock:
            key = self.shared_keys.get((sip, fp))
        ct, iv = msg.get("ciphertext"), msg.get("iv")
        if key and ct and iv:
            try:
                raw_ct = base64.b64decode(ct.encode())
                raw_iv = base64.b64decode(iv.encode())
                data = decrypt_bytes(raw_ct, key, raw_iv)
                os.makedirs("received", exist_ok=True)
                safe = os.path.basename(fname)
                path = os.path.join("received", safe)
                with open(path, "wb") as f:
                    f.write(data)
                if mid:
                    self._send_ack(conn, mid)
                self.gui_queue.put(("msg_in", sender, f"[File] {safe} (đã lưu)", sip, fp))
            except Exception:
                pass

    def _on_group_create(self, conn, msg):
        gn = msg.get("group_name")
        cr = msg.get("from", "?")
        cr_ip, cr_port = msg.get("from_ip"), int(msg.get("from_port", 0))
        mid = msg.get("msg_id")
        with self.lock:
            if gn not in self.groups:
                self.groups[gn] = {"members": [(cr_ip, cr_port, cr)], "creator": cr}
        if mid:
            self._send_ack(conn, mid)
        self.gui_queue.put(("log", f"Nhóm '{gn}' được tạo bởi [{cr}]."))
        self.gui_queue.put(("update_groups",))

    def _on_group_join(self, conn, msg):
        gn = msg.get("group_name")
        jr = msg.get("from", "?")
        jr_ip, jr_port = msg.get("from_ip"), int(msg.get("from_port", 0))
        mid = msg.get("msg_id")
        with self.lock:
            if gn in self.groups:
                ms = self.groups[gn]["members"]
                if not any(m[0] == jr_ip and m[1] == jr_port for m in ms):
                    ms.append((jr_ip, jr_port, jr))
        if mid:
            self._send_ack(conn, mid)
        self.gui_queue.put(("log", f"[{jr}] đã tham gia nhóm '{gn}'."))
        self.gui_queue.put(("update_groups",))

    def _on_group_leave(self, conn, msg):
        gn = msg.get("group_name")
        lv = msg.get("from", "?")
        lv_ip, lv_port = msg.get("from_ip"), int(msg.get("from_port", 0))
        mid = msg.get("msg_id")
        with self.lock:
            if gn in self.groups:
                self.groups[gn]["members"] = [
                    m for m in self.groups[gn]["members"]
                    if not (m[0] == lv_ip and m[1] == lv_port)]
        if mid:
            self._send_ack(conn, mid)
        self.gui_queue.put(("log", f"[{lv}] đã rời nhóm '{gn}'."))
        self.gui_queue.put(("update_groups",))

    def _on_group_msg(self, conn, addr, msg):
        gn = msg.get("group_name")
        sender = msg.get("from", "?")
        content = msg.get("content", "")
        mid = msg.get("msg_id")
        with self.lock:
            if mid in self.seen_messages:
                return
            self.seen_messages.add(mid)
        if mid:
            self._send_ack(conn, mid)
        self.gui_queue.put(("group_in", gn, sender, content))
        with self.lock:
            grp = self.groups.get(gn)
            if not grp:
                return
            members = list(grp["members"])
        for m_ip, m_port, _ in members:
            if m_ip == self.ip and m_port == self.port:
                continue
            if m_ip == addr[0]:
                continue
            threading.Thread(target=self._send_direct,
                             args=(m_ip, m_port, msg), daemon=True).start()

    def _on_group_sync(self, conn, msg):
        gn = msg.get("group_name")
        members = msg.get("members", [])
        creator = msg.get("creator", "?")
        mid = msg.get("msg_id")
        with self.lock:
            self.groups[gn] = {
                "members": [(m[0], int(m[1]), m[2]) for m in members],
                "creator": creator}
        if mid:
            self._send_ack(conn, mid)
        self.gui_queue.put(("update_groups",))

    # ======================== QUEUE PROCESSOR ========================

    def _process_queue(self):
        try:
            while True:
                item = self.gui_queue.get_nowait()
                action = item[0]

                if action == "msg_in":
                    sender, content, sip, sport = item[1], item[2], item[3], int(item[4])
                    key = (sip, sport)
                    self.peer_chats.setdefault(key, []).append(("in", sender, content))
                    if self.current_view == "peer" and self.current_peer_key == key:
                        self._add_bubble(content, sender=sender)

                elif action == "msg_out":
                    tname, content, tip, tport = item[1], item[2], item[3], int(item[4])
                    key = (tip, tport)
                    self.peer_chats.setdefault(key, []).append(("out", content))
                    if self.current_view == "peer" and self.current_peer_key == key:
                        self._add_bubble(content, is_outgoing=True)

                elif action == "broadcast_in":
                    sender, content = item[1], item[2]
                    self.broadcast_history.append(("broadcast_in", sender, content))
                    if self.current_view == "broadcast":
                        self._add_bubble(content, sender=sender)

                elif action == "broadcast_out":
                    content = item[1]
                    self.broadcast_history.append(("broadcast_out", content))
                    if self.current_view == "broadcast":
                        self._add_bubble(content, is_outgoing=True)

                elif action == "group_in":
                    gn, sender, content = item[1], item[2], item[3]
                    self.group_chats.setdefault(gn, []).append(("in", sender, content))
                    if self.current_view == "group" and self.current_group == gn:
                        self._add_bubble(content, sender=sender)

                elif action == "group_out":
                    gn, content = item[1], item[2]
                    self.group_chats.setdefault(gn, []).append(("out", content))
                    if self.current_view == "group" and self.current_group == gn:
                        self._add_bubble(content, is_outgoing=True)

                elif action == "log":
                    text = item[1]
                    self.broadcast_history.append(("system", text))
                    if self.current_view == "broadcast":
                        self._add_system(text)

                elif action == "peer_event":
                    text = item[1]
                    self.broadcast_history.append(("system", text))
                    if self.current_view == "broadcast":
                        self._add_system(text)

                elif action == "update_peers":
                    self._refresh_peer_sidebar()

                elif action == "update_groups":
                    self._refresh_group_sidebar()
                    if self.current_view == "group" and self.current_group:
                        self._select_group(self.current_group)

                self.gui_queue.task_done()
        except queue.Empty:
            pass

        if self.running or not hasattr(self, 'chat_container'):
            self.root.after(100, self._process_queue)


def main():
    root = tk.Tk()
    app = PeerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app._handle_leave)
    root.mainloop()


if __name__ == "__main__":
    main()
