"""LITE INTERCOM relay server.

Both clients connect OUT to this server, so there is no need for NAT
traversal or port forwarding on the clients themselves. The server relays
control messages (call / end) and audio between paired clients by name.

Run this on a host both clients can reach: a cheap VPS, one machine with
TCP port 5000 forwarded on its router, or any machine on a shared VPN.
"""

import socket
import struct
import threading

HOST = "0.0.0.0"
PORT = 5000

# Wire protocol: each message is a 4-byte big-endian length followed by the
# payload. Payload byte 0 is the message type, the rest is the data.
MSG_REGISTER = 1        # client -> server: data = client name
MSG_CALL = 2            # client -> server: data = target name
MSG_END = 3            # client -> server: end the current call
MSG_AUDIO = 4          # both ways: data = raw int16 mono audio
MSG_INCOMING_CALL = 5  # server -> client: data = caller name
MSG_CALL_ENDED = 6     # server -> client: the peer ended the call

clients = {}   # name -> connection
pairs = {}     # name -> peer name (who they are in a call with)
lock = threading.Lock()


def send_frame(conn, mtype, data=b""):
    if isinstance(data, str):
        data = data.encode("utf-8")
    payload = bytes([mtype]) + data
    try:
        conn.sendall(struct.pack(">I", len(payload)) + payload)
    except Exception:
        pass


def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def recv_frame(conn):
    header = recv_exact(conn, 4)
    if not header:
        return None, None
    (length,) = struct.unpack(">I", header)
    if length <= 0 or length > 10_000_000:
        return None, None
    payload = recv_exact(conn, length)
    if payload is None:
        return None, None
    return payload[0], payload[1:]


def end_pair(name):
    with lock:
        peer = pairs.pop(name, None)
        if peer is not None:
            pairs.pop(peer, None)
            pconn = clients.get(peer)
        else:
            pconn = None
    if pconn is not None:
        send_frame(pconn, MSG_CALL_ENDED)


def handle_client(conn, addr):
    name = None
    try:
        while True:
            mtype, data = recv_frame(conn)
            if mtype is None:
                break

            if mtype == MSG_REGISTER:
                name = data.decode("utf-8", "ignore").strip()
                with lock:
                    clients[name] = conn
                print(f"[+] {name} connected from {addr[0]}")

            elif mtype == MSG_CALL:
                target = data.decode("utf-8", "ignore").strip()
                with lock:
                    tconn = clients.get(target)
                    busy = (target in pairs) or (name in pairs)
                    if tconn is not None and not busy and target != name:
                        pairs[name] = target
                        pairs[target] = name
                if tconn is not None and target != name:
                    send_frame(tconn, MSG_INCOMING_CALL, name)
                    print(f"[>] {name} calling {target}")

            elif mtype == MSG_END:
                end_pair(name)

            elif mtype == MSG_AUDIO:
                with lock:
                    peer = pairs.get(name)
                    pconn = clients.get(peer) if peer else None
                if pconn is not None:
                    send_frame(pconn, MSG_AUDIO, data)

    except Exception:
        pass
    finally:
        if name:
            end_pair(name)
            with lock:
                if clients.get(name) is conn:
                    del clients[name]
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
    print(f"LITE INTERCOM relay server listening on {HOST}:{PORT}")
    while True:
        conn, addr = srv.accept()
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
