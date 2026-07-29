"""LITE INTERCOM - WebRTC edition.

Audio is carried by a real WebRTC peer connection (Opus codec, jitter
buffering, DTLS-SRTP encryption) instead of raw UDP. A small signaling server
(signaling_server.py) only brokers call setup; the audio itself flows directly
between the two machines.

Requirements:
    pip install aiortc sounddevice numpy customtkinter av

Configuration lives in ip_list.txt:
    CONFIG: SERVER, 192.168.0.10, 5000
    CONFIG: NAME, PC1
    CONFIG: TITLE, LITE INTERCOM
    PC2
"""

import asyncio
import collections
import fractions
import json
import os
import queue
import socket
import threading

import av
import customtkinter as ctk
import numpy as np
import sounddevice as sd
from aiortc import (
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

ctk.set_appearance_mode("Dark")

COLORS = {
    "BG_MAIN": "#121212",
    "ACCENT": "#389379",
    "TEXT_WHITE": "#FFFFFF",
    "BTN_BASE": "#333333",
    "BTN_QUIT_HOVER": "#FF0000",
    "ME_BTN": "#252525",
    "OFFLINE": "#777777",
}

IP_LIST_FILE = "ip_list.txt"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 5000

# WebRTC/Opus works natively at 48 kHz; 20 ms frames are the Opus default.
SAMPLE_RATE = 48000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960
CHANNELS = 1


class MicrophoneTrack(MediaStreamTrack):
    """Feeds microphone audio into the peer connection as 20 ms Opus frames."""

    kind = "audio"

    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=50)
        self._pts = 0
        self._time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._stream = None
        self._start_stream()

    def _start_stream(self):
        def callback(indata, frames, time_info, status):
            # Runs on the PortAudio thread: hand the block over to the event
            # loop without blocking the audio callback.
            data = bytes(indata)
            try:
                self.loop.call_soon_threadsafe(self._push, data)
            except RuntimeError:
                pass

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=SAMPLES_PER_FRAME,
            callback=callback,
        )
        self._stream.start()

    def _push(self, data):
        if self.queue.full():
            # Drop the oldest block so latency does not creep up.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(data)

    async def recv(self):
        data = await self.queue.get()
        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(data)
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += SAMPLES_PER_FRAME
        return frame

    def stop(self):
        super().stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class SpeakerPlayer:
    """Plays incoming WebRTC audio frames through the default output device."""

    def __init__(self):
        self.buffer = collections.deque(maxlen=25)
        self._stream = None
        self._resampler = av.AudioResampler(
            format="s16", layout="mono", rate=SAMPLE_RATE
        )

    def start(self):
        def callback(outdata, frames, time_info, status):
            try:
                if self.buffer:
                    chunk = self.buffer.popleft()
                    n = min(len(chunk), frames)
                    outdata[:n, 0] = chunk[:n]
                    if n < frames:
                        outdata[n:, 0] = 0
                else:
                    outdata.fill(0)
            except Exception:
                outdata.fill(0)

        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=SAMPLES_PER_FRAME,
            callback=callback,
        )
        self._stream.start()

    def feed(self, frame):
        try:
            resampled = self._resampler.resample(frame)
        except Exception:
            return
        if not isinstance(resampled, list):
            resampled = [resampled]
        for item in resampled:
            if item is None:
                continue
            samples = item.to_ndarray().reshape(-1).astype(np.int16)
            self.buffer.append(samples)

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.buffer.clear()


