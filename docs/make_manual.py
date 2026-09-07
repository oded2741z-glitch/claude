from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

OUT = "/home/user/claude/docs/P2P_Intercom_Manual.pdf"

ACCENT = colors.HexColor("#ff7300")
INK = colors.HexColor("#111827")
GREY = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")
CODE_BG = colors.HexColor("#f5f6f8")
HEAD_BG = colors.HexColor("#eef1f5")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

ss = getSampleStyleSheet()

S_TITLE = ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                         fontSize=26, leading=30, textColor=INK, alignment=TA_LEFT,
                         spaceAfter=2)
S_SUB = ParagraphStyle("s", parent=ss["Normal"], fontName="Helvetica",
                       fontSize=11.5, leading=16, textColor=GREY, spaceAfter=14)
S_H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                      fontSize=16, leading=20, textColor=INK,
                      spaceBefore=16, spaceAfter=7)
S_H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                      fontSize=11.5, leading=15, textColor=ACCENT,
                      spaceBefore=12, spaceAfter=4)
S_BODY = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica",
                        fontSize=9.6, leading=14.2, textColor=INK, spaceAfter=6)
S_LI = ParagraphStyle("li", parent=S_BODY, leftIndent=11, bulletIndent=2, spaceAfter=3)
S_CODE = ParagraphStyle("c", parent=ss["Normal"], fontName="Courier",
                        fontSize=8.3, leading=11.4, textColor=INK,
                        backColor=CODE_BG, borderPadding=(6, 7, 6, 7),
                        leftIndent=2, spaceBefore=3, spaceAfter=8)
S_TD = ParagraphStyle("td", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=8.4, leading=11.4, textColor=INK)
S_TDC = ParagraphStyle("tdc", parent=S_TD, fontName="Courier", fontSize=7.9)
S_TH = ParagraphStyle("th", parent=S_TD, fontName="Helvetica-Bold", textColor=INK)
S_NOTE = ParagraphStyle("n", parent=S_BODY, leftIndent=9, borderPadding=(6, 8, 6, 8),
                        backColor=colors.HexColor("#fff7ed"), spaceBefore=4, spaceAfter=9)


def P(text, style=S_BODY):
    return Paragraph(text, style)


def LI(text):
    return Paragraph(text, S_LI, bulletText="–")


def CODE(text):
    body = escape(text).replace(" ", "&nbsp;").replace("\n", "<br/>")
    return Paragraph(f'<font face="Courier">{body}</font>', S_CODE)


def NOTE(text):
    return Paragraph(text, S_NOTE)


def TBL(header, rows, widths, mono_cols=()):
    def cell(txt, col, head=False):
        if head:
            return P(escape(txt), S_TH)
        style = S_TDC if col in mono_cols else S_TD
        return Paragraph(escape(txt).replace("\n", "<br/>"), style)

    data = [[cell(h, i, True) for i, h in enumerate(header)]]
    for r in rows:
        data.append([cell(c, i) for i, c in enumerate(r)])

    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.HexColor("#cbd5e1")),
        ("GRID", (0, 0), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
    ]))
    return t


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - MARGIN + 6 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN + 6 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GREY)
    canvas.drawString(MARGIN, PAGE_H - MARGIN + 8 * mm, "P2P Intercom — Technical Manual")
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 8 * mm, "v2")

    canvas.line(MARGIN, MARGIN - 4 * mm, PAGE_W - MARGIN, MARGIN - 4 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(MARGIN, MARGIN - 8 * mm, "(c) oT — All rights reserved.")
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 8 * mm, str(canvas.getPageNumber()))
    canvas.restoreState()


story = []
A = story.append

# ----------------------------------------------------------------- cover
A(Spacer(1, 34))
A(P("P2P Intercom", S_TITLE))
A(P("Technical manual for <b>server.py</b> and <b>clint.py</b> — a two-party "
    "voice intercom over UDP, with a signalling server that matches the peers and "
    "reports the remote client's headphone state.", S_SUB))

