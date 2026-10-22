import os
import sys
import json
import queue
import socket
import secrets
import threading
import subprocess
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import filedialog
from datetime import datetime

try:
    import keyboard
    HAS_KEYBOARD_LIB = True
except ImportError:
    HAS_KEYBOARD_LIB = False


STACK_FOLDER_NAME = "local-agent-stack"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
REQUIRED_OLLAMA_MODELS = ["llama3.1:latest", "nomic-embed-text:latest"]

COLORS = {
    "bar": "#121212",
    "panel": "#1e1e1e",
    "control": "#333333",
    "text": "#ffffff",
    "muted": "#888888",
    "accent": "#ff6600",
    "danger": "#ff0000",
}

SERVICES = [
    ("Ollama", "11434", ("http", "http://127.0.0.1:11434/api/tags")),
    ("Postgres", "5432", ("tcp", 5432)),
    ("Qdrant", "6333", ("http", "http://127.0.0.1:6333/healthz")),
    ("Langfuse", "3001", ("http", "http://127.0.0.1:3001/api/public/health")),
    ("LiteLLM", "4000", ("http", "http://127.0.0.1:4000/health/liveliness")),
    ("Mem0", "8000", ("http", "http://127.0.0.1:8000/health")),
    ("Agent Tools", "8001", ("http", "http://127.0.0.1:8001/health")),
    ("Open WebUI", "3000", ("http", "http://127.0.0.1:3000/health")),
]


DOCKER_COMPOSE_TEMPLATE = """name: local-agent-stack

services:
  postgres:
    image: postgres:16.15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: litellm
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres_init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - agent_net

  qdrant:
    image: qdrant/qdrant:v1.20.0
    restart: unless-stopped
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    networks:
      - agent_net

  langfuse:
    image: langfuse/langfuse:2.95.11
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "127.0.0.1:3001:3000"
    environment:
      DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/langfuse
      NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
      SALT: ${LANGFUSE_SALT}
      NEXTAUTH_URL: http://localhost:3001
      TELEMETRY_ENABLED: "false"
      LANGFUSE_INIT_ORG_ID: local-org
      LANGFUSE_INIT_ORG_NAME: Local
      LANGFUSE_INIT_PROJECT_ID: local-agent
      LANGFUSE_INIT_PROJECT_NAME: Local Agent
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_ADMIN_EMAIL}
      LANGFUSE_INIT_USER_NAME: Admin
      LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_ADMIN_PASSWORD}
    networks:
      - agent_net

  litellm:
    image: ghcr.io/berriai/litellm:v1.103.7
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      langfuse:
        condition: service_started
    ports:
      - "127.0.0.1:4000:4000"
    environment:
      DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/litellm
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      LITELLM_SALT_KEY: ${LITELLM_SALT_KEY}
      LANGFUSE_HOST: http://langfuse:3000
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
    volumes:
      - ./litellm_config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - agent_net

  mem0:
    build:
      context: ./mem0_service
    restart: unless-stopped
    depends_on:
      - qdrant
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      OLLAMA_BASE_URL: http://host.docker.internal:11434
      QDRANT_HOST: qdrant
      QDRANT_PORT: "6333"
      LLM_MODEL: llama3.1:latest
      EMBEDDING_MODEL: nomic-embed-text
      MEM0_TELEMETRY: "False"
    volumes:
      - mem0_data:/root/.mem0
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - agent_net

  open-webui:
    image: ghcr.io/open-webui/open-webui:v0.12.3
    restart: unless-stopped
    depends_on:
      - litellm
    ports:
      - "127.0.0.1:3000:8080"
    environment:
      ENABLE_OLLAMA_API: "False"
      OPENAI_API_BASE_URL: http://litellm:4000/v1
      OPENAI_API_KEY: ${LITELLM_MASTER_KEY}
      WEBUI_AUTH: "False"
      WEBUI_SECRET_KEY: ${WEBUI_SECRET_KEY}
      DEFAULT_MODELS: local-agent-llm
      TOOL_SERVER_CONNECTIONS: '[{"type": "openapi", "url": "http://host.docker.internal:8001", "path": "openapi.json", "auth_type": "bearer", "key": "${AGENT_TOOLS_TOKEN}", "config": {"enable": true}, "info": {"id": "agent", "name": "Agent Tools", "description": "Long-term memory and host tools"}}]'
      DEFAULT_MODEL_METADATA: '{"toolIds": ["server:agent"]}'
      DEFAULT_MODEL_PARAMS: '{"system": "You are a local AI agent with long-term memory and tools on the host computer. At the start of a conversation, and whenever the user mentions themselves, their preferences or earlier work, call search_memory first. When the user shares a lasting fact, preference or decision, call add_memory to save it. Use run_command, list_dir and host_status when a task needs the local computer, and report what they returned."}'
    volumes:
      - open_webui_data:/app/backend/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    networks:
      - agent_net

volumes:
  postgres_data:
  qdrant_data:
  mem0_data:
  open_webui_data:

networks:
  agent_net:
    driver: bridge
"""

