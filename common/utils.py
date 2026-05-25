import json
import socket
import time


def get_local_ip(fallback="127.0.0.1"):
    """Best-effort detection of the machine's LAN IP address."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        try:
            probe.close()
        except Exception:
            pass

    try:
        hostname = socket.gethostname()
        for addr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            candidate = addr[4][0]
            if candidate and not candidate.startswith("127."):
                return candidate
    except Exception:
        pass

    return fallback

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
        f = sock.makefile('r', encoding='utf-8')
        line = f.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except (socket.timeout, socket.error):
        return None
    except json.JSONDecodeError:
        print("[Utils] Received invalid JSON format.")
        return None
    finally:
        sock.settimeout(old_timeout)

def send_reliable(target_ip, target_port, msg_dict, max_retries=3, timeout=5):
    """
    Sends a JSON message and waits for an ACK response from the receiver.
    Retries up to max_retries times with exponential backoff on failure.
    Returns (success: bool, ack_data: dict or None).
    """
    for attempt in range(max_retries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((target_ip, int(target_port)))
            if send_json(s, msg_dict):
                ack = recv_json(s, timeout=timeout)
                if ack and ack.get("type") == "ack" and ack.get("msg_id") == msg_dict.get("msg_id"):
                    return True, ack
        except Exception:
            pass
        finally:
            s.close()
        backoff = min(2 ** attempt, 8)
        time.sleep(backoff)
    return False, None
