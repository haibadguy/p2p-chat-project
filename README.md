# ELITE P2P CHAT — Technical README

Một ứng dụng chat ngang hàng (P2P) viết bằng Python 3, sử dụng mô hình Bootstrap-assisted P2P và hỗ trợ E2EE (Diffie-Hellman + symmetric keystream). README này tập trung vào hướng dẫn kỹ thuật: cài đặt, chạy, cấu trúc mã và các giao thức nội bộ.

---

## Table of Contents
- Project overview
- Features
- Architecture
- Protocol (JSON messages)
- Requirements
- Setup & Run
- Project structure
- Development & Testing
- Contributing

---

## Project overview

This repository implements a peer-to-peer chat system with:
- A Bootstrap server that tracks online peers (TCP, default port 5555).
- Peer nodes that act as both TCP servers and clients for direct E2EE chat/file transfer.
- Two UX modes: CLI (`peer.py`) and GUI (`peer_gui.py` using Tkinter/ttk).

## Features
- End-to-end encryption (Diffie-Hellman key exchange + symmetric keystream encryption).
- Reliable delivery primitives (ACK/retry) and group/broadcast messaging.
- File transfer over encrypted channels.

## Architecture (high level)

Bootstrap Server (TCP:5555)
  └─ maintains peer registry, handles `register/heartbeat/leave`
Peers (CLI or GUI)
  ├─ perform DH key exchange on first contact
  ├─ exchange JSON messages over TCP with newline delimiters
  └─ send encrypted payloads (base64) + iv + msg_id

## Protocol (JSON messages)

All messages are JSON terminated by `\n`.

Key examples:

`key_exchange` (initiates DH):
```json
{
  "type": "key_exchange",
  "from": "alice",
  "from_ip": "127.0.0.1",
  "from_port": 5001,
  "pub_key": "..."
}
```

`chat` (encrypted message):
```json
{
  "type": "chat",
  "from": "alice",
  "ciphertext": "<base64>",
  "iv": "<base64>",
  "msg_id": "uuid"
}
```

`file` (encrypted file chunk or whole file):
```json
{
  "type": "file",
  "from": "alice",
  "filename": "document.pdf",
  "ciphertext": "<base64>",
  "iv": "<base64>",
  "msg_id": "uuid"
}
```

## Requirements
- Python 3.8+ (3.10 recommended)
- No third-party packages required (pure stdlib), but install virtualenv if desired.

## Setup & Run

1. Clone repository and ensure you are on `main`:

```powershell
git fetch origin
git checkout main
git reset --hard origin/main
```

2. Start the Bootstrap server (default port 5555):

```powershell
py bootstrap_server.py
```

3. Start one or more peers:

- GUI mode (recommended):
```powershell
py peer_gui.py
```

- CLI mode:
```powershell
py peer.py --name Alice --port 5001
```

Notes:
- When using GUI, enter display name and listening port in the form.
- Received files are saved under the project `received/` directory.

## Project structure

- `bootstrap_server.py` — minimal registry server for peer discovery
- `peer.py` — CLI peer implementation and command set
- `peer_gui.py` — Tkinter-based GUI peer
- `common/` — shared modules (`encryption.py`, `message.py`, `utils.py`)
- `received/` — directory where incoming files are stored

## Development & Testing

- Run unit checks (if added) with `pytest` (not included by default).
- Linting: use `ruff`/`flake8` locally for style (optional).

## Contributing

- Create feature branches from `main`.
- Keep commits atomic and signpost scope (e.g., `feat:`, `fix:`, `docs:`).

## License & Contact

See repository metadata for license. For questions or support, contact the maintainers.

---

Files received by peers are saved in `received/` relative to the project root.