A(TBL(["Component", "Role"], [
    ["server.py",
     "Tkinter GUI. Hosts the optional internal signalling server, runs one side of the "
     "call, shows a live dashboard of the link, beeps when the remote headphones connect, "
     "and can shut the remote client down."],
    ["clint.py",
     "Headless console client. Registers with the server, reports its headphone state once "
     "per second, runs the other side of the call. No window, no keyboard input."],
], [32 * mm, 132 * mm], mono_cols=(0,)))

A(Spacer(1, 10))
A(P("This manual is written in English to match the interface: every button label, log "
    "line and setting key it refers to appears verbatim on screen.", S_BODY))

# ----------------------------------------------------------------- 1
A(P("1. How a call is established", S_H1))
A(P("Both sides speak to a signalling server only long enough to learn each other's "
    "address. The audio itself never passes through the server — it goes straight "
    "between the two machines.", S_BODY))

A(P("Step by step", S_H2))
for i, txt in enumerate([
    "<b>Register.</b> Each side sends <font face=\"Courier\">{\"id\": \"...\"}</font> to "
    "the signalling server once per second and the server answers "
    "<font face=\"Courier\">{\"ack\":true}</font>.",
    "<b>Match.</b> As soon as two different IDs are registered, the server sends each one "
    "the other's IP, port and ID, five times, then clears the pool.",
    "<b>Punch.</b> Each side fires tagged PUNCH datagrams straight at the peer for two "
    "seconds. This opens the NAT mapping in both directions.",
    "<b>Talk.</b> Both sides open their microphone and speaker and exchange tagged audio "
    "datagrams peer to peer.",
], 1):
    A(Paragraph(txt, S_LI, bulletText=f"{i}."))

A(NOTE("<b>Why the tag byte matters.</b> Signalling is JSON and starts with "
       "<font face=\"Courier\">{</font>. P2P packets carry a one-byte tag instead "
       "(<font face=\"Courier\">\\x01</font> audio, <font face=\"Courier\">\\x02</font> "
       "punch). Both sockets receive both kinds, and without the tag a JSON control "
       "message would be handed to the speaker and played as noise."))

A(P("Address locking", S_H2))
A(P("The server reports the address it saw, but a NAT may hand the peer a different "
    "source port. Each side therefore locks onto the address the first tagged packet "
    "actually arrived from, and from then on ignores tagged packets from anywhere else. "
    "That covers the NAT case and also stops anyone else on the network from injecting "
    "audio into a live call.", S_BODY))

A(NOTE("<b>Limit.</b> Hole punching works with cone NAT. Symmetric NAT rewrites the port "
       "per destination, so the punched mapping does not match and the call will not "
       "connect. That needs a relay (TURN), which this project does not implement."))

# ----------------------------------------------------------------- 2
A(PageBreak())
A(P("2. Installing and running", S_H1))
A(P("Requirements", S_H2))
A(CODE("pip install sounddevice numpy\n\n"
       "python server.py     # the GUI machine\n"
       "python clint.py      # the headless machine"))
A(P("Tkinter ships with Python on Windows. <font face=\"Courier\">winsound</font> is part "
    "of the standard library on Windows only; on Linux and macOS the import is guarded and "
    "the app simply runs without the beep.", S_BODY))

A(P("Testing both sides on one computer", S_H2))
A(P("Two machines are not required to exercise everything except real audio:", S_BODY))
for t in [
    "Run <font face=\"Courier\">server.py</font> and press <b>START INTERNAL SERVER</b>.",
    "Set the client's <font face=\"Courier\">settings.txt</font> to "
    "<font face=\"Courier\">\"ip\": \"127.0.0.1\"</font>.",
    "Give the two sides <b>different</b> IDs — for example "
    "<font face=\"Courier\">node_A</font> in the GUI and "
    "<font face=\"Courier\">node_B</font> in the file. Matching needs two distinct IDs.",
    "Run <font face=\"Courier\">clint.py</font> in its own console window.",
]:
    A(LI(t))
A(P("Headphone detection, the beep, the dashboard and the shutdown button all work this "
    "way. A real call does not, because both processes would fight over the same sound "
    "card.", S_BODY))

# ----------------------------------------------------------------- 3
A(P("3. Configuration files", S_H1))
A(P("Both files are JSON, are created with defaults on first run, and are read again "
    "before every call attempt — so an edit takes effect without a restart.", S_BODY))

