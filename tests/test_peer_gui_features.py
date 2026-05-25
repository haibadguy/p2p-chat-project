import base64
import socket
import threading
import time
from pathlib import Path

import tkinter as tk

from bootstrap_server import BootstrapServer
from common.encryption import encrypt_bytes, encrypt_string
from peer_gui import PeerGUI


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_gui():
    root = tk.Tk()
    root.withdraw()
    app = PeerGUI(root)
    return root, app


def stop_gui(root, app):
    app.running = False
    app.online = False
    try:
        if app.server_socket:
            app.server_socket.close()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


def test_gui_initial_state_and_view_switch():
    root, app = make_gui()
    try:
        app.login_frame.destroy()
        app._build_main()
        assert app.current_view == "broadcast"
        assert app.current_peer_key is None
        assert app.current_group is None

        app.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]
        app.groups = {"team": {"members": [("127.0.0.1", 5001, "alice")], "creator": "alice"}}

        app._select_peer("127.0.0.1", 5002, "bob")
        assert app.current_view == "peer"
        assert app.current_peer_key == ("127.0.0.1", 5002)
        assert app.current_peer_name == "bob"

        app._select_group("team")
        assert app.current_view == "group"
        assert app.current_group == "team"

        app._select_broadcast()
        assert app.current_view == "broadcast"
        assert app.current_peer_key is None
        assert app.current_group is None
    finally:
        stop_gui(root, app)


def test_gui_queue_processing_updates_histories():
    root, app = make_gui()
    try:
        app.login_frame.destroy()
        app._build_main()
        app.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]
        app.groups = {"team": {"members": [("127.0.0.1", 5001, "alice")], "creator": "alice"}}

        app.gui_queue.put(("msg_in", "bob", "hello", "127.0.0.1", 5002))
        app.gui_queue.put(("msg_out", "bob", "reply", "127.0.0.1", 5002))
        app.gui_queue.put(("broadcast_in", "bob", "all-hands"))
        app.gui_queue.put(("broadcast_out", "all-hands"))
        app.gui_queue.put(("group_in", "team", "bob", "group-msg"))
        app.gui_queue.put(("group_out", "team", "group-msg"))
        app.gui_queue.put(("log", "system-log"))
        app.gui_queue.put(("peer_event", "peer-event"))
        app.gui_queue.put(("update_peers",))
        app.gui_queue.put(("update_groups",))

        app._process_queue()

        assert app.peer_chats[("127.0.0.1", 5002)] == [("in", "bob", "hello"), ("out", "reply")]
        assert app.broadcast_history[0] == ("broadcast_in", "bob", "all-hands")
        assert app.broadcast_history[1] == ("broadcast_out", "all-hands")
        assert app.group_chats["team"] == [("in", "bob", "group-msg"), ("out", "group-msg")]
        assert ("system", "system-log") in app.broadcast_history
        assert ("system", "peer-event") in app.broadcast_history
    finally:
        stop_gui(root, app)


def test_gui_direct_chat_file_and_pending_delivery(tmp_path):
    bootstrap_port = free_port()
    server = BootstrapServer(host="127.0.0.1", port=bootstrap_port)
    server_thread = threading.Thread(target=server.start, daemon=True)
    server_thread.start()
    assert wait_for(lambda: server.server_socket is not None, timeout=2.0)

    sender_root, sender = make_gui()
    receiver_root, receiver = make_gui()

    try:
        sender.name = "alice"
        sender.ip = "127.0.0.1"
        sender.port = free_port()
        sender.bootstrap_host = "127.0.0.1"
        sender.bootstrap_port = bootstrap_port
        sender.running = True
        sender.online = True

        receiver.name = "bob"
        receiver.ip = "127.0.0.1"
        receiver.port = free_port()
        receiver.bootstrap_host = "127.0.0.1"
        receiver.bootstrap_port = bootstrap_port
        receiver.running = True
        receiver.online = True

        threading.Thread(target=receiver._start_server, daemon=True).start()
        assert wait_for(lambda: receiver.server_socket is not None, timeout=2.0)
        assert receiver._register_with_bootstrap() is True

        # Seed shared key from sender to receiver and send a direct chat.
        key = sender._ensure_shared_key("127.0.0.1", receiver.port)
        assert key is not None
        chat_ciphertext, chat_iv = encrypt_string("hello from gui", key)
        chat_msg = {
            "type": "chat",
            "from": sender.name,
            "from_ip": sender.ip,
            "from_port": sender.port,
            "ciphertext": chat_ciphertext,
            "iv": chat_iv,
            "msg_id": "gui-chat-1",
        }
        assert sender._send_with_ack("127.0.0.1", receiver.port, chat_msg) is True
        time.sleep(0.5)
        assert receiver._fetch_pending_messages() is True

        # File transfer through GUI path.
        file_bytes = b"gui-file-bytes"
        file_path = tmp_path / "gui_test.bin"
        file_path.write_bytes(file_bytes)
        file_ciphertext, file_iv = encrypt_bytes(file_bytes, key)
        file_msg = {
            "type": "file",
            "from": sender.name,
            "from_ip": sender.ip,
            "from_port": sender.port,
            "filename": file_path.name,
            "ciphertext": base64.b64encode(file_ciphertext).decode(),
            "iv": base64.b64encode(file_iv).decode(),
            "msg_id": "gui-file-1",
        }
        assert sender._send_with_ack("127.0.0.1", receiver.port, file_msg) is True
        received_path = Path("received") / file_path.name
        assert wait_for(lambda: received_path.exists(), timeout=2.5)
        assert received_path.read_bytes() == file_bytes
        received_path.unlink(missing_ok=True)

        # Offline store-and-forward path.
        receiver.running = False
        receiver.online = False
        try:
            if receiver.server_socket:
                receiver.server_socket.close()
        except Exception:
            pass
        time.sleep(0.3)

        offline_ciphertext, offline_iv = encrypt_string("offline gui hello", key)
        offline_msg = {
            "type": "chat",
            "from": sender.name,
            "from_ip": sender.ip,
            "from_port": sender.port,
            "ciphertext": offline_ciphertext,
            "iv": offline_iv,
            "msg_id": "gui-offline-1",
        }
        assert sender._store_offline_message("127.0.0.1", receiver.port, offline_msg) is True

        receiver.running = True
        receiver.online = True
        threading.Thread(target=receiver._start_server, daemon=True).start()
        assert wait_for(lambda: receiver.server_socket is not None, timeout=2.0)
        assert receiver._register_with_bootstrap() is True

        delivered_ids = []
        original = receiver._process_incoming_message

        def capture(conn, addr, msg):
            delivered_ids.append(msg.get("msg_id"))
            return original(conn, addr, msg)

        receiver._process_incoming_message = capture
        assert receiver._fetch_pending_messages() is True
        assert wait_for(lambda: "gui-offline-1" in delivered_ids, timeout=2.0)
    finally:
        stop_gui(sender_root, sender)
        stop_gui(receiver_root, receiver)
        try:
            server.stop()
        except Exception:
            pass