LITELLM_CONFIG_TEMPLATE = """model_list:
  - model_name: local-agent-llm
    litellm_params:
      model: ollama_chat/llama3.1:latest
      api_base: http://host.docker.internal:11434

litellm_settings:
  drop_params: true
  success_callback: ["langfuse"]
  failure_callback: ["langfuse"]

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
"""

POSTGRES_INIT_TEMPLATE = """CREATE DATABASE langfuse;
"""

MEM0_DOCKERFILE_TEMPLATE = """FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

MEM0_REQUIREMENTS_TEMPLATE = """mem0ai==2.2.1
fastapi==0.161.0
uvicorn==0.54.0
qdrant-client==1.20.0
ollama==0.6.3
"""

MEM0_MAIN_PY_TEMPLATE = """import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from mem0 import Memory
from pydantic import BaseModel

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:latest")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBED_DIMS = 768

MEM0_CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "agent_long_term_memory",
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "embedding_model_dims": EMBED_DIMS,
        },
    },
    "llm": {
        "provider": "ollama",
        "config": {
            "model": LLM_MODEL,
            "temperature": 0.1,
            "max_tokens": 2000,
            "ollama_base_url": OLLAMA_URL,
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": EMBED_MODEL,
            "ollama_base_url": OLLAMA_URL,
            "embedding_dims": EMBED_DIMS,
        },
    },
}

app = FastAPI(title="Mem0 Memory Service", version="2.0.0")
memory_instance = None


def get_memory() -> Memory:
    global memory_instance
    if memory_instance is None:
        memory_instance = Memory.from_config(MEM0_CONFIG)
    return memory_instance


class AddMemoryRequest(BaseModel):
    text: str
    user_id: str = "default_user"
    metadata: Optional[Dict[str, Any]] = None


class SearchMemoryRequest(BaseModel):
    query: str
    user_id: str = "default_user"
    limit: int = 5


@app.get("/health")
def health_check():
    return {"status": "online", "service": "mem0", "embedder": EMBED_MODEL, "dims": EMBED_DIMS}


@app.post("/add")
def add_memory(req: AddMemoryRequest):
    try:
        result = get_memory().add(req.text, user_id=req.user_id, metadata=req.metadata or {})
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/search")
def search_memory(req: SearchMemoryRequest):
    try:
        result = get_memory().search(req.query, top_k=req.limit, filters={"user_id": req.user_id})
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/all/{user_id}")
def get_all_memories(user_id: str):
    try:
        result = get_memory().get_all(filters={"user_id": user_id})
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
"""

TOOLS_REQUIREMENTS_TEMPLATE = """fastapi==0.161.0
uvicorn==0.54.0
"""

TOOLS_SERVER_PY_TEMPLATE = """import hmac
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STACK_DIR = os.path.dirname(BASE_DIR)
WORKSPACE_DIR = os.path.join(STACK_DIR, "workspace")
PORT = 8001
MEMORY_URL = "http://127.0.0.1:8000"


def load_env(path):
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
    return values


ENV = load_env(os.path.join(STACK_DIR, ".env"))
TOKEN = ENV.get("AGENT_TOOLS_TOKEN", "")
HOST = ENV.get("AGENT_TOOLS_HOST", "127.0.0.1")
MEMORY_USER_ID = ENV.get("MEMORY_USER_ID", "default_user")

app = FastAPI(
    title="Agent Tools",
    version="2.0.0",
    description="Long-term memory and host computer tools for the local AI agent.",
)


@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.url.path != "/health":
        supplied = request.headers.get("authorization", "")
        if not TOKEN or not hmac.compare_digest(supplied, "Bearer " + TOKEN):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


def call_memory(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        MEMORY_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode("utf-8", errors="replace")}
    except Exception as exc:
        return {"error": "Memory service unavailable: " + str(exc)}


class SearchMemoryBody(BaseModel):
    query: str = Field(..., description="What to look for, for example: user's favourite language")
    limit: int = Field(5, description="Maximum number of memories to return")


class AddMemoryBody(BaseModel):
    text: str = Field(..., description="The fact to remember, written as a full sentence")


class RunCommandBody(BaseModel):
    command: str = Field(..., description="Shell command to run on the host computer")
    timeout: int = Field(30, description="Timeout in seconds")


class ListDirBody(BaseModel):
    path: str = Field(".", description="Directory path. Relative paths start at the agent workspace folder")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "online", "service": "agent-tools", "port": PORT}


