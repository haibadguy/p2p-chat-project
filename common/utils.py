import json
import socket

def send_json(sock, msg_dict):
    """
    Serializes a dictionary to JSON, appends a newline character,
    and sends it completely over the socket.
    """
    try:
        data = json.dumps(msg_dict) + "\n"
        sock.sendall(data.encode('utf-8'))
        return True
    except socket.error as e:
        print(f"[Utils] Error sending message: {e}")
        return False

def recv_json(sock, timeout=None):
    """
    Reads a single newline-terminated line from the socket,
    parses it as JSON, and returns the resulting dictionary.
    Returns None if connection is closed or an error occurs.
    """
    old_timeout = sock.gettimeout()
    if timeout is not None:
        sock.settimeout(timeout)
    
    try:
        # Using makefile('r') is highly robust for reading newline-delimited lines.
        # It handles internal OS-level buffering properly.
        f = sock.makefile('r', encoding='utf-8')
        line = f.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except (socket.timeout, socket.error) as e:
        # Silent timeout/error or print if needed
        return None
    except json.JSONDecodeError:
        print("[Utils] Received invalid JSON format.")
        return None
    finally:
        sock.settimeout(old_timeout)
