"use strict";

const ACCENT = "#389379";
const SELECTED = "#FFFFFF";
const HANDLE = 8;

const state = {
    folder: null,
    images: [],
    index: 0,
    mode: "draw",          // "draw" | "edit"
    boxes: [],             // {tag, x1, y1, x2, y2} in canvas pixels
    imgW: 0,
    imgH: 0,
    selected: null,
    // interaction
    drawing: false,
    dragIdx: null,
    dragHandle: null,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
};

const img = new Image();
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

// ---- DOM refs ----
const el = (id) => document.getElementById(id);
const settingsView = el("settings-view");
const viewerView = el("viewer-view");
const statusEl = el("status");
const folderSelect = el("folder-select");
const activeTagInput = el("active-tag");
const modeBtn = el("mode-btn");
const counterEl = el("image-counter");
const progressWrap = el("progress-wrap");
const progressFill = el("progress-fill");

// ============ SETTINGS / EXTRACTION ============
async function loadFolders() {
    const res = await fetch("/api/folders");
    const folders = await res.json();
    folderSelect.innerHTML = "";
    if (folders.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "(no folders yet)";
        opt.value = "";
        folderSelect.appendChild(opt);
        return;
    }
    folders.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f;
        opt.textContent = f;
        folderSelect.appendChild(opt);
    });
}

el("extract-btn").addEventListener("click", async () => {
    const fileInput = el("video-input");
    const folder = el("folder-name").value.trim();
    const interval = el("interval").value;

    if (!fileInput.files.length || !folder || !interval) {
        setStatus("Please fill in all extraction fields.", true);
        return;
    }

    const fd = new FormData();
    fd.append("video", fileInput.files[0]);
    fd.append("folder", folder);
    fd.append("interval", interval);

    el("extract-btn").disabled = true;
    setStatus("Uploading & processing... Please wait.", false);
    progressWrap.classList.remove("hidden");
    progressFill.style.width = "0%";

    let res;
    try {
        res = await fetch("/api/extract", { method: "POST", body: fd });
    } catch (e) {
        finishExtract("Upload failed.", true);
        return;
    }
    const data = await res.json();
    if (!res.ok) {
        finishExtract(data.error || "Extraction failed.", true);
        return;
    }
    pollJob(data.job_id, data.folder);
});

function pollJob(jobId, folder) {
    const timer = setInterval(async () => {
        const res = await fetch(`/api/job/${jobId}`);
        if (!res.ok) return;
        const job = await res.json();
        progressFill.style.width = (job.progress || 0) + "%";
        if (job.status === "running") {
            setStatus(`Processing... ${job.saved || 0} frames extracted.`, false);
        } else if (job.status === "done") {
            clearInterval(timer);
            finishExtract(job.message || "Done!", false);
            openFolder(folder);
        } else if (job.status === "error") {
            clearInterval(timer);
            finishExtract(job.message || "Error.", true);
        }
    }, 600);
}

function finishExtract(message, isError) {
    el("extract-btn").disabled = false;
    progressWrap.classList.add("hidden");
    setStatus(message, isError);
    loadFolders();
}

el("load-btn").addEventListener("click", () => {
    const folder = folderSelect.value;
    if (folder) openFolder(folder);
});

async function openFolder(folder) {
    const res = await fetch(`/api/folder/${encodeURIComponent(folder)}`);
    if (!res.ok) {
        setStatus("Could not open folder.", true);
        return;
    }
    const data = await res.json();
    if (!data.images.length) {
        setStatus(`No images found in '${folder}'.`, true);
        return;
    }
    state.folder = folder;
    state.images = data.images;
    state.index = 0;
    settingsView.classList.add("hidden");
    viewerView.classList.remove("hidden");
    showImage();
}

function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.style.color = isError ? "#D32F2F" : ACCENT;
}

// ============ VIEWER ============
function showImage() {
    if (!state.images.length) {
        counterEl.textContent = "No images available.";
        return;
    }
    const name = state.images[state.index];
    counterEl.textContent = `${name} - (${state.index + 1} / ${state.images.length})`;
    state.selected = null;
    state.boxes = [];

    img.onload = async () => {
        // Scale down to a max display size, preserving aspect (mirrors the desktop thumbnail).
        const maxW = 700, maxH = 450;
        let { width: w, height: h } = img;
        const ratio = Math.min(maxW / w, maxH / h, 1);
        state.imgW = Math.round(w * ratio);
        state.imgH = Math.round(h * ratio);
        canvas.width = state.imgW;
        canvas.height = state.imgH;
        await loadAnnotations(name);
        redraw();
    };
    img.src = `/api/image/${encodeURIComponent(state.folder)}/${encodeURIComponent(name)}`;

    el("prev-btn").disabled = state.index === 0;
    el("next-btn").disabled = state.index === state.images.length - 1;
}