@app.post(
    "/memory/search",
    operation_id="search_memory",
    summary="Search long-term memory for facts about the user, their preferences and earlier conversations.",
)
def search_memory(body: SearchMemoryBody):
    return call_memory("POST", "/search", {"query": body.query, "user_id": MEMORY_USER_ID, "limit": body.limit})


@app.post(
    "/memory/add",
    operation_id="add_memory",
    summary="Save a lasting fact, preference or decision to long-term memory.",
)
def add_memory(body: AddMemoryBody):
    return call_memory("POST", "/add", {"text": body.text, "user_id": MEMORY_USER_ID})


@app.post(
    "/memory/list",
    operation_id="list_memories",
    summary="List everything stored in long-term memory.",
)
def list_memories():
    return call_memory("GET", "/all/" + MEMORY_USER_ID)


@app.post(
    "/host/status",
    operation_id="host_status",
    summary="Get the host computer's operating system, Python version, workspace folder and time.",
)
def host_status():
    return {
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "workspace": WORKSPACE_DIR,
        "timestamp": datetime.now().isoformat(),
    }


@app.post(
    "/host/run_command",
    operation_id="run_command",
    summary="Run a shell command on the host computer inside the agent workspace folder and return its output.",
)
def run_command(body: RunCommandBody):
    try:
        proc = subprocess.run(
            body.command,
            shell=True,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=body.timeout,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "error": "Command timed out after " + str(body.timeout) + " seconds"}
    except Exception as exc:
        return {"returncode": -1, "error": str(exc)}