A(P("settings_A.txt — server", S_H2))
A(TBL(["Key", "Meaning"], [
    ["ext_ip", "Public address to report instead of a private one. Only relevant when the "
               "signalling server is not itself on the public internet."],
    ["local_mode", "true keeps addresses exactly as seen and ignores ext_ip. Use this on a "
                   "single LAN."],
    ["ip / port", "Signalling server this GUI registers with. Point it at 127.0.0.1 when "
                  "hosting the internal server."],
    ["my_id", "This side's identifier. Must differ from the client's."],
], [24 * mm, 140 * mm], mono_cols=(0,)))

A(NOTE("<b>ext_ip does not translate the port.</b> Only the address is rewritten, so this "
       "path needs a static port forward on the router. On one LAN, tick "
       "<b>Use Local Mode</b> instead."))

A(P("settings.txt — client", S_H2))
A(TBL(["Key", "Meaning"], [
    ["ip / port", "Signalling server address. Also the only address the client will accept "
                  "a shutdown command from."],
    ["my_id", "This client's identifier."],
    ["hp_device", "Part of the headset's device name, matched case-insensitively. This is "
                  "what makes unplug detection work — see section 7."],
], [24 * mm, 140 * mm], mono_cols=(0,)))

A(CODE('{\n'
       '    "ip": "192.168.1.12",\n'
       '    "port": "9999",\n'
       '    "my_id": "node_B",\n'
       '    "hp_device": "2- USB PnP"\n'
       '}'))

# ----------------------------------------------------------------- 4
A(PageBreak())
A(P("4. Server window reference", S_H1))
A(TBL(["Control", "What it does"], [
    ["Help", "Opens the in-app help, drawn in the application theme."],
    ["Quit", "Ends the call, stops the internal server, closes the window."],
    ["External IP / Use Local Mode",
     "Address rewriting policy for the internal signalling server. See section 3."],
    ["START INTERNAL SERVER",
     "Binds UDP on the configured port and starts matching peers. The label only changes "
     "to STOP once the bind actually succeeded; a failure is reported on the SERVER "
     "CONNECTION row."],
    ["Server IP / Port / My ID",
     "Which signalling server this side registers with, and under what name. Locked while "
     "a call is running."],
    ["START INTERCOM",
     "Registers, waits for a peer, punches, then streams. Also silences the beep."],
    ["SHUTDOWN CLIENT",
     "Asks the most recently heard client to exit its process. See section 6."],
], [42 * mm, 122 * mm]))

A(P("Dashboard rows", S_H2))
A(P("Three cards refresh five times a second. The worker threads only write plain values; "
    "a single timer redraws them, so no background thread ever touches a widget.", S_BODY))

A(TBL(["Row", "Colour", "Meaning"], [
    ["SERVER CONNECTION", "grey", "Not hosting and not registered."],
    ["", "green", "Internal server listening, or registered with an external one."],
    ["", "amber", "Registration sent, no peer assigned yet."],
    ["", "red", "Bind failed — the port is taken or blocked."],
    ["CLIENT CONNECTION", "grey", "No call running."],
    ["", "amber", "Waiting for a peer, punching, or peer audio has stopped."],
    ["", "green", "Audio arriving from the locked peer. Shows peer, address and call time."],
    ["CLIENT HEADPHONES", "grey", "Nobody has reported, or no report for over 5 seconds."],
    ["", "green", "Remote headphones present. Beeping until acknowledged."],
    ["", "red", "Remote headphones gone."],
], [38 * mm, 16 * mm, 110 * mm]))

A(NOTE("A stale row shows <i>no report for N s</i> in grey rather than staying green. A "
       "green light that is really just a stale memory is worse than an honest "
       "“unknown”."))

# ----------------------------------------------------------------- 5
A(P("5. Client console output", S_H1))
A(P("The client has no window. Every line below is a state change — it is not printed "
    "again while the state holds, so a quiet console means nothing has changed.", S_BODY))

