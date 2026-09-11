import os

SERVER_URL = "http://127.0.0.1:5000"
CONFIG_FILE = "config.txt"
TARGETS_FILE = "targets.txt"
SNAPSHOTS_DIR = "snapshots"

def load_config():
    config = {"width": "1200", "height": "800", "show_header": "True", "target_display": "0"}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        config[k] = v
        except: pass
    return config

def update_config(updates):
    config = load_config()
    config.update(updates)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            for k, v in config.items():
                f.write(f"{k}={v}\n")
    except: pass

def load_targets():
    targets = []
    if os.path.exists(TARGETS_FILE):
        try:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "|" in line:
                        name, url = line.strip().split("|", 1)
                        targets.append({"name": name, "url": url})
        except: pass
    return targets