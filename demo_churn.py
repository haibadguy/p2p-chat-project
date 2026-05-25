import argparse
import base64
import socket
import threading
import time
from pathlib import Path

from bootstrap_server import BootstrapServer
from common.encryption import encrypt_bytes, encrypt_string
from peer import Peer


def wait_for(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start_peer(peer):
    peer.running = True
    peer.online = True
    thread = threading.Thread(target=peer.start_server, daemon=True)
    thread.start()
    wait_for(lambda: peer.server_socket is not None, timeout=2)
    return thread


def stop_peer(peer):
    peer.running = False
    peer.online = False
    try:
        if peer.server_socket:
            peer.server_socket.close()
    except Exception:
        pass
    time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="P2P churn and store-and-forward demo")
    parser.add_argument("--online-seconds", type=int, default=3)
    parser.add_argument("--offline-seconds", type=int, default=3)
    parser.add_argument("--port-base", type=int, default=5800)
    args = parser.parse_args()

    bootstrap_port = args.port_base
    alice_port = args.port_base + 1
    bob_port = args.port_base + 2
    carol_port = args.port_base + 3

    print(f"[Demo] Starting Bootstrap on 127.0.0.1:{bootstrap_port}")
    bootstrap = BootstrapServer(host="127.0.0.1", port=bootstrap_port)
    bootstrap_thread = threading.Thread(target=bootstrap.start, daemon=True)
    bootstrap_thread.start()
    if not wait_for(lambda: bootstrap.server_socket is not None, timeout=2):
        raise SystemExit("Bootstrap did not start")

    alice = Peer("127.0.0.1", alice_port, "alice", bootstrap_host="127.0.0.1", bootstrap_port=bootstrap_port)
    bob = Peer("127.0.0.1", bob_port, "bob", bootstrap_host="127.0.0.1", bootstrap_port=bootstrap_port)
    carol = Peer("127.0.0.1", carol_port, "carol", bootstrap_host="127.0.0.1", bootstrap_port=bootstrap_port)

    for peer in (alice, bob, carol):
        start_peer(peer)

    print("[Demo] Registering peers")
    for peer in (alice, bob, carol):
        if not peer.register_with_bootstrap():
            raise SystemExit(f"Failed to register {peer.name}")

    print("[Demo] Direct chat/file to Bob")
    shared_key = alice.ensure_shared_key("127.0.0.1", bob.port)
    if not shared_key:
        raise SystemExit("Failed to establish shared key with Bob")

    chat_ciphertext, chat_iv = encrypt_string("Hello Bob from Alice", shared_key)
    chat_msg = {
        "type": "chat",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "ciphertext": chat_ciphertext,
        "iv": chat_iv,
        "msg_id": "demo-chat-1",
    }
    print(f"[Demo] Chat ACK: {alice.send_with_ack('127.0.0.1', bob.port, chat_msg)}")

    demo_file = Path("demo_payload.txt")
    demo_file.write_bytes(b"demo payload for file transfer")
    file_bytes = demo_file.read_bytes()
    file_ciphertext, file_iv = encrypt_bytes(file_bytes, shared_key)
    file_msg = {
        "type": "file",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "filename": demo_file.name,
        "ciphertext": base64.b64encode(file_ciphertext).decode(),
        "iv": base64.b64encode(file_iv).decode(),
        "msg_id": "demo-file-1",
    }
    print(f"[Demo] File ACK: {alice.send_with_ack('127.0.0.1', bob.port, file_msg)}")

    received_path = Path("received") / demo_file.name
    print(f"[Demo] File saved locally: {received_path.exists()}")

    print("[Demo] Taking Carol offline and queuing a pending chat")
    carol_key = alice.ensure_shared_key("127.0.0.1", carol.port)
    if not carol_key:
        raise SystemExit("Failed to establish shared key with Carol")

    pending_messages = []
    original_carol_dispatch = carol._process_incoming_message

    def capture_pending(conn, addr, msg):
        pending_messages.append(msg.get("msg_id"))
        return original_carol_dispatch(conn, addr, msg)

    carol._process_incoming_message = capture_pending

    churn_thread = threading.Thread(
        target=carol.churn_loop,
        kwargs={
            "online_seconds": args.online_seconds,
            "offline_seconds": args.offline_seconds,
            "cycles": 1,
        },
        daemon=True,
    )
    churn_thread.start()

    if not wait_for(lambda: not carol.online, timeout=args.online_seconds + 2):
        raise SystemExit("Carol never went offline")

    offline_ciphertext, offline_iv = encrypt_string("Message stored while Carol is offline", carol_key)
    offline_msg = {
        "type": "chat",
        "from": alice.name,
        "from_ip": alice.ip,
        "from_port": alice.port,
        "ciphertext": offline_ciphertext,
        "iv": offline_iv,
        "msg_id": "demo-offline-1",
    }
    print(f"[Demo] Store-and-forward queued: {alice._store_offline_message('127.0.0.1', carol.port, offline_msg)}")

    churn_thread.join(timeout=args.online_seconds + args.offline_seconds + 5)
    print(f"[Demo] Carol rejoined and processed pending IDs: {pending_messages}")

    for peer in (alice, bob, carol):
        stop_peer(peer)

    try:
        bootstrap.stop()
    except Exception:
        pass

    try:
        demo_file.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        received_path.unlink(missing_ok=True)
    except Exception:
        pass

    print("[Demo] Finished")


if __name__ == "__main__":
    main()