A(TBL(["Line", "Meaning"], [
    ["Audio devices detected: ...",
     "Printed once at startup. Copy part of your headset's name into hp_device."],
    ["HEADPHONES: CONNECTED",
     "The tracked headset now provides both a microphone and a speaker. Names and indexes "
     "follow. Reported to the server immediately."],
    ["HEADPHONES: DISCONNECTED",
     "The tracked headset is gone. Any active call ends."],
    ["SERVER: CONNECTED",
     "The server answered a heartbeat."],
    ["SERVER: DISCONNECTED",
     "No answer for 3 seconds. The client keeps trying."],
    ["hp_device \"x\" matches no mic+speaker pair",
     "The keyword is wrong or the device is unplugged. Printed once, not per poll."],
    ["Peer Target Assigned ...",
     "The server matched this client with a peer."],
    ["Peer reached us from ...",
     "The peer's real source address differs from the one reported. Normal behind NAT."],
    ["Peer disconnected (3 seconds timeout).",
     "No audio for 3 seconds. The call ends and the client waits for a new match."],
    ["Shutdown command received from server.",
     "A valid remote shutdown. The process exits."],
    ["Ignored shutdown command from ...",
     "A shutdown arrived from something other than the signalling server, and was dropped."],
], [56 * mm, 108 * mm], mono_cols=(0,)))

# ----------------------------------------------------------------- 6
A(PageBreak())
A(P("6. Beep and remote shutdown", S_H1))

A(P("Beep on headphone connect", S_H2))
A(P("When the server sees a client's headphone state <i>change</i> to connected, it starts "
    "a repeating 1000 Hz beep, 200 ms long, every 1.5 seconds. It fires on the transition "
    "only — the client heartbeats once a second, and reacting to every report would "
    "beep forever.", S_BODY))
A(P("It stops when you click anywhere on the CLIENT HEADPHONES card, when the headphones "
    "are unplugged, when you press START INTERCOM, when the internal server is stopped, and "
    "on close. While it rings, the card reads <i>click to silence</i>.", S_BODY))
A(NOTE("<font face=\"Courier\">winsound.Beep</font> blocks for the length of the beep, "
       "which is why the beep is kept short: silencing waits at most one beep. Raising "
       "<font face=\"Courier\">RING_MS</font> above roughly 400 makes the silence button "
       "feel sluggish."))

A(P("SHUTDOWN CLIENT", S_H2))
A(P("The button targets the client that reported most recently, names it in a confirmation "
    "dialog, and on confirmation sends "
    "<font face=\"Courier\">{\"cmd\": \"shutdown\"}</font> three times. The client ends any "
    "call, reports its disconnect and exits its process.", S_BODY))

A(P("Two guards, both deliberate:", S_BODY))
A(LI("<b>The command leaves the internal server's socket.</b> If that server is not "
     "running the button explains why instead of sending into the void — the client "
     "would reject it anyway."))
A(LI("<b>The client obeys only its own signalling server.</b> Source address and port must "
     "both match the server it registered with. Anything else is logged and dropped, so a "
     "single stray datagram cannot kill a remote client."))

A(NOTE("<b>This is one-way.</b> The client exits its process. If it runs on a machine with "
       "no screen, restarting it needs physical access."))

# ----------------------------------------------------------------- 7
A(P("7. Troubleshooting", S_H1))

A(P("Headphone state never changes on the server", S_H2))
A(P("Almost always <font face=\"Courier\">hp_device</font> is empty. Without it the client "
    "watches the system <i>default</i> device, and Windows falls back to the built-in audio "
    "the moment a USB headset is unplugged — so the check keeps passing and the client "
    "keeps reporting “connected”.", S_BODY))
A(P("Run the client, read the device list it prints, and put part of your headset's name in "
    "<font face=\"Courier\">hp_device</font>.", S_BODY))

A(NOTE("<b>Be specific enough.</b> If two USB dongles are present, "
       "<font face=\"Courier\">\"USB PnP\"</font> matches both, and unplugging one still "
       "leaves a match — the disconnect is swallowed. Use the distinguishing part, for "
       "example <font face=\"Courier\">\"2- USB PnP\"</font>."))

A(P("Client says SERVER: DISCONNECTED", S_H2))
A(P("The server is not answering heartbeats. Check that the internal server is started, "
    "that the IP and port match on both sides, and that the port is open in the firewall. "
    "An old build of <font face=\"Courier\">server.py</font> does not send acknowledgements "
    "at all — both files must come from the same version.", S_BODY))