class LiteIntercomApp:
    def __init__(self, root):
        self.root = root
        self.root.geometry("280x450+0+0")
        self.root.configure(fg_color=COLORS["BG_MAIN"])
        self.root.title("LITE INTERCOM")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.my_name = socket.gethostname()
        self.server_host = DEFAULT_SERVER_HOST
        self.server_port = DEFAULT_SERVER_PORT

        self.always_on_top_var = ctk.BooleanVar(value=False)
        self.active_peer_name = None
        self.is_calling = False
        self.contact_buttons = {}
        self.online_peers = set()

        self.loop = asyncio.new_event_loop()
        self.writer = None
        self.pc = None
        self.mic_track = None
        self.player = None

        self._setup_ui()
        self._load_contacts()

        threading.Thread(target=self._run_loop, daemon=True).start()

    # ---- UI ------------------------------------------------------------

    def _setup_ui(self):
        self.title_bar = ctk.CTkFrame(self.root, height=35, fg_color=COLORS["BG_MAIN"], corner_radius=0)
        self.title_bar.pack(side="top", fill="x")

        self.title_label = ctk.CTkLabel(self.title_bar, text="LITE INTERCOM", font=("Consolas", 14, "bold"),
                                        text_color=COLORS["BTN_QUIT_HOVER"])
        self.title_label.pack(side="left", padx=10)

        ctk.CTkButton(self.title_bar, text="QUIT", width=45, height=25, corner_radius=0,
                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["BTN_QUIT_HOVER"],
                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11, "bold"),
                      command=self.on_close).pack(side="right", padx=5, pady=5)

        ctk.CTkButton(self.title_bar, text="HELP", width=45, height=25, corner_radius=0,
                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 11, "bold"),
                      command=self.show_help).pack(side="right", padx=5, pady=5)

        self.settings_frame = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        self.settings_frame.pack(side="top", fill="x", padx=10, pady=(5, 5))

        ctk.CTkCheckBox(self.settings_frame, text="ALWAYS ON TOP", variable=self.always_on_top_var,
                        fg_color=COLORS["ACCENT"], text_color=COLORS["TEXT_WHITE"],
                        font=("Consolas", 10), command=self.toggle_topmost,
                        corner_radius=0, checkbox_width=14, checkbox_height=14,
                        border_width=1).pack(side="left", padx=(0, 10))

        self.sidebar = ctk.CTkScrollableFrame(self.root, fg_color="transparent", corner_radius=0)
        self.sidebar.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        self.status_label = ctk.CTkLabel(self.root, text="CONNECTING...", font=("Consolas", 10),
                                         text_color=COLORS["OFFLINE"])
        self.status_label.pack(side="bottom", pady=(0, 4))

        self.call_btn = ctk.CTkButton(self.root, text="START CALL", height=45, corner_radius=0,
                                      fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                      text_color=COLORS["TEXT_WHITE"], font=("Consolas", 14, "bold"),
                                      command=self.toggle_call)
        self.call_btn.pack(side="bottom", fill="x", padx=10, pady=(5, 5))

    def toggle_topmost(self):
        self.root.attributes("-topmost", self.always_on_top_var.get())

    def _load_contacts(self):
        if not os.path.exists(IP_LIST_FILE):
            with open(IP_LIST_FILE, "w", encoding="utf-8") as f:
                f.write("CONFIG: SERVER, 127.0.0.1, 5000\n")
                f.write("CONFIG: NAME, Me\n")
                f.write("CONFIG: TITLE, LITE INTERCOM\n")
                f.write("# One contact name per line:\n")
                f.write("PC2\n")

        contacts = []
        with open(IP_LIST_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.upper().startswith("CONFIG:"):
                    parts = [p.strip() for p in line[len("CONFIG:"):].split(",")]
                    key = parts[0].upper() if parts else ""
                    if key == "TITLE" and len(parts) >= 2:
                        self.title_label.configure(text=parts[1])
                    elif key == "NAME" and len(parts) >= 2:
                        self.my_name = parts[1]
                    elif key == "SERVER" and len(parts) >= 2:
                        self.server_host = parts[1]
                        if len(parts) >= 3 and parts[2].isdigit():
                            self.server_port = int(parts[2])
                    continue

                contacts.append(line.split(",")[0].strip())

        for name in contacts:
            if not name or name == self.my_name:
                continue
            btn = ctk.CTkButton(self.sidebar, text=name, corner_radius=0, height=45,
                                fg_color=COLORS["BTN_BASE"], hover_color=COLORS["ACCENT"],
                                text_color=COLORS["OFFLINE"], font=("Consolas", 12),
                                command=lambda n=name: self.select_peer(n))
            btn.pack(fill="x", pady=2, padx=2)
            self.contact_buttons[name] = btn

    def _refresh_contact_colors(self):
        for name, btn in self.contact_buttons.items():
            if name == self.active_peer_name:
                btn.configure(fg_color=COLORS["ACCENT"], text_color=COLORS["BG_MAIN"])
            else:
                online = name in self.online_peers
                btn.configure(fg_color=COLORS["BTN_BASE"],
                              text_color=COLORS["TEXT_WHITE"] if online else COLORS["OFFLINE"])

    def select_peer(self, name):
        if self.is_calling:
            return
        self.active_peer_name = name
        self._refresh_contact_colors()
        self.call_btn.configure(text=f"START CALL WITH {name.upper()}")

    def _set_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)

    def _set_in_call_ui(self):
        self.is_calling = True
        self.call_btn.configure(text="END CALL", fg_color=COLORS["BTN_QUIT_HOVER"],
                                text_color=COLORS["TEXT_WHITE"], hover_color="#CC0000")

    def _reset_call_ui(self):
        self.is_calling = False
        text = f"START CALL WITH {self.active_peer_name.upper()}" if self.active_peer_name else "START CALL"
        self.call_btn.configure(text=text, fg_color=COLORS["BTN_BASE"],
                                text_color=COLORS["TEXT_WHITE"], hover_color=COLORS["ACCENT"])
        self._refresh_contact_colors()

    # ---- call control (called from the Tk thread) ----------------------

    def toggle_call(self):
        if not self.active_peer_name:
            return
        if self.is_calling:
            self._submit(self._hang_up(notify=True))
        else:
            if self.writer is None:
                self._set_status("NOT CONNECTED TO SERVER", COLORS["BTN_QUIT_HOVER"])
                return
            self._set_in_call_ui()
            self._set_status("CALLING...", COLORS["ACCENT"])
            self._submit(self._place_call(self.active_peer_name))

    def _submit(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _ui(self, fn, *args):
        self.root.after(0, lambda: fn(*args))

    # ---- asyncio / signaling -------------------------------------------

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._signaling_main())

    async def _signaling_main(self):
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.server_host, self.server_port)
                self.writer = writer
                await self._send({"type": "register", "name": self.my_name})
                self._ui(self._set_status, "CONNECTED", COLORS["ACCENT"])
                self.root.after(0, lambda: self.title_label.configure(text_color=COLORS["ACCENT"]))

                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    await self._handle_signal(msg)
            except Exception:
                pass

            self.writer = None
            self.online_peers.clear()
            self._ui(self._set_status, "RECONNECTING...", COLORS["BTN_QUIT_HOVER"])
            self.root.after(0, lambda: self.title_label.configure(text_color=COLORS["BTN_QUIT_HOVER"]))
            await self._hang_up(notify=False)
            await asyncio.sleep(3)

    async def _send(self, obj):
        writer = self.writer
        if writer is None:
            return
        try:
            writer.write((json.dumps(obj) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass

    async def _handle_signal(self, msg):
        mtype = msg.get("type")

        if mtype == "peers":
            self.online_peers = set(msg.get("names", []))
            self._ui(self._refresh_contact_colors)

        elif mtype == "peer-joined":
            self.online_peers.add(msg.get("name"))
            self._ui(self._refresh_contact_colors)

        elif mtype == "peer-left":
            self.online_peers.discard(msg.get("name"))
            self._ui(self._refresh_contact_colors)
            if msg.get("name") == self.active_peer_name and self.is_calling:
                await self._hang_up(notify=False)

        elif mtype == "offer":
            caller = msg.get("from")
            if self.is_calling:
                await self._send({"type": "busy", "to": caller})
                return
            await self._accept_call(caller, msg.get("sdp"))

        elif mtype == "answer":
            if self.pc is not None:
                try:
                    await self.pc.setRemoteDescription(
                        RTCSessionDescription(sdp=msg.get("sdp"), type="answer")
                    )
                    self._ui(self._set_status, "IN CALL", COLORS["ACCENT"])
                except Exception:
                    await self._hang_up(notify=True)

        elif mtype in ("end", "busy", "unavailable"):
            label = "PEER BUSY" if mtype == "busy" else (
                "PEER OFFLINE" if mtype == "unavailable" else "CALL ENDED")
            self._ui(self._set_status, label, COLORS["OFFLINE"])
            await self._hang_up(notify=False)

    # ---- WebRTC --------------------------------------------------------

    def _new_peer_connection(self):
        # No ICE servers: this runs on a LAN with no internet access, so only
        # host candidates are used. Adding STUN here would just stall setup.
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))

        @pc.on("connectionstatechange")
        async def on_state_change():
            state = pc.connectionState
            if state == "connected":
                self._ui(self._set_status, "IN CALL", COLORS["ACCENT"])
            elif state in ("failed", "closed", "disconnected"):
                await self._hang_up(notify=False)

        @pc.on("track")
        def on_track(track):
            if track.kind == "audio":
                asyncio.ensure_future(self._consume_track(track))

        return pc

    async def _consume_track(self, track):
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                break
            except Exception:
                break
            if self.player is not None:
                self.player.feed(frame)

    def _refresh_audio_devices(self):
        # PortAudio scans devices once at import time, so a headset plugged in
        # after launch stays invisible until it is re-initialized.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

    def _start_media(self):
        self._refresh_audio_devices()
        self.player = SpeakerPlayer()
        self.player.start()
        self.mic_track = MicrophoneTrack(self.loop)
        return self.mic_track

    async def _place_call(self, target):
        try:
            self.pc = self._new_peer_connection()
            self.pc.addTrack(self._start_media())
            offer = await self.pc.createOffer()
            # aiortc completes ICE gathering inside setLocalDescription, so the
            # SDP we send already carries every candidate (no trickle needed).
            await self.pc.setLocalDescription(offer)
            await self._send({
                "type": "offer",
                "to": target,
                "sdp": self.pc.localDescription.sdp,
            })
        except Exception:
            await self._hang_up(notify=False)

    async def _accept_call(self, caller, sdp):
        try:
            self._ui(self.select_peer, caller)
            self._ui(self._set_in_call_ui)
            self._ui(self._set_status, "CONNECTING...", COLORS["ACCENT"])

            self.pc = self._new_peer_connection()
            self.pc.addTrack(self._start_media())
            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            await self._send({
                "type": "answer",
                "to": caller,
                "sdp": self.pc.localDescription.sdp,
            })
        except Exception:
            await self._hang_up(notify=True)

    async def _hang_up(self, notify):
        if notify and self.active_peer_name:
            await self._send({"type": "end", "to": self.active_peer_name})

        if self.mic_track is not None:
            self.mic_track.stop()
            self.mic_track = None
        if self.player is not None:
            self.player.stop()
            self.player = None
        if self.pc is not None:
            pc, self.pc = self.pc, None
            try:
                await pc.close()
            except Exception:
                pass

        self._ui(self._reset_call_ui)

    # ---- misc ----------------------------------------------------------

    def show_help(self):
        win = ctk.CTkToplevel(self.root)
        win.geometry("320x260")
        win.title("SYSTEM HELP")
        win.configure(fg_color=COLORS["BG_MAIN"])

        txt = ctk.CTkTextbox(win, fg_color=COLORS["BG_MAIN"], text_color=COLORS["TEXT_WHITE"],
                             font=("Consolas", 11), corner_radius=0)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0",
                   "--- LITE INTERCOM (WebRTC) ---\n\n"
                   "* Title is GREEN when the signaling\n"
                   "  server is reachable.\n"
                   "* Contact names are white when that\n"
                   "  peer is online, grey when offline.\n"
                   "* Select a contact, then START CALL.\n"
                   "* Audio is Opus over WebRTC and goes\n"
                   "  directly peer-to-peer.\n"
                   "* Edit 'ip_list.txt' for server+contacts.\n")
        txt.configure(state="disabled")

    def on_close(self):
        try:
            fut = asyncio.run_coroutine_threadsafe(self._hang_up(notify=True), self.loop)
            fut.result(timeout=2)
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)


if __name__ == "__main__":
    root = ctk.CTk()
    app = LiteIntercomApp(root)
    root.mainloop()