async function loadAnnotations(name) {
    const res = await fetch(
        `/api/annotations/${encodeURIComponent(state.folder)}/${encodeURIComponent(name)}`
    );
    const data = await res.json();
    state.boxes = (data.boxes || []).map((b) => ({
        tag: b.tag,
        x1: (b.x_center - b.width / 2) * state.imgW,
        y1: (b.y_center - b.height / 2) * state.imgH,
        x2: (b.x_center + b.width / 2) * state.imgW,
        y2: (b.y_center + b.height / 2) * state.imgH,
    }));
}

async function saveAnnotations() {
    if (!state.images.length) return;
    const name = state.images[state.index];
    const boxes = state.boxes.map((b) => {
        const x1 = Math.min(b.x1, b.x2), x2 = Math.max(b.x1, b.x2);
        const y1 = Math.min(b.y1, b.y2), y2 = Math.max(b.y1, b.y2);
        return {
            tag: b.tag,
            x_center: ((x1 + x2) / 2) / state.imgW,
            y_center: ((y1 + y2) / 2) / state.imgH,
            width: (x2 - x1) / state.imgW,
            height: (y2 - y1) / state.imgH,
        };
    });
    await fetch(
        `/api/annotations/${encodeURIComponent(state.folder)}/${encodeURIComponent(name)}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ boxes }),
        }
    );
}

function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, state.imgW, state.imgH);

    state.boxes.forEach((b, idx) => {
        const x1 = Math.min(b.x1, b.x2), y1 = Math.min(b.y1, b.y2);
        const x2 = Math.max(b.x1, b.x2), y2 = Math.max(b.y1, b.y2);
        const isSel = idx === state.selected;
        ctx.strokeStyle = isSel ? SELECTED : ACCENT;
        ctx.lineWidth = 2;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

        ctx.fillStyle = isSel ? SELECTED : ACCENT;
        ctx.font = "bold 13px Arial";
        ctx.fillText(b.tag, x1 + 2, Math.max(12, y1 - 4));

        if (isSel && state.mode === "edit") {
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]].forEach(([hx, hy]) => {
                ctx.fillStyle = SELECTED;
                ctx.fillRect(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE);
            });
        }
    });
}

// ---- pointer position helper (handles canvas CSS scaling) ----
function pos(evt) {
    const rect = canvas.getBoundingClientRect();
    const cx = (evt.touches ? evt.touches[0].clientX : evt.clientX) - rect.left;
    const cy = (evt.touches ? evt.touches[0].clientY : evt.clientY) - rect.top;
    return {
        x: cx * (canvas.width / rect.width),
        y: cy * (canvas.height / rect.height),
    };
}

function hitTest(x, y) {
    for (let idx = state.boxes.length - 1; idx >= 0; idx--) {
        const b = state.boxes[idx];
        const xmin = Math.min(b.x1, b.x2), xmax = Math.max(b.x1, b.x2);
        const ymin = Math.min(b.y1, b.y2), ymax = Math.max(b.y1, b.y2);
        const corners = {
            nw: [xmin, ymin], ne: [xmax, ymin],
            se: [xmax, ymax], sw: [xmin, ymax],
        };
        for (const [h, [hx, hy]] of Object.entries(corners)) {
            if (Math.abs(x - hx) < HANDLE && Math.abs(y - hy) < HANDLE) {
                return { idx, handle: h };
            }
        }
        if (x >= xmin && x <= xmax && y >= ymin && y <= ymax) {
            return { idx, handle: "move" };
        }
    }
    return null;
}

function onDown(evt) {
    evt.preventDefault();
    const { x, y } = pos(evt);
    state.lastX = x;
    state.lastY = y;

    if (state.mode === "draw") {
        const tag = activeTagInput.value.trim().replace(/\s+/g, "_");
        if (!tag) {
            alert("Please enter an Active Tag before drawing.");
            return;
        }
        state.drawing = true;
        state.startX = x;
        state.startY = y;
        state.boxes.push({ tag, x1: x, y1: y, x2: x, y2: y });
        state.selected = state.boxes.length - 1;
    } else {
        const hit = hitTest(x, y);
        if (hit) {
            state.dragIdx = hit.idx;
            state.dragHandle = hit.handle;
            state.selected = hit.idx;
            activeTagInput.value = state.boxes[hit.idx].tag;
        } else {
            state.selected = null;
        }
    }
    redraw();
}

function onMove(evt) {
    if (state.mode === "draw" && state.drawing) {
        const { x, y } = pos(evt);
        const b = state.boxes[state.boxes.length - 1];
        b.x2 = x;
        b.y2 = y;
        redraw();
    } else if (state.mode === "edit" && state.dragIdx !== null) {
        const { x, y } = pos(evt);
        const dx = x - state.lastX;
        const dy = y - state.lastY;
        state.lastX = x;
        state.lastY = y;
        const b = state.boxes[state.dragIdx];
        switch (state.dragHandle) {
            case "move": b.x1 += dx; b.y1 += dy; b.x2 += dx; b.y2 += dy; break;
            case "nw": b.x1 += dx; b.y1 += dy; break;
            case "ne": b.x2 += dx; b.y1 += dy; break;
            case "se": b.x2 += dx; b.y2 += dy; break;
            case "sw": b.x1 += dx; b.y2 += dy; break;
        }
        redraw();
    }
}

function onUp() {
    if (state.mode === "draw" && state.drawing) {
        state.drawing = false;
        const b = state.boxes[state.boxes.length - 1];
        if (Math.abs(b.x2 - b.x1) < 5 || Math.abs(b.y2 - b.y1) < 5) {
            state.boxes.pop();
            state.selected = null;
            redraw();
            return;
        }
        saveAnnotations();
    } else if (state.mode === "edit" && state.dragIdx !== null) {
        state.dragIdx = null;
        state.dragHandle = null;
        saveAnnotations();
    }
}

canvas.addEventListener("mousedown", onDown);
canvas.addEventListener("mousemove", onMove);
window.addEventListener("mouseup", onUp);
canvas.addEventListener("touchstart", onDown, { passive: false });
canvas.addEventListener("touchmove", (e) => { e.preventDefault(); onMove(e); }, { passive: false });
window.addEventListener("touchend", onUp);

// ---- controls ----
modeBtn.addEventListener("click", () => {
    if (state.mode === "draw") {
        state.mode = "edit";
        modeBtn.textContent = "Mode: EDIT";
        modeBtn.style.background = ACCENT;
        modeBtn.style.color = SELECTED;
        canvas.style.cursor = "default";
    } else {
        state.mode = "draw";
        modeBtn.textContent = "Mode: DRAW";
        modeBtn.style.background = "";
        modeBtn.style.color = "";
        canvas.style.cursor = "crosshair";
        state.selected = null;
    }
    redraw();
});

el("update-tag-btn").addEventListener("click", () => {
    if (state.selected === null) return;
    const tag = activeTagInput.value.trim().replace(/\s+/g, "_");
    if (!tag) return;
    state.boxes[state.selected].tag = tag;
    redraw();
    saveAnnotations();
});

el("delete-box-btn").addEventListener("click", () => {
    if (state.selected === null) return;
    state.boxes.splice(state.selected, 1);
    state.selected = null;
    redraw();
    saveAnnotations();
});

el("delete-image-btn").addEventListener("click", async () => {
    if (!state.images.length) return;
    const name = state.images[state.index];
    if (!confirm(`Delete image ${name}?`)) return;
    const res = await fetch(
        `/api/image/${encodeURIComponent(state.folder)}/${encodeURIComponent(name)}`,
        { method: "DELETE" }
    );
    const data = await res.json();
    state.images = data.images;
    if (state.index >= state.images.length) {
        state.index = Math.max(0, state.images.length - 1);
    }
    if (state.images.length) {
        showImage();
    } else {
        backToSettings();
        setStatus("Folder is now empty.", false);
    }
});

el("prev-btn").addEventListener("click", () => {
    if (state.index > 0) { state.index--; showImage(); }
});
el("next-btn").addEventListener("click", () => {
    if (state.index < state.images.length - 1) { state.index++; showImage(); }
});

function backToSettings() {
    viewerView.classList.add("hidden");
    settingsView.classList.remove("hidden");
}
el("back-btn").addEventListener("click", backToSettings);

document.addEventListener("keydown", (e) => {
    if (viewerView.classList.contains("hidden")) return;
    if (document.activeElement === activeTagInput) return;
    if (e.key === "ArrowLeft") el("prev-btn").click();
    if (e.key === "ArrowRight") el("next-btn").click();
});

// ---- help modal ----
el("help-btn").addEventListener("click", () => el("help-modal").classList.remove("hidden"));
el("help-close").addEventListener("click", () => el("help-modal").classList.add("hidden"));

// ---- init ----
loadFolders();