@app.post(
    "/host/list_dir",
    operation_id="list_dir",
    summary="List the files and folders in a directory on the host computer.",
)
def list_dir(body: ListDirBody):
    try:
        target = os.path.abspath(os.path.join(WORKSPACE_DIR, os.path.expanduser(body.path)))
        items = []
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            items.append({"name": name, "type": "dir" if os.path.isdir(full) else "file"})
        return {"path": target, "items": items}
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    if not TOKEN:
        print("AGENT_TOOLS_TOKEN is missing in .env", flush=True)
        sys.exit(1)
    print("Agent Tools listening on http://" + HOST + ":" + str(PORT), flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
"""

STACK_FILES = [
    ("docker-compose.yml", DOCKER_COMPOSE_TEMPLATE),
    ("litellm_config.yaml", LITELLM_CONFIG_TEMPLATE),
    (os.path.join("postgres_init", "01-create-langfuse-db.sql"), POSTGRES_INIT_TEMPLATE),
    (os.path.join("mem0_service", "Dockerfile"), MEM0_DOCKERFILE_TEMPLATE),
    (os.path.join("mem0_service", "requirements.txt"), MEM0_REQUIREMENTS_TEMPLATE),
    (os.path.join("mem0_service", "main.py"), MEM0_MAIN_PY_TEMPLATE),
    (os.path.join("tools_server", "requirements.txt"), TOOLS_REQUIREMENTS_TEMPLATE),
    (os.path.join("tools_server", "server.py"), TOOLS_SERVER_PY_TEMPLATE),
]

HELP_TEXT = (
    "LOCAL AI AGENT STACK\n"
    "\n"
    "Services\n"
    "  Open WebUI     http://localhost:3000    Chat interface\n"
    "  Langfuse       http://localhost:3001    Trace of every model call\n"
    "  LiteLLM        http://localhost:4000    Proxy to Ollama\n"
    "  Qdrant         http://localhost:6333    Vector database\n"
    "  Mem0           http://localhost:8000    Long-term memory API\n"
    "  Agent Tools    http://localhost:8001    Memory and host tools for the agent\n"
    "  Ollama         http://localhost:11434   Runs on this computer\n"
    "\n"
    "Buttons\n"
    "  FULL SETUP          Generate files, start tools, build and start Docker\n"
    "  GENERATE FILES      Write stack files (asks before overwriting edits)\n"
    "  RUN COMPOSE         Build and start the Docker services\n"
    "  START TOOLS         Start or stop the Agent Tools server\n"
    "  START ENVIRONMENT   Start Agent Tools and Docker services\n"
    "  STOP ENVIRONMENT    Stop Agent Tools and Docker services\n"
    "  F4                  Hide or show this window\n"
    "\n"
    "Before the first run\n"
    "  ollama pull llama3.1\n"
    "  ollama pull nomic-embed-text\n"
    "\n"
    "Passwords, keys and the Langfuse login are in .env in the stack folder."
)


def apply_dark_title_bar(window):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def new_env_values():
    return {
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "LITELLM_MASTER_KEY": "sk-" + secrets.token_hex(24),
        "LITELLM_SALT_KEY": "sk-" + secrets.token_hex(24),
        "LANGFUSE_NEXTAUTH_SECRET": secrets.token_hex(32),
        "LANGFUSE_SALT": secrets.token_hex(32),
        "LANGFUSE_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(16),
        "LANGFUSE_SECRET_KEY": "sk-lf-" + secrets.token_hex(16),
        "LANGFUSE_ADMIN_EMAIL": "admin@agent.local",
        "LANGFUSE_ADMIN_PASSWORD": secrets.token_hex(12),
        "WEBUI_SECRET_KEY": secrets.token_hex(32),
        "AGENT_TOOLS_TOKEN": secrets.token_hex(32),
        "AGENT_TOOLS_HOST": "0.0.0.0" if sys.platform.startswith("linux") else "127.0.0.1",
        "MEMORY_USER_ID": "default_user",
    }


def read_env_file(path):
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
    return values


def probe_service(check):
    kind, target = check
    try:
        if kind == "tcp":
            with socket.create_connection(("127.0.0.1", target), timeout=1.5):
                return True
        with urllib.request.urlopen(target, timeout=2) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:
        return False


class DarkDialog(tk.Toplevel):
    def __init__(self, parent, title, width, height):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=COLORS["bar"])
        self.transient(parent)
        self.iconphoto(False, parent.blank_icon)
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        self.minsize(width // 2, height // 2)
        self.body = tk.Frame(self, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["bar"])
        self.body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        apply_dark_title_bar(self)


class HelpWindow(DarkDialog):
    def __init__(self, parent):
        super().__init__(parent, "Help", 620, 520)
        text = tk.Text(
            self.body,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Consolas", 9),
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            padx=12,
            pady=12,
            wrap=tk.NONE,
        )
        text.insert("1.0", HELP_TEXT)
        text.tag_add("title", "1.0", "1.end")
        text.tag_config("title", foreground=COLORS["accent"])
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True)
        parent.make_button(self.body, "CLOSE", self.destroy).pack(side=tk.BOTTOM, anchor="e", padx=12, pady=12, ipadx=8, ipady=3)


class ConfirmDialog(DarkDialog):
    def __init__(self, parent, title, message, confirm_text, cancel_text):
        super().__init__(parent, title, 520, 300)
        self.result = False
        tk.Label(
            self.body,
            text=message,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Consolas", 9),
            justify=tk.LEFT,
            anchor="nw",
            wraplength=480,
        ).pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        row = tk.Frame(self.body, bg=COLORS["panel"])
        row.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=12)
        parent.make_button(row, confirm_text, self._confirm, danger=True).pack(side=tk.RIGHT, ipadx=8, ipady=3)
        parent.make_button(row, cancel_text, self.destroy).pack(side=tk.RIGHT, padx=(0, 6), ipadx=8, ipady=3)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.focus_force()
        parent.wait_window(self)

    def _confirm(self):
        self.result = True
        self.destroy()


class LocalAIAgentApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Local AI Agent Stack")
        self.configure(bg=COLORS["bar"])
        self.blank_icon = tk.PhotoImage(width=16, height=16)
        self.iconphoto(True, self.blank_icon)
        width, height = 960, 680
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        self.minsize(820, 560)

        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.is_hidden = False
        self.help_window = None
        self.tools_process = None
        self.docker_state = "UNKNOWN"
        self.service_labels = {}
        self.action_buttons = []

        app_dir = os.path.dirname(os.path.abspath(sys.argv[0] or __file__))
        self.target_dir_var = tk.StringVar(value=os.path.join(app_dir, STACK_FOLDER_NAME))

        self._build_header()
        self._build_status_bar()
        self._build_workspace()
        self._setup_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        apply_dark_title_bar(self)

        self.after(100, self._process_ui_queue)
        threading.Thread(target=self._health_loop, daemon=True).start()

        self.log("Local AI Agent Stack ready.", "accent")
        self.log(f"Stack folder: {self.target_dir_var.get()}")
        if HAS_KEYBOARD_LIB:
            self.log("Global hotkey F4 active (hide / show window).")
        else:
            self.log("Hotkey F4 active while the window has focus. Install 'keyboard' for a global hotkey.", "muted")
        threading.Thread(target=self._check_ollama, daemon=True).start()

    def make_button(self, parent, text, command, danger=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=COLORS["danger"] if danger else COLORS["control"],
            fg=COLORS["text"],
            activebackground=COLORS["accent"],
            activeforeground=COLORS["bar"],
            disabledforeground=COLORS["muted"],
            font=("Consolas", 9, "bold"),
            bd=0,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS["bar"],
            highlightcolor=COLORS["accent"],
            cursor="hand2",
        )

    def _build_header(self):
        header = tk.Frame(self, bg=COLORS["bar"], height=40)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="Local AI Agent Stack",
            bg=COLORS["bar"],
            fg=COLORS["accent"],
            font=("Consolas", 11, "bold"),
        ).pack(side=tk.LEFT, padx=12)
        self.make_button(header, "QUIT", self.quit_app, danger=True).pack(side=tk.RIGHT, padx=(0, 10), pady=6, ipadx=10)
        self.make_button(header, "HELP", self.open_help).pack(side=tk.RIGHT, padx=(0, 6), pady=6, ipadx=10)

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=COLORS["bar"], height=26)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self.status_lbl = tk.Label(
            bar,
            text="SERVICES ONLINE: -/- | DOCKER: UNKNOWN | TOOLS: STOPPED",
            bg=COLORS["bar"],
            fg=COLORS["muted"],
            font=("Consolas", 8),
        )
        self.status_lbl.pack(side=tk.LEFT, padx=12)

    def _build_workspace(self):
        outer = tk.Frame(self, bg=COLORS["bar"])
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        top = tk.Frame(outer, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["bar"])
        top.pack(fill=tk.X)
        inner = tk.Frame(top, bg=COLORS["panel"])
        inner.pack(fill=tk.X, padx=12, pady=10)

        tk.Label(inner, text="STACK FOLDER", bg=COLORS["panel"], fg=COLORS["muted"], font=("Consolas", 8, "bold")).pack(anchor="w")
        path_row = tk.Frame(inner, bg=COLORS["panel"])
        path_row.pack(fill=tk.X, pady=(4, 8))
        tk.Entry(
            path_row,
            textvariable=self.target_dir_var,
            bg=COLORS["control"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            font=("Consolas", 10),
            bd=0,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=COLORS["bar"],
            highlightcolor=COLORS["accent"],
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))
        self.make_button(path_row, "BROWSE", self._browse_directory).pack(side=tk.RIGHT, ipadx=8, ipady=3)

        self.drop_zone = tk.Label(
            inner,
            text="Click here to lock the stack folder",
            bg=COLORS["bar"],
            fg=COLORS["accent"],
            font=("Consolas", 9),
            height=2,
            cursor="hand2",
        )
        self.drop_zone.pack(fill=tk.X, pady=(0, 8))
        self.drop_zone.bind("<Button-1>", self._lock_target)

        row1 = tk.Frame(inner, bg=COLORS["panel"])
        row1.pack(fill=tk.X, pady=(0, 6))
        self.btn_full = self.make_button(row1, "FULL SETUP", self._on_full_setup)
        self.btn_generate = self.make_button(row1, "GENERATE FILES", self._on_generate)
        self.btn_compose = self.make_button(row1, "RUN COMPOSE", self._on_run_compose)
        self.btn_tools = self.make_button(row1, "START TOOLS", self._on_toggle_tools)
        for index, btn in enumerate([self.btn_full, self.btn_generate, self.btn_compose, self.btn_tools]):
            btn.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0 if index == 0 else 3, 0))

        row2 = tk.Frame(inner, bg=COLORS["panel"])
        row2.pack(fill=tk.X)
        self.btn_start_env = self.make_button(row2, "START ENVIRONMENT", self._on_start_environment)
        self.btn_stop_env = self.make_button(row2, "STOP ENVIRONMENT", self._on_stop_environment)
        self.btn_start_env.configure(fg=COLORS["accent"])
        self.btn_start_env.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.btn_stop_env.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(3, 0))

        self.action_buttons = [
            self.btn_full,
            self.btn_generate,
            self.btn_compose,
            self.btn_tools,
            self.btn_start_env,
            self.btn_stop_env,
        ]

        bottom = tk.Frame(outer, bg=COLORS["bar"])
        bottom.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        services = tk.Frame(bottom, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["bar"], width=250)
        services.pack(side=tk.LEFT, fill=tk.Y)
        services.pack_propagate(False)
        tk.Label(services, text="SERVICES", bg=COLORS["panel"], fg=COLORS["muted"], font=("Consolas", 8, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
        grid = tk.Frame(services, bg=COLORS["panel"])
        grid.pack(fill=tk.X, padx=12)
        grid.columnconfigure(2, weight=1)
        for row, (name, port, _check) in enumerate(SERVICES):
            tk.Label(grid, text=name, bg=COLORS["panel"], fg=COLORS["text"], font=("Consolas", 9), anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            tk.Label(grid, text=port, bg=COLORS["panel"], fg=COLORS["muted"], font=("Consolas", 8), anchor="w").grid(row=row, column=1, sticky="w", padx=(8, 0))
            state = tk.Label(grid, text="...", bg=COLORS["panel"], fg=COLORS["muted"], font=("Consolas", 9, "bold"), anchor="e")
            state.grid(row=row, column=2, sticky="e")
            self.service_labels[name] = state

        log_panel = tk.Frame(bottom, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["bar"])
        log_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        log_header = tk.Frame(log_panel, bg=COLORS["panel"])
        log_header.pack(fill=tk.X, padx=12, pady=(8, 4))
        tk.Label(log_header, text="LOG", bg=COLORS["panel"], fg=COLORS["muted"], font=("Consolas", 8, "bold")).pack(side=tk.LEFT)
        self.make_button(log_header, "CLEAR", self._clear_logs).pack(side=tk.RIGHT, ipadx=6)

        log_body = tk.Frame(log_panel, bg=COLORS["panel"])
        log_body.pack(fill=tk.BOTH, expand=True, padx=(12, 4), pady=(0, 8))
        self.log_text = tk.Text(
            log_body,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent"],
            selectforeground=COLORS["bar"],
            font=("Consolas", 9),
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(
            log_body,
            command=self.log_text.yview,
            bg=COLORS["control"],
            troughcolor=COLORS["panel"],
            activebackground=COLORS["accent"],
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            width=10,
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_config("accent", foreground=COLORS["accent"])
        self.log_text.tag_config("muted", foreground=COLORS["muted"])
        self.log_text.tag_config("error", foreground=COLORS["danger"])

    def ui(self, func):
        self.ui_queue.put(func)

    def _process_ui_queue(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.after(100, self._process_ui_queue)

    def log(self, message, tag=None):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"

        def append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line, tag or ())
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.ui(append)

    def _clear_logs(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def tools_running(self):
        return self.tools_process is not None and self.tools_process.poll() is None

    def _refresh_status_bar(self, online=None):
        if online is not None:
            self._online_count = online
        count = getattr(self, "_online_count", None)
        services_text = f"{count}/{len(SERVICES)}" if count is not None else f"-/{len(SERVICES)}"
        tools_text = "RUNNING" if self.tools_running() else "STOPPED"
        text = f"SERVICES ONLINE: {services_text} | DOCKER: {self.docker_state} | TOOLS: {tools_text}"
        self.ui(lambda: self.status_lbl.configure(text=text))

    def _set_docker_state(self, state):
        self.docker_state = state
        self._refresh_status_bar()

    def _health_loop(self):
        while not self.stop_event.is_set():
            results = {name: probe_service(check) for name, _port, check in SERVICES}

            def apply(results=results):
                for name, online in results.items():
                    self.service_labels[name].configure(
                        text="ONLINE" if online else "OFFLINE",
                        fg=COLORS["accent"] if online else COLORS["muted"],
                    )

            self.ui(apply)
            self._refresh_status_bar(sum(results.values()))
            self.stop_event.wait(4)

    def base_dir(self):
        return os.path.abspath(self.target_dir_var.get().strip() or STACK_FOLDER_NAME)

    def _browse_directory(self):
        selected = filedialog.askdirectory(initialdir=os.path.dirname(self.base_dir()), title="Select Stack Folder Location")
        if selected:
            target = selected if os.path.basename(selected).lower() == STACK_FOLDER_NAME else os.path.join(selected, STACK_FOLDER_NAME)
            self.target_dir_var.set(os.path.normpath(target))
            self._lock_target()

    def _lock_target(self, event=None):
        self.drop_zone.configure(text=f"Stack folder: {self.base_dir()}")
        self.log(f"Stack folder locked: {self.base_dir()}", "accent")

    def _check_ollama(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            self.log("Ollama is not responding on port 11434. Start Ollama before the stack.", "error")
            return False
        names = {model.get("name", "") for model in data.get("models", [])}
        missing = [name for name in REQUIRED_OLLAMA_MODELS if name not in names]
        if missing:
            for name in missing:
                self.log(f"Ollama model missing: {name}  ->  run: ollama pull {name.replace(':latest', '')}", "error")
        else:
            self.log("Ollama is online with llama3.1 and nomic-embed-text.", "accent")
        return True

    def changed_stack_files(self):
        base = self.base_dir()
        changed = []
        for rel_path, content in STACK_FILES:
            path = os.path.join(base, rel_path)
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as existing:
                        if existing.read() != content:
                            changed.append(rel_path)
                except Exception:
                    changed.append(rel_path)
        return changed

    def ask_overwrite(self):
        changed = self.changed_stack_files()
        if not changed:
            return False
        message = (
            "These files in the stack folder differ from the generated version:\n\n"
            + "\n".join(f"  {path}" for path in changed)
            + "\n\nOverwrite them, or keep your versions and only create missing files?"
        )
        return ConfirmDialog(self, "Files Changed", message, "OVERWRITE", "KEEP MINE").result

    def ensure_env_file(self, base):
        env_path = os.path.join(base, ".env")
        existing = read_env_file(env_path)
        missing = {key: value for key, value in new_env_values().items() if key not in existing}
        if not missing:
            return existing
        new_file = not os.path.isfile(env_path)
        with open(env_path, "a", encoding="utf-8", newline="\n") as env_file:
            if new_file:
                env_file.write("# Local AI Agent Stack secrets. Keep this file private.\n")
            for key, value in missing.items():
                env_file.write(f"{key}={value}\n")
        self.log("  [+] .env created with new passwords and keys" if new_file else f"  [+] .env: added {', '.join(missing)}")
        existing.update(missing)
        return existing

    def generate_stack_files(self, overwrite):
        base = self.base_dir()
        self.ui(lambda: self.drop_zone.configure(text=f"Stack folder: {base}"))
        self.log(f">>> Writing stack files in {base}", "accent")
        try:
            os.makedirs(os.path.join(base, "workspace"), exist_ok=True)
            self.ensure_env_file(base)
            for rel_path, content in STACK_FILES:
                path = os.path.join(base, rel_path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as existing:
                        if existing.read() == content:
                            continue
                    if not overwrite:
                        self.log(f"  [=] Kept your version: {rel_path}", "muted")
                        continue
                with open(path, "w", encoding="utf-8", newline="\n") as out:
                    out.write(content)
                self.log(f"  [+] {rel_path}")
            self.log("Stack files are ready.", "accent")
            return True
        except Exception as exc:
            self.log(f"Error writing stack files: {exc}", "error")
            return False

    def stream_process(self, cmd, cwd, prefix, tag=None):
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log(f"{prefix} {line}", tag)
        proc.stdout.close()
        return proc.wait()

    def prepare_tools_python(self, base):
        tools_dir = os.path.join(base, "tools_server")
        venv_dir = os.path.join(tools_dir, ".venv")
        if sys.platform == "win32":
            python = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            python = os.path.join(venv_dir, "bin", "python")
        requirements = os.path.join(tools_dir, "requirements.txt")
        marker = os.path.join(venv_dir, "installed-requirements.txt")
        try:
            if not os.path.isfile(python):
                self.log("Creating a Python environment for Agent Tools (first run only)...", "muted")
                if self.stream_process([sys.executable, "-m", "venv", venv_dir], tools_dir, "[VENV]", "muted") != 0:
                    self.log("Could not create the Python environment for Agent Tools.", "error")
                    return None
            with open(requirements, encoding="utf-8") as req_file:
                required = req_file.read()
            installed = ""
            if os.path.isfile(marker):
                with open(marker, encoding="utf-8") as marker_file:
                    installed = marker_file.read()
            if installed != required:
                self.log("Installing Agent Tools packages...", "muted")
                cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "-r", requirements]
                if self.stream_process(cmd, tools_dir, "[PIP]", "muted") != 0:
                    self.log("Package installation for Agent Tools failed.", "error")
                    return None
                with open(marker, "w", encoding="utf-8") as marker_file:
                    marker_file.write(required)
            return python
        except Exception as exc:
            self.log(f"Error preparing Agent Tools: {exc}", "error")
            return None

    def start_tools_server(self):
        if self.tools_running():
            self.log("Agent Tools is already running on port 8001.", "accent")
            return True
        base = self.base_dir()
        script = os.path.join(base, "tools_server", "server.py")
        if not os.path.isfile(script):
            self.log("tools_server/server.py not found. Generate the stack files first.", "error")
            return False
        python = self.prepare_tools_python(base)
        if not python:
            return False
        try:
            os.makedirs(os.path.join(base, "workspace"), exist_ok=True)
            self.tools_process = subprocess.Popen(
                [python, "-u", script],
                cwd=os.path.join(base, "workspace"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
        except Exception as exc:
            self.log(f"Error starting Agent Tools: {exc}", "error")
            return False
        threading.Thread(target=self._read_tools_output, args=(self.tools_process,), daemon=True).start()
        self.ui(lambda: self.btn_tools.configure(text="STOP TOOLS"))
        self._refresh_status_bar()
        return True

    def _read_tools_output(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log(f"[TOOLS] {line}")
        proc.stdout.close()
        code = proc.wait()
        self.log(f"Agent Tools stopped (exit code {code}).", "muted")
        if self.tools_process is proc:
            self.tools_process = None
        self.ui(lambda: self.btn_tools.configure(text="START TOOLS"))
        self._refresh_status_bar()

    def stop_tools_server(self):
        proc = self.tools_process
        if proc is None or proc.poll() is not None:
            return
        self.log(">>> Stopping Agent Tools...", "muted")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def run_compose_up(self, build):
        base = self.base_dir()
        compose_file = os.path.join(base, "docker-compose.yml")
        cmd = ["docker", "compose", "-f", compose_file, "up", "-d"]
        if build:
            cmd.append("--build")
        self._set_docker_state("STARTING")
        self.log(f">>> {' '.join(cmd)}", "accent")
        try:
            code = self.stream_process(cmd, base, "[DOCKER]")
        except FileNotFoundError:
            self._set_docker_state("NOT FOUND")
            self.log("Docker was not found. Install Docker Desktop and make sure it is running.", "error")
            return False
        except Exception as exc:
            self._set_docker_state("ERROR")
            self.log(f"Error running Docker Compose: {exc}", "error")
            return False
        if code != 0:
            self._set_docker_state("ERROR")
            self.log(f"docker compose up failed (exit code {code}).", "error")
            return False
        self._set_docker_state("RUNNING")
        env = read_env_file(os.path.join(base, ".env"))
        self.log("Docker services are up. First start can take a minute until all show ONLINE.", "accent")
        self.log("  Open WebUI : http://localhost:3000", "accent")
        self.log("  Langfuse   : http://localhost:3001", "accent")
        self.log(f"  Langfuse login: {env.get('LANGFUSE_ADMIN_EMAIL', '')} / {env.get('LANGFUSE_ADMIN_PASSWORD', '')}", "accent")
        return True

    def run_compose_stop(self):
        base = self.base_dir()
        compose_file = os.path.join(base, "docker-compose.yml")
        if not os.path.isfile(compose_file):
            self.log("No docker-compose.yml in the stack folder.", "muted")
            return
        self._set_docker_state("STOPPING")
        try:
            code = self.stream_process(["docker", "compose", "-f", compose_file, "stop"], base, "[DOCKER]", "muted")
        except FileNotFoundError:
            self._set_docker_state("NOT FOUND")
            self.log("Docker was not found.", "error")
            return
        except Exception as exc:
            self._set_docker_state("ERROR")
            self.log(f"Error stopping Docker Compose: {exc}", "error")
            return
        if code == 0:
            self._set_docker_state("STOPPED")
            self.log("Docker services stopped.", "accent")
        else:
            self._set_docker_state("ERROR")
            self.log(f"docker compose stop failed (exit code {code}).", "error")

    def run_task(self, worker):
        if self.busy:
            self.log("Another operation is still running.", "muted")
            return
        self.busy = True
        for btn in self.action_buttons:
            btn.configure(state=tk.DISABLED)

        def runner():
            try:
                worker()
            except Exception as exc:
                self.log(f"Unexpected error: {exc}", "error")
            finally:
                def release():
                    self.busy = False
                    for btn in self.action_buttons:
                        btn.configure(state=tk.NORMAL)
                self.ui(release)

        threading.Thread(target=runner, daemon=True).start()

    def _on_full_setup(self):
        overwrite = self.ask_overwrite()

        def worker():
            self.log("=== FULL SETUP ===", "accent")
            if not self.generate_stack_files(overwrite):
                return
            self._check_ollama()
            self.start_tools_server()
            self.run_compose_up(build=True)

        self.run_task(worker)

    def _on_generate(self):
        overwrite = self.ask_overwrite()
        self.run_task(lambda: self.generate_stack_files(overwrite))

    def _on_run_compose(self):
        def worker():
            if self.generate_stack_files(overwrite=False):
                self.run_compose_up(build=True)

        self.run_task(worker)

    def _on_toggle_tools(self):
        def worker():
            if self.tools_running():
                self.stop_tools_server()
            elif self.generate_stack_files(overwrite=False):
                self.start_tools_server()

        self.run_task(worker)

    def _on_start_environment(self):
        def worker():
            self.log("=== START ENVIRONMENT ===", "accent")
            if not self.generate_stack_files(overwrite=False):
                return
            self._check_ollama()
            self.start_tools_server()
            self.run_compose_up(build=False)

        self.run_task(worker)

    def _on_stop_environment(self):
        def worker():
            self.log("=== STOP ENVIRONMENT ===", "muted")
            self.stop_tools_server()
            self.run_compose_stop()

        self.run_task(worker)

    def _setup_hotkeys(self):
        self.bind("<F4>", lambda event: self.toggle_visibility())
        if HAS_KEYBOARD_LIB:
            try:
                keyboard.add_hotkey("f4", lambda: self.ui(self.toggle_visibility))
            except Exception:
                pass

    def toggle_visibility(self):
        if self.is_hidden:
            self.deiconify()
            self.is_hidden = False
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            self.after(250, lambda: self.attributes("-topmost", False))
        else:
            if self.help_window is not None and self.help_window.winfo_exists():
                self.help_window.destroy()
            self.withdraw()
            self.is_hidden = True

    def open_help(self):
        if self.help_window is None or not self.help_window.winfo_exists():
            self.help_window = HelpWindow(self)
        else:
            self.help_window.lift()

    def quit_app(self):
        self.stop_event.set()
        self.stop_tools_server()
        if HAS_KEYBOARD_LIB:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = LocalAIAgentApp()
    app.mainloop()
