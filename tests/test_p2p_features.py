import socket
import threading
import time
from pathlib import Path

import pytest

import peer as peer_module
from bootstrap_server import BootstrapServer
from common.encryption import encrypt_bytes, encrypt_string
from common.message import MSG_TYPE_BROADCAST
from peer import Peer


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


@pytest.fixture()
def bootstrap_server():
    port = free_port()
    server = BootstrapServer(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    assert wait_for(lambda: server.server_socket is not None, timeout=2.0)
    yield port, server
    server.stop()
    time.sleep(0.2)


@pytest.fixture()
def peer_factory(bootstrap_server):
    bootstrap_port, _ = bootstrap_server

    def make_peer(name, port=None):
        return Peer(
            ip="127.0.0.1",
            port=port or free_port(),
            name=name,
            bootstrap_host="127.0.0.1",
            bootstrap_port=bootstrap_port,
        )

    return make_peer


@pytest.fixture()
def started_peer(peer_factory):
    created = []

    def start_peer(name):
        peer = peer_factory(name)
        peer.running = True
        peer.online = True
        thread = threading.Thread(target=peer.start_server, daemon=True)
        thread.start()
        assert wait_for(lambda: peer.server_socket is not None, timeout=2.0)
        created.append(peer)
        return peer, thread

    yield start_peer

    for peer in created:
        peer.running = False
        peer.online = False
        try:
            if peer.server_socket:
                peer.server_socket.close()
        except Exception:
            pass
        time.sleep(0.05)


def test_register_and_get_peer_list(peer_factory, bootstrap_server):
    _, _ = bootstrap_server
    alice = peer_factory("alice")
    bob = peer_factory("bob")
    carol = peer_factory("carol")

    assert alice.register_with_bootstrap() is True
    assert bob.register_with_bootstrap() is True
    assert carol.register_with_bootstrap() is True

    assert alice.get_peer_list() is True
    names = {peer["name"] for peer in alice.peer_list}
    assert names == {"bob", "carol"}


def test_bootstrap_rejects_invalid_store_forward(bootstrap_server):
    bootstrap_port, _ = bootstrap_server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", bootstrap_port))
    assert peer_module.send_json(
        sock,
        {
            "type": "store_forward",
            "target_ip": "127.0.0.1",
            "message": {"type": "chat"},
        },
    )
    response = peer_module.recv_json(sock, timeout=5)
    sock.close()
    assert response is not None
    assert response.get("status") == "error"


def test_bootstrap_returns_empty_pending_batch(bootstrap_server):
    bootstrap_port, _ = bootstrap_server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", bootstrap_port))
    assert peer_module.send_json(
        sock,
        {"type": "get_pending", "ip": "127.0.0.1", "port": 9999},
    )
    response = peer_module.recv_json(sock, timeout=5)
    sock.close()
    assert response is not None
    assert response.get("type") == "pending_batch"
    assert response.get("messages") == []


def test_direct_chat_and_file_transfer(bootstrap_server, started_peer, capsys, tmp_path):
    alice, _ = started_peer("alice")
    bob, _ = started_peer("bob")

    assert alice.register_with_bootstrap() is True
    assert bob.register_with_bootstrap() is True

    shared_key = alice.ensure_shared_key("127.0.0.1", bob.port)
    assert shared_key is not None

    ciphertext, iv = encrypt_string("hello bob", shared_key)
    chat_msg = {
        "type": "chat",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "ciphertext": ciphertext,
        "iv": iv,
        "msg_id": "chat-ack-1",
    }
    assert alice.send_with_ack("127.0.0.1", bob.port, chat_msg) is True
    time.sleep(0.5)
    out = capsys.readouterr().out
    assert "hello bob" in out

    file_bytes = b"test-file-bytes"
    source = tmp_path / "sample.bin"
    source.write_bytes(file_bytes)

    file_ciphertext, file_iv = encrypt_bytes(file_bytes, shared_key)
    file_msg = {
        "type": "file",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "filename": source.name,
        "ciphertext": __import__("base64").b64encode(file_ciphertext).decode(),
        "iv": __import__("base64").b64encode(file_iv).decode(),
        "msg_id": "file-ack-1",
    }
    assert alice.send_with_ack("127.0.0.1", bob.port, file_msg) is True
    received_path = Path("received") / source.name
    assert wait_for(lambda: received_path.exists(), timeout=2.5)
    assert received_path.read_bytes() == file_bytes
    received_path.unlink(missing_ok=True)


def test_store_forward_offline_message_is_delivered(bootstrap_server, started_peer, capsys):
    alice, _ = started_peer("alice")
    bob, _ = started_peer("bob")

    assert alice.register_with_bootstrap() is True
    assert bob.register_with_bootstrap() is True

    shared_key = alice.ensure_shared_key("127.0.0.1", bob.port)
    assert shared_key is not None

    bob.running = False
    bob.online = False
    try:
        if bob.server_socket:
            bob.server_socket.close()
    except Exception:
        pass
    time.sleep(0.3)

    ciphertext, iv = encrypt_string("offline hello", shared_key)
    offline_msg = {
        "type": "chat",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "ciphertext": ciphertext,
        "iv": iv,
        "msg_id": "offline-chat-1",
    }

    assert alice.send_with_ack("127.0.0.1", bob.port, offline_msg, max_retries=1) is False
    assert alice._store_offline_message("127.0.0.1", bob.port, offline_msg) is True

    bob.running = True
    bob.online = True
    threading.Thread(target=bob.start_server, daemon=True).start()
    assert wait_for(lambda: bob.server_socket is not None, timeout=2.0)

    assert bob.register_with_bootstrap() is True
    assert bob.fetch_pending_messages() is True
    time.sleep(0.5)

    out = capsys.readouterr().out
    assert "offline hello" in out


def test_broadcast_deduplicates_repeated_message(peer_factory):
    peer = peer_factory("dedupe")
    msg = {
        "type": MSG_TYPE_BROADCAST,
        "from": "alice",
        "content": "hello all",
        "msg_id": "dup-1",
        "ttl": 2,
    }

    peer._process_incoming_message(None, ("127.0.0.1", 12345), msg)
    peer._process_incoming_message(None, ("127.0.0.1", 12345), msg)
    assert peer.seen_messages == {"dup-1"}


def test_churn_loop_leaves_and_reregisters(monkeypatch, peer_factory):
    peer = peer_factory("churner")
    peer.running = True
    peer.online = True

    actions = []

    monkeypatch.setattr(peer_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(peer, "leave_network", lambda: actions.append("leave"))
    monkeypatch.setattr(peer, "register_with_bootstrap", lambda: actions.append("register") or True)
    monkeypatch.setattr(peer, "get_peer_list", lambda: actions.append("get") or True)
    monkeypatch.setattr(peer, "fetch_pending_messages", lambda: actions.append("fetch") or True)

    peer.churn_loop(online_seconds=0, offline_seconds=0, cycles=1)

    assert actions == ["leave", "register", "get", "fetch"]


def test_send_command_reports_when_no_shared_key(peer_factory, capsys):
    # Prepare a peer with a resolved peer entry
    peer = peer_factory("sender")
    peer.peer_list = [{"name": "bob", "ip": "127.0.0.1", "port": 5002}]
    peer.running = True
    peer.online = True

    # Force ensure_shared_key to fail by overriding the instance method
    peer.ensure_shared_key = lambda ip, port: None

    peer.handle_send_command("1", "hello no key")
    out = capsys.readouterr().out
    assert "Từ chối gửi tin nhắn" in out or "không thiết lập được kênh bảo mật" in out