A(P("Two sides never match", S_H2))
A(P("The two IDs must differ; the server matches the first two <i>distinct</i> IDs it sees. "
    "A registration older than 30 seconds is discarded, so a side that stopped registering "
    "will not be matched.", S_BODY))

A(P("Audio connects, then drops after three seconds", S_H2))
A(P("Datagrams are not arriving from the peer. Usually symmetric NAT, or a firewall "
    "blocking the punched port. Check whether the log shows the peer reaching you from an "
    "unexpected address.", S_BODY))

A(P("A widget disappeared after editing the server window", S_H2))
A(P("The dashboard is packed with <font face=\"Courier\">expand=True</font> and consumes "
    "whatever space is left. Anything packed <i>after</i> it gets none and is silently not "
    "drawn — no error, no warning. Pack new widgets <b>before</b> "
    "<font face=\"Courier\">_create_dashboard()</font>.", S_BODY))

# ----------------------------------------------------------------- 8
A(PageBreak())
A(P("8. Wire protocol", S_H1))
A(P("Every datagram is either JSON signalling or a tagged P2P packet. Nothing else is "
    "accepted.", S_BODY))

A(P("Client to server", S_H2))
A(TBL(["Message", "When", "Effect"], [
    ['{"id": X}', "Once a second while waiting for a peer",
     "Enters the matching pool. Two distinct IDs trigger a match."],
    ['{"id": X, "status": "hp",\n "headphones": bool}', "Once a second, always",
     "Updates the dashboard and the beep. Never enters the matching pool."],
    ['{"id": X,\n "status": "disconnected"}', "On exit, three times",
     "Frees the slot and marks the client disconnected."],
], [50 * mm, 40 * mm, 74 * mm], mono_cols=(0,)))

A(P("Server to client", S_H2))
A(TBL(["Message", "When", "Effect"], [
    ['{"ack":true}', "For every valid signalling message",
     "Proves the server is alive. Three seconds without one flips the client to SERVER: "
     "DISCONNECTED."],
    ['{"peer_ip": ..., "peer_port": ...,\n "peer_id": ...}', "On a match, five times",
     "The client punches this address and starts streaming."],
    ['{"cmd": "shutdown"}', "SHUTDOWN CLIENT, three times",
     "The client exits — only if the datagram came from its own signalling server."],
], [50 * mm, 40 * mm, 74 * mm], mono_cols=(0,)))

A(P("Peer to peer", S_H2))
A(TBL(["Packet", "Meaning"], [
    ["\\x01 + PCM", "Audio. 16 kHz, mono, signed 16-bit little endian, 512 frames."],
    ["\\x02PUNCH", "NAT hole punch. Opens the mapping; never played."],
], [32 * mm, 132 * mm], mono_cols=(0,)))

# ----------------------------------------------------------------- 9
A(P("9. Tuning constants", S_H1))
A(P("All at the top of each file.", S_BODY))

A(P("Shared audio", S_H2))
A(TBL(["Constant", "Value", "Effect of changing it"], [
    ["SAMPLE_RATE", "16000", "Voice quality against bandwidth. Must match on both sides."],
    ["CHANNELS", "1", "Mono. Must match on both sides."],
    ["CHUNK_SIZE", "512", "Frames per packet: ~32 ms. Lower means less latency, more packets."],
    ["TIMEOUT_SECS", "3.0", "Silence from the peer before the call is dropped."],
], [34 * mm, 20 * mm, 110 * mm], mono_cols=(0, 1)))

A(P("Server", S_H2))
A(TBL(["Constant", "Value", "Effect of changing it"], [
    ["CLIENT_TTL", "30.0", "How long a registration stays eligible for matching."],
    ["MATCH_RETRIES", "5", "Copies of the peer-match reply. UDP may drop some."],
    ["CMD_RETRIES", "3", "Copies of the shutdown command."],
    ["PUNCH_DURATION", "2.0", "Seconds of punching before streaming starts."],
    ["UI_TICK_MS", "200", "Dashboard refresh interval."],
    ["PEER_IDLE", "1.5", "Silence before the client row stops being green."],
    ["HP_REPORT_TTL", "5.0", "Silence before the headphone row goes to unknown."],
    ["RING_INTERVAL", "1.5", "Gap between beeps."],
    ["RING_FREQ", "1000", "Beep pitch in Hz. Useful range roughly 500-2000."],
    ["RING_MS", "200", "Beep length. Above ~400 the silence click feels delayed."],
], [34 * mm, 20 * mm, 110 * mm], mono_cols=(0, 1)))

