"""LITE INTERCOM WebRTC signaling server.

This server only brokers the connection setup: clients register a name and
exchange SDP offers/answers through it. Once the WebRTC peer connection is
established the audio flows directly between the two machines (P2P) and no
longer passes through here.

Run it on any machine both clients can reach:
    python signaling_server.py
"""

import json
import socket
import threading

HOST = "0.0.0.0"
PORT = 5000

clients = {}   # name -> file-like write stream
lock = threading.Lock()


def send_json(wfile, obj):
    try:
        wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
        wfile.flush()
    except Exception:
        pass


def relay(target, message):
    with lock:
        wfile = clients.get(target)
    if wfile is not None:
        send_json(wfile, message)
        return True
    return False


def handle_client(conn, addr):
    name = None
    rfile = conn.makefile("rb")
    wfile = conn.makefile("wb")
    try:
        for raw in rfile:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue

            mtype = msg.get("type")

            if mtype == "register":
                name = str(msg.get("name", "")).strip()
                if not name:
                    continue
                with lock:
                    clients[name] = wfile
                print(f"[+] {name} connected from {addr[0]}")
                # Tell the newcomer who else is online, and announce them.
                with lock:
                    others = [n for n in clients if n != name]
                send_json(wfile, {"type": "peers", "names": others})
                for other in others:
                    relay(other, {"type": "peer-joined", "name": name})
                continue

            # Every other message is simply forwarded to msg["to"].
            target = msg.get("to")
            if not target or not name:
                continue
            msg["from"] = name
            if mtype in ("call", "offer", "answer", "end", "busy"):
                if not relay(target, msg):
                    send_json(wfile, {"type": "unavailable", "name": target})
                if mtype in ("call", "offer"):
                    print(f"[>] {name} -> {target} ({mtype})")

    except Exception:
        pass
    finally:
        if name:
            with lock:
                if clients.get(name) is wfile:
                    del clients[name]
                others = list(clients)
            for other in others:
                relay(other, {"type": "peer-left", "name": name})
            print(f"[-] {name} disconnected")
        try:
            conn.close()
        except Exception:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(50)
    print(f"LITE INTERCOM signaling server listening on {HOST}:{PORT}")
    print("Audio itself is peer-to-peer; only call setup passes through here.")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
