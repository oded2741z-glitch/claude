import asyncio
import functools
import http.server
import json
import os
import socket
import struct
import threading

try:
    from websockets.asyncio.server import serve
except ImportError:
    from websockets.server import serve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GROUPS_FILE = os.path.join(BASE_DIR, "groups.txt")
HTTP_PORT = 8000
WS_PORT = 8001


class Peer:
    def __init__(self, pid, ws):
        self.id = pid
        self.ws = ws
        self.role = None
        self.name = ""
        self.group = ""


class State:
    def __init__(self):
        self.groups = []
        self.peers = {}
        self.broadcast = False
        self.active_groups = set()
        self.joined_group = None
        self.next_id = 1


st = State()


def load_groups():
    if not os.path.exists(GROUPS_FILE):
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            f.write("Team Alpha\nTeam Beta\nTeam Gamma\n")
    with open(GROUPS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def managers():
    return [p for p in st.peers.values() if p.role == "manager"]


def clients_in(group):
    return [p for p in st.peers.values() if p.role == "client" and p.group == group]


def status_for(peer):
    if st.broadcast:
        return "BROADCAST"
    if peer.group in st.active_groups or st.joined_group == peer.group:
        return "ACTIVE"
    return "STANDBY"


def audio_targets(sender):
    if st.broadcast:
        if sender.role == "manager":
            return [p for p in st.peers.values() if p.id != sender.id]
        return []
    if sender.role == "manager":
        if st.joined_group:
            return clients_in(st.joined_group)
        return []
    targets = []
    if sender.group in st.active_groups:
        targets += [p for p in clients_in(sender.group) if p.id != sender.id]
    if st.joined_group == sender.group:
        targets += managers()
    return targets


async def send_json(peer, payload):
    try:
        await peer.ws.send(json.dumps(payload))
    except Exception:
        pass


async def push_state():
    members = {g: [] for g in st.groups}
    for p in st.peers.values():
        if p.role == "client" and p.group in members:
            members[p.group].append(p.name)
    manager_payload = {
        "type": "state",
        "broadcast": st.broadcast,
        "active": sorted(st.active_groups),
        "joined": st.joined_group,
        "members": members,
    }
    tasks = []
    for p in list(st.peers.values()):
        if p.role == "manager":
            tasks.append(send_json(p, manager_payload))
        elif p.role == "client":
            tasks.append(send_json(p, {"type": "state", "status": status_for(p)}))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def handle_control(peer, data):
    kind = data.get("type")
    if kind == "hello":
        role = data.get("role")
        if role == "manager":
            peer.role = "manager"
            peer.name = "Commander"
        elif role == "client":
            group = data.get("group", "")
            if group not in st.groups:
                await send_json(peer, {"type": "error", "message": "Unknown team"})
                return
            peer.role = "client"
            peer.name = (data.get("name") or "Unnamed").strip()[:32]
            peer.group = group
        else:
            return
        await send_json(peer, {"type": "welcome", "id": peer.id, "role": peer.role})
        await push_state()
        return
    if peer.role != "manager":
        return
    if kind == "broadcast":
        st.broadcast = bool(data.get("on"))
    elif kind == "group":
        group = data.get("group")
        if group in st.groups:
            if data.get("on"):
                st.active_groups.add(group)
            else:
                st.active_groups.discard(group)
    elif kind == "join":
        group = data.get("group")
        if data.get("on") and group in st.groups:
            st.joined_group = group
        else:
            st.joined_group = None
    else:
        return
    await push_state()


async def handler(ws, path=None):
    peer = Peer(st.next_id, ws)
    st.next_id += 1
    st.peers[peer.id] = peer
    await send_json(peer, {"type": "groups", "groups": st.groups})
    try:
        async for message in ws:
            if isinstance(message, bytes):
                if peer.role is None:
                    continue
                frame = struct.pack("<I", peer.id) + message
                targets = audio_targets(peer)
                if targets:
                    await asyncio.gather(
                        *[t.ws.send(frame) for t in targets], return_exceptions=True
                    )
            else:
                try:
                    data = json.loads(message)
                except ValueError:
                    continue
                await handle_control(peer, data)
    except Exception:
        pass
    finally:
        st.peers.pop(peer.id, None)
        if peer.role == "manager" and not managers():
            st.joined_group = None
            st.broadcast = False
        await push_state()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass


def start_http():
    handler_class = functools.partial(QuietHandler, directory=BASE_DIR)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), handler_class)
    server.serve_forever()


async def main():
    st.groups = load_groups()
    threading.Thread(target=start_http, daemon=True).start()
    ip = get_local_ip()
    print("Intercom server running")
    print("Teams: " + ", ".join(st.groups))
    print("Client page:  http://%s:%d/client.html" % (ip, HTTP_PORT))
    print("Manager page: http://%s:%d/manager.html" % (ip, HTTP_PORT))
    print("WebSocket:    ws://%s:%d" % (ip, WS_PORT))
    async with serve(handler, "0.0.0.0", WS_PORT, max_size=None, ping_interval=20):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
