import socket
import threading
import json
import time
import sys

from common.message import (
    MSG_TYPE_REGISTER,
    MSG_TYPE_PEER_LIST,
    MSG_TYPE_GET_PEERS,
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_LEAVE,
    MSG_TYPE_PEER_JOINED,
    MSG_TYPE_PEER_LEFT
)
from common.utils import send_json, recv_json


class BootstrapServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host = host
        self.port = port
        self.peers = []
        self.lock = threading.Lock()
        self.running = True
        self.server_socket = None

    def start(self):
        sweeper = threading.Thread(target=self.sweep_inactive_peers, daemon=True)
        sweeper.start()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(10)
            print(f"[Bootstrap] Server running on {self.host}:{self.port}")
        except socket.error as e:
            print(f"[Bootstrap] Failed to bind to {self.host}:{self.port} - {e}")
            sys.exit(1)

        try:
            while self.running:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[Bootstrap] KeyboardInterrupt received. Shutting down server...")
        except Exception as e:
            if self.running:
                print(f"[Bootstrap] Error in accept loop: {e}")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        print("[Bootstrap] Server stopped.")

    def handle_client(self, conn, addr):
        try:
            msg = recv_json(conn, timeout=5)
            if not msg:
                return

            msg_type = msg.get("type")
            if not msg_type:
                send_json(conn, {"status": "error", "message": "Missing message type"})
                return

            if msg_type == MSG_TYPE_REGISTER:
                self.handle_register(conn, msg, addr)
            elif msg_type == MSG_TYPE_GET_PEERS:
                self.handle_get_peers(conn, msg, addr)
            elif msg_type == MSG_TYPE_HEARTBEAT:
                self.handle_heartbeat(conn, msg)
            elif msg_type == MSG_TYPE_LEAVE:
                self.handle_leave(conn, msg)
            else:
                send_json(conn, {"status": "error", "message": f"Unsupported message type: {msg_type}"})

        except Exception as e:
            print(f"[Bootstrap] Exception handling client {addr}: {e}")
        finally:
            conn.close()

    def _notify_all_peers(self, notification, exclude_ip=None, exclude_port=None):
        """Push a notification to all registered peers (except the excluded one)."""
        with self.lock:
            targets = [
                (p["ip"], p["port"]) for p in self.peers
                if not (p["ip"] == exclude_ip and p["port"] == exclude_port)
            ]

        for t_ip, t_port in targets:
            threading.Thread(target=self._push_notify, args=(t_ip, t_port, notification), daemon=True).start()

    def _push_notify(self, ip, port, msg_dict):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((ip, int(port)))
            send_json(s, msg_dict)
        except Exception:
            pass
        finally:
            s.close()

    def handle_register(self, conn, msg, addr):
        ip = msg.get("ip")
        port = msg.get("port")
        name = msg.get("name")

        if not ip or not port or not name:
            send_json(conn, {"status": "error", "message": "Missing registration details (ip, port, name)"})
            return

        try:
            port = int(port)
        except ValueError:
            send_json(conn, {"status": "error", "message": "Port must be an integer"})
            return

        is_new = False
        with self.lock:
            existing_peer = None
            for p in self.peers:
                if p["ip"] == ip and p["port"] == port:
                    existing_peer = p
                    break

            if existing_peer:
                existing_peer["name"] = name
                existing_peer["last_heartbeat"] = time.time()
                print(f"[Bootstrap] Updated existing peer: {name} ({ip}:{port})")
            else:
                self.peers.append({
                    "ip": ip,
                    "port": port,
                    "name": name,
                    "last_heartbeat": time.time()
                })
                is_new = True
                print(f"[Bootstrap] Registered new peer: {name} ({ip}:{port})")

        send_json(conn, {"status": "success", "message": "Registered successfully"})

        if is_new:
            self._notify_all_peers(
                {"type": MSG_TYPE_PEER_JOINED, "peer_name": name, "peer_ip": ip, "peer_port": port},
                exclude_ip=ip, exclude_port=port
            )

    def handle_get_peers(self, conn, msg, addr):
        requester_ip = msg.get("ip")
        requester_port = msg.get("port")

        if requester_port is not None:
            try:
                requester_port = int(requester_port)
            except ValueError:
                pass

        with self.lock:
            filtered_peers = []
            for p in self.peers:
                if requester_ip and requester_port:
                    if p["ip"] == requester_ip and p["port"] == requester_port:
                        continue
                filtered_peers.append({
                    "ip": p["ip"],
                    "port": p["port"],
                    "name": p["name"]
                })

        response = {
            "type": MSG_TYPE_PEER_LIST,
            "peers": filtered_peers
        }
        send_json(conn, response)

    def handle_heartbeat(self, conn, msg):
        ip = msg.get("ip")
        port = msg.get("port")

        if not ip or port is None:
            send_json(conn, {"status": "error", "message": "Missing heartbeat details (ip, port)"})
            return

        try:
            port = int(port)
        except ValueError:
            send_json(conn, {"status": "error", "message": "Port must be an integer"})
            return

        updated = False
        with self.lock:
            for p in self.peers:
                if p["ip"] == ip and p["port"] == port:
                    p["last_heartbeat"] = time.time()
                    updated = True
                    break

        if updated:
            send_json(conn, {"status": "success", "message": "Heartbeat updated"})
        else:
            send_json(conn, {"status": "error", "message": "Peer not registered"})

    def handle_leave(self, conn, msg):
        ip = msg.get("ip")
        port = msg.get("port")

        if not ip or port is None:
            send_json(conn, {"status": "error", "message": "Missing leave details (ip, port)"})
            return

        try:
            port = int(port)
        except ValueError:
            send_json(conn, {"status": "error", "message": "Port must be an integer"})
            return

        removed = False
        peer_name = ""
        with self.lock:
            for i, p in enumerate(self.peers):
                if p["ip"] == ip and p["port"] == port:
                    peer_name = p["name"]
                    self.peers.pop(i)
                    removed = True
                    break

        if removed:
            print(f"[Bootstrap] Peer left network: {peer_name} ({ip}:{port})")
            send_json(conn, {"status": "success", "message": "Removed successfully"})
            self._notify_all_peers(
                {"type": MSG_TYPE_PEER_LEFT, "peer_name": peer_name, "peer_ip": ip, "peer_port": port},
                exclude_ip=ip, exclude_port=port
            )
        else:
            send_json(conn, {"status": "error", "message": "Peer not registered"})

    def sweep_inactive_peers(self):
        """Removes peers with no heartbeat in 30 seconds. Checks every 5 seconds."""
        while self.running:
            time.sleep(5)
            now = time.time()
            timed_out = []
            with self.lock:
                inactive_peers = [p for p in self.peers if now - p["last_heartbeat"] > 30]
                for p in inactive_peers:
                    self.peers.remove(p)
                    timed_out.append(p)

            for p in timed_out:
                print(f"[Bootstrap] Peer timed out (no heartbeat for 30s): {p['name']} ({p['ip']}:{p['port']})")
                self._notify_all_peers(
                    {"type": MSG_TYPE_PEER_LEFT, "peer_name": p["name"], "peer_ip": p["ip"], "peer_port": p["port"]}
                )


if __name__ == "__main__":
    server = BootstrapServer()
    server.start()
