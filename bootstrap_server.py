import socket
import threading
import json
import time
import sys

# Import shared modules
from common.message import (
    MSG_TYPE_REGISTER,
    MSG_TYPE_PEER_LIST,
    MSG_TYPE_GET_PEERS,
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_LEAVE
)
from common.utils import send_json, recv_json

class BootstrapServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host = host
        self.port = port
        self.peers = []              # List of dicts: {"ip": str, "port": int, "name": str, "last_heartbeat": float}
        self.lock = threading.Lock()  # Synchronize access to self.peers
        self.running = True
        self.server_socket = None

    def start(self):
        """
        Starts the TCP Bootstrap Server and background threads.
        """
        # 1. Start background sweeper thread
        sweeper = threading.Thread(target=self.sweep_inactive_peers, daemon=True)
        sweeper.start()

        # 2. Setup TCP Socket
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            print(f"[Bootstrap] Server running on {self.host}:{self.port}")
        except socket.error as e:
            print(f"[Bootstrap] Failed to bind to {self.host}:{self.port} - {e}")
            sys.exit(1)

        # 3. Main Accept Loop
        try:
            while self.running:
                conn, addr = self.server_socket.accept()
                # Spawn a daemon thread to handle each client connection
                client_thread = threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True)
                client_thread.start()
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
        """
        Handles incoming requests from a single client connection.
        """
        # Print connection log
        # print(f"[Bootstrap] Connection accepted from {addr[0]}:{addr[1]}")
        
        try:
            # Receive newline-delimited JSON message
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

    def handle_register(self, conn, msg, addr):
        """
        Registers a new peer node in the active peers list.
        """
        ip = msg.get("ip")
        port = msg.get("port")
        name = msg.get("name")

        if not ip or not port or not name:
            send_json(conn, {"status": "error", "message": "Missing registration details (ip, port, name)"})
            return

        # Ensure correct data types
        try:
            port = int(port)
        except ValueError:
            send_json(conn, {"status": "error", "message": "Port must be an integer"})
            return

        with self.lock:
            # Check if peer is already registered
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
                print(f"[Bootstrap] Registered new peer: {name} ({ip}:{port})")

        # Confirm registration success
        send_json(conn, {"status": "success", "message": "Registered successfully"})

    def handle_get_peers(self, conn, msg, addr):
        """
        Returns the list of active peers, excluding the peer making the request.
        """
        requester_ip = msg.get("ip")
        requester_port = msg.get("port")

        if requester_port is not None:
            try:
                requester_port = int(requester_port)
            except ValueError:
                pass

        with self.lock:
            # Filter out the requester peer from the peer list
            filtered_peers = []
            for p in self.peers:
                # Exclude based on IP and port if provided by requester
                if requester_ip and requester_port:
                    if p["ip"] == requester_ip and p["port"] == requester_port:
                        continue
                filtered_peers.append({
                    "ip": p["ip"],
                    "port": p["port"],
                    "name": p["name"]
                })

        # Send the peer list back
        response = {
            "type": MSG_TYPE_PEER_LIST,
            "peers": filtered_peers
        }
        send_json(conn, response)

    def handle_heartbeat(self, conn, msg):
        """
        Updates the last_heartbeat timestamp for the requesting peer.
        """
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
        """
        Explicitly removes a peer from the registered peer list.
        """
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
        else:
            send_json(conn, {"status": "error", "message": "Peer not registered"})

    def sweep_inactive_peers(self):
        """
        Background daemon thread that sweeps inactive peers every 10 seconds.
        Removes any peer that hasn't sent a heartbeat in over 60 seconds.
        """
        while self.running:
            time.sleep(10)
            now = time.time()
            with self.lock:
                inactive_peers = []
                for p in self.peers:
                    if now - p["last_heartbeat"] > 60:
                        inactive_peers.append(p)
                
                for p in inactive_peers:
                    self.peers.remove(p)
                    print(f"[Bootstrap] Peer timed out (no heartbeat for 60s): {p['name']} ({p['ip']}:{p['port']})")

if __name__ == "__main__":
    server = BootstrapServer()
    server.start()