def test_gui_bootstrap_connection_failure_shows_error(monkeypatch):
    root, app = make_gui()
    errors = []
    try:
        monkeypatch.setattr(app, "_register_with_bootstrap", lambda: False)
        monkeypatch.setattr(
            "peer_gui.messagebox.showerror",
            lambda title, message: errors.append((title, message)),
        )

        app._var_name.set("alice")
        app._var_port.set("5001")
        app._var_bhost.set("127.0.0.1")
        app._var_bport.set("59999")

        app._handle_join()

        assert errors == [("Lỗi", "Không thể kết nối Bootstrap Server.")]
        assert app.running is False
    finally:
        stop_gui(root, app)


def test_gui_send_file_rejects_oversized_file(monkeypatch, tmp_path):
    root, app = make_gui()
    errors = []
    try:
        app.login_frame.destroy()
        app._build_main()
        app.current_view = "peer"
        app.current_peer_key = ("127.0.0.1", 5002)
        app.current_peer_name = "bob"
        app.running = True
        app.online = True
        app.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]

        big_file = tmp_path / "too_big.bin"
        big_file.write_bytes(b"x")

        monkeypatch.setattr("peer_gui.filedialog.askopenfilename", lambda: str(big_file))
        monkeypatch.setattr("peer_gui.os.path.getsize", lambda _path: 3 * 1024 * 1024)
        monkeypatch.setattr(
            "peer_gui.messagebox.showerror",
            lambda title, message: errors.append((title, message)),
        )

        app._send_file()

        assert errors == [("Lỗi", "File lớn hơn 2MB.")]
        assert ("log", "[Lỗi] Gửi file tới bob thất bại.") not in app.broadcast_history
    finally:
        stop_gui(root, app)


def test_gui_broadcast_logs_when_peer_list_empty():
    root, app = make_gui()
    try:
        app.login_frame.destroy()
        app._build_main()
        app.running = True
        app.online = True
        app.peer_list = []

        app._do_send_broadcast("hello everyone")
        time.sleep(0.2)
        app._process_queue()
        assert ("system", "Không có peer nào online.") in app.broadcast_history
    finally:
        stop_gui(root, app)


def test_gui_file_read_or_encrypt_error(monkeypatch, tmp_path):
    root, app = make_gui()
    try:
        app.login_frame.destroy()
        app._build_main()
        app.current_view = "peer"
        app.current_peer_key = ("127.0.0.1", 5002)
        app.current_peer_name = "bob"
        app.running = True
        app.online = True
        app.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]

        file_path = tmp_path / "will_exist.bin"
        file_path.write_bytes(b"data")

        # Ensure shared key is present so we reach encryption step
        monkeypatch.setattr(app, "_ensure_shared_key", lambda ip, port: b"dummykeybytes")
        # Simulate encryption failing
        monkeypatch.setattr("peer_gui.encrypt_bytes", lambda data, key: (_ for _ in ()).throw(Exception("enc error")))

        monkeypatch.setattr("peer_gui.filedialog.askopenfilename", lambda: str(file_path))

        app._send_file()
        time.sleep(0.2)
        app._process_queue()

        # There should be a log entry indicating encryption/read failure
        assert any(entry[0] == "system" and "Đọc/mã hóa file thất bại" in entry[1]
               for entry in app.broadcast_history)
    finally:
        stop_gui(root, app)


def test_gui_store_forward_rejected_logs_error(monkeypatch):
    root, app = make_gui()
    try:
        app.login_frame.destroy()
        app._build_main()
        app.current_view = "peer"
        app.current_peer_key = ("127.0.0.1", 5002)
        app.current_peer_name = "bob"
        app.running = True
        app.online = True
        app.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]

        # Ensure shared key is present so we reach send step
        monkeypatch.setattr(app, "_ensure_shared_key", lambda ip, port: b"dummy")
        # Simulate send failing
        monkeypatch.setattr(app, "_send_with_ack", lambda ip, port, msg: False)
        # Simulate bootstrap rejecting store-forward
        monkeypatch.setattr(app, "_store_offline_message", lambda ip, port, msg: False)

        # Trigger chat send
        app._do_send_chat("hello")
        time.sleep(0.2)
        app._process_queue()

        # Expect a log indicating failure to get ACK (store-forward rejected)
        assert any(entry[0] == "system" and "Không nhận được ACK" in entry[1]
               for entry in app.broadcast_history)
    finally:
        stop_gui(root, app)