A(P("Client", S_H2))
A(TBL(["Constant", "Value", "Effect of changing it"], [
    ["HEARTBEAT_INTERVAL", "1.0", "Rate of headphone reports and registrations."],
    ["SERVER_TIMEOUT", "3.0", "Missing acknowledgements before SERVER: DISCONNECTED."],
    ["DEVICE_POLL_INTERVAL", "1.0", "How often the audio hardware is examined."],
    ["DEVICE_REFRESH_GAP", "2.0", "Minimum gap between PortAudio restarts."],
    ["REPORT_REPEATS", "3", "Copies of a state-change report."],
    ["AUDIO_QUEUE_MAX", "64", "Playback backlog. Oldest packet is dropped when full."],
    ["STREAM_CLOSE_WAIT", "5.0", "Patience when closing a stream on an unplugged device."],
], [40 * mm, 20 * mm, 104 * mm], mono_cols=(0, 1)))

# ----------------------------------------------------------------- 10
A(PageBreak())
A(P("10. Implementation notes", S_H1))
A(P("Four things in this code look odd and are load-bearing. Changing them reintroduces "
    "bugs that were awkward to find.", S_BODY))

A(P("PortAudio must be restarted to see hardware changes", S_H2))
A(P("PortAudio snapshots the device list when it initialises. Without a periodic "
    "terminate-and-initialise, a headset that was unplugged is still listed forever and "
    "hot-plug is invisible. The restart never runs while a stream is open: terminating "
    "PortAudio underneath a live stream kills the process at the C level, with no "
    "traceback. An open-stream counter is held for the whole life of every stream, "
    "including the close — closing a stream on a device that was just pulled can "
    "block, and that window is exactly when a restart would be fatal.", S_BODY))

A(P("Streams open on the tracked device, not the default", S_H2))
A(P("Once the headset is located by name, its indexes are used for both the capability "
    "check and the streams. Opening on the default device would silently record from the "
    "built-in microphone after a headset is unplugged.", S_BODY))

A(P("One receive loop owns the socket", S_H2))
A(P("The client's socket carries heartbeats, match replies, commands and audio. A single "
    "thread calls <font face=\"Courier\">recvfrom</font> and dispatches into queues. When "
    "several threads read the same UDP socket they steal each other's datagrams at random, "
    "which produces failures that look like packet loss and never reproduce the same way "
    "twice.", S_BODY))

A(P("Audio threads are joined before the socket closes", S_H2))
A(P("Closing the socket out from under a running audio thread leaves the sound card busy "
    "and raises misleading exceptions. Shutdown waits for the threads first, and if one is "
    "still closing a stream after five seconds it says so and leaves PortAudio "
    "alone.", S_BODY))

A(P("11. Version history", S_H1))
A(TBL(["Version", "Contents"], [
    ["v1", "Headphone and server-link state reporting. Persistent client socket, device "
           "tracking by name, heartbeat with acknowledgements, single receive loop."],
    ["v2", "Beep on headphone connect. SHUTDOWN CLIENT with source validation. All dialogs "
           "drawn in the application theme. Watermark and layout fixes."],
], [20 * mm, 144 * mm], mono_cols=(0,)))

A(Spacer(1, 16))
A(P("<font color=\"#64748b\">(c) oT — All rights reserved.</font>", S_BODY))

# ----------------------------------------------------------------- build
doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=MARGIN, rightMargin=MARGIN,
                      topMargin=MARGIN, bottomMargin=MARGIN,
                      title="P2P Intercom - Technical Manual",
                      author="oT", subject="Manual for server.py and clint.py")
frame = Frame(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN, id="f")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(story)
print("written:", OUT)
