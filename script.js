const urlInput = document.getElementById('urlInput');
const pasteBtn = document.getElementById('pasteBtn');
const downloadBtn = document.getElementById('downloadBtn');
const statusEl = document.getElementById('status');
const qualityRow = document.getElementById('qualityRow');
const pills = document.querySelectorAll('.pill');
const formatInputs = document.querySelectorAll('input[name="format"]');

let selectedQuality = '1080';

pills.forEach(p => p.addEventListener('click', () => {
  pills.forEach(x => x.classList.remove('active'));
  p.classList.add('active');
  selectedQuality = p.dataset.q;
}));

formatInputs.forEach(r => r.addEventListener('change', () => {
  qualityRow.style.display = r.value === 'mp3' && r.checked ? 'none' : 'flex';
}));

pasteBtn.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    urlInput.value = text;
    setStatus('', '');
  } catch {
    setStatus('Cannot access clipboard. Paste manually with Ctrl+V', 'error');
  }
});

function setStatus(msg, type) {
  statusEl.textContent = msg;
  statusEl.className = 'status' + (type ? ' ' + type : '');
}

function isValidYouTubeUrl(url) {
  return /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?v=|shorts\/|embed\/)|youtu\.be\/)[\w-]{11}/.test(url);
}

function extractVideoId(url) {
  const m = url.match(/(?:v=|youtu\.be\/|shorts\/|embed\/)([\w-]{11})/);
  return m ? m[1] : null;
}

downloadBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  const format = document.querySelector('input[name="format"]:checked').value;

  if (!url) {
    setStatus('Please paste a YouTube link', 'error');
    return;
  }
  if (!isValidYouTubeUrl(url)) {
    setStatus('Invalid link. Make sure it is a YouTube URL', 'error');
    return;
  }

  const videoId = extractVideoId(url);
  downloadBtn.disabled = true;
  setStatus('Processing video...', 'loading');

  try {
    // NOTE: Real YouTube downloading requires a backend (yt-dlp / similar).
    // Update API_ENDPOINT to your backend that returns { downloadUrl, title }.
    const API_ENDPOINT = '/api/download';

    const res = await fetch(API_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videoId, format, quality: selectedQuality })
    });

    if (!res.ok) throw new Error('Backend not available');

    const data = await res.json();
    setStatus('✓ Ready! Your download is starting...', 'success');
    const a = document.createElement('a');
    a.href = data.downloadUrl;
    a.download = (data.title || 'youtube-video') + '.' + format;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (err) {
    setStatus('⚠ A backend with yt-dlp is required for real downloads. See README.', 'error');
  } finally {
    downloadBtn.disabled = false;
  }
});

urlInput.addEventListener('keypress', e => {
  if (e.key === 'Enter') downloadBtn.click();
});
