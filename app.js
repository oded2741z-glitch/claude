(() => {
  const $ = (id) => document.getElementById(id);

  const dropzone = $('dropzone');
  const fileInput = $('fileInput');
  const browseBtn = $('browseBtn');
  const workspace = $('workspace');
  const video = $('video');
  const canvas = $('hiddenCanvas');
  const ctx = canvas.getContext('2d');
  const frameCountInput = $('frameCount');
  const extractBtn = $('extractBtn');
  const captureBtn = $('captureBtn');
  const framesList = $('framesList');
  const framesCount = $('framesCount');
  const clearBtn = $('clearBtn');
  const buildBtn = $('buildBtn');
  const delayInput = $('delay');
  const widthInput = $('gifWidth');
  const qualityInput = $('quality');
  const status = $('status');
  const result = $('result');
  const gifImage = $('gifImage');
  const downloadLink = $('downloadLink');

  let frames = [];

  // ---- File handling ----
  function loadFile(file) {
    if (!file || !file.type.startsWith('video/')) {
      setStatus('אנא בחרו קובץ וידאו תקין (MP4 / WebM / MOV).', 'err');
      return;
    }
    const url = URL.createObjectURL(file);
    video.src = url;
    video.onloadedmetadata = () => {
      workspace.classList.remove('hidden');
      dropzone.classList.add('hidden');
      setStatus(`וידאו נטען · משך ${video.duration.toFixed(1)} שניות`, 'good');
    };
  }

  browseBtn.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => loadFile(e.target.files[0]));

  ['dragenter','dragover'].forEach(ev =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('drag'); }));
  ['dragleave','drop'].forEach(ev =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove('drag'); }));
  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]);
  });

  // ---- Frame extraction ----
  function captureAt(time) {
    return new Promise((resolve, reject) => {
      const onSeeked = () => {
        video.removeEventListener('seeked', onSeeked);
        try {
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          canvas.toBlob((blob) => {
            const url = URL.createObjectURL(blob);
            resolve({ url, time, width: canvas.width, height: canvas.height });
          }, 'image/png');
        } catch (e) { reject(e); }
      };
      video.addEventListener('seeked', onSeeked);
      video.currentTime = Math.min(Math.max(time, 0), Math.max(video.duration - 0.05, 0));
    });
  }

  async function extractAuto() {
    if (!video.duration || isNaN(video.duration)) {
      setStatus('הוידאו עוד לא נטען לגמרי, נסו שוב.', 'err');
      return;
    }
    const n = Math.max(2, Math.min(40, parseInt(frameCountInput.value, 10) || 10));
    extractBtn.disabled = true;
    setStatus(`מחלץ ${n} פריימים…`);
    try {
      video.pause();
      const step = video.duration / (n + 1);
      for (let i = 1; i <= n; i++) {
        const f = await captureAt(step * i);
        addFrame(f);
        setStatus(`חולץ פריים ${i}/${n}…`);
      }
      setStatus(`נוספו ${n} פריימים ✓`, 'good');
    } catch (e) {
      setStatus('שגיאה בחילוץ פריימים: ' + e.message, 'err');
    } finally {
      extractBtn.disabled = false;
    }
  }

  async function captureCurrent() {
    try {
      video.pause();
      const f = await captureAt(video.currentTime);
      addFrame(f);
      setStatus('פריים נוסף ✓', 'good');
    } catch (e) {
      setStatus('שגיאה: ' + e.message, 'err');
    }
  }

  extractBtn.addEventListener('click', extractAuto);
  captureBtn.addEventListener('click', captureCurrent);

  // ---- Frame list (drag to reorder) ----
  function addFrame(f) {
    frames.push(f);
    renderFrames();
  }

  function renderFrames() {
    framesList.innerHTML = '';
    frames.forEach((f, idx) => {
      const el = document.createElement('div');
      el.className = 'frame-item';
      el.draggable = true;
      el.dataset.index = idx;
      el.innerHTML = `
        <img src="${f.url}" alt="frame ${idx + 1}">
        <span class="order">${idx + 1}</span>
        <button class="remove" title="הסר">✕</button>
      `;
      el.querySelector('.remove').addEventListener('click', (e) => {
        e.stopPropagation();
        URL.revokeObjectURL(f.url);
        frames.splice(idx, 1);
        renderFrames();
      });

      el.addEventListener('dragstart', () => el.classList.add('dragging'));
      el.addEventListener('dragend', () => el.classList.remove('dragging'));
      el.addEventListener('dragover', (e) => {
        e.preventDefault();
        const dragging = document.querySelector('.frame-item.dragging');
        if (!dragging || dragging === el) return;
        const from = parseInt(dragging.dataset.index, 10);
        const to = parseInt(el.dataset.index, 10);
        const item = frames.splice(from, 1)[0];
        frames.splice(to, 0, item);
        renderFrames();
      });

      framesList.appendChild(el);
    });
    framesCount.textContent = `(${frames.length})`;
    buildBtn.disabled = frames.length < 2;
  }

  clearBtn.addEventListener('click', () => {
    frames.forEach(f => URL.revokeObjectURL(f.url));
    frames = [];
    renderFrames();
    result.classList.add('hidden');
  });

  // ---- GIF building ----
  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = url;
    });
  }

  async function buildGif() {
    if (frames.length < 2) return;
    if (typeof GIF === 'undefined') {
      setStatus('ספריית GIF לא נטענה. בדקו את החיבור לאינטרנט.', 'err');
      return;
    }

    buildBtn.disabled = true;
    setStatus('בונה GIF…');

    const delay = Math.max(50, parseInt(delayInput.value, 10) || 400);
    const targetW = Math.max(120, Math.min(1280, parseInt(widthInput.value, 10) || 480));
    const quality = Math.max(1, Math.min(30, parseInt(qualityInput.value, 10) || 10));

    const first = await loadImage(frames[0].url);
    const ratio = first.naturalHeight / first.naturalWidth;
    const w = targetW;
    const h = Math.round(targetW * ratio);

    const gif = new GIF({
      workers: 2,
      quality,
      width: w,
      height: h,
      workerScript: 'https://cdn.jsdelivr.net/npm/gif.js.optimized@1.0.1/dist/gif.worker.js'
    });

    const tmp = document.createElement('canvas');
    tmp.width = w; tmp.height = h;
    const tctx = tmp.getContext('2d');

    for (const f of frames) {
      const img = await loadImage(f.url);
      tctx.clearRect(0, 0, w, h);
      tctx.drawImage(img, 0, 0, w, h);
      gif.addFrame(tctx, { copy: true, delay });
    }

    gif.on('progress', (p) => setStatus(`בונה GIF… ${Math.round(p * 100)}%`));
    gif.on('finished', (blob) => {
      const url = URL.createObjectURL(blob);
      gifImage.src = url;
      downloadLink.href = url;
      result.classList.remove('hidden');
      setStatus(`GIF מוכן · ${(blob.size / 1024).toFixed(0)} KB`, 'good');
      buildBtn.disabled = false;
      result.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });

    try {
      gif.render();
    } catch (e) {
      setStatus('שגיאה ביצירת GIF: ' + e.message, 'err');
      buildBtn.disabled = false;
    }
  }

  buildBtn.addEventListener('click', buildGif);

  function setStatus(msg, kind) {
    status.textContent = msg;
    status.className = 'status' + (kind ? ' ' + kind : '');
  }
})();
