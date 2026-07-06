"""
Flask web app for setup mode.

Endpoints:
  GET  /                    → setup UI (MJPEG + line configurator + status)
  GET  /stream              → MJPEG multipart stream
  GET  /api/status          → JSON status snapshot
  POST /api/line            → save new line config {point1, point2, in_direction}
  GET  /generate_204        → 204 for Android captive portal detection
  GET  /hotspot-detect.html → redirect for iOS captive portal detection
  GET  /ncsi.txt            → redirect for Windows NCSI
  GET  /favicon.ico         → 204 (silence browser noise)

Any request whose Host header doesn't match the hotspot IP is redirected to /
(this triggers the captive portal browser on mobile clients).
"""
import io
import logging
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Deferred import so Flask is only required when setup mode is active.
_app = None
_state = None  # SharedState instance, set by create_app()


def create_app(shared_state, hotspot_ip: str = "10.42.0.1"):
    """Create and return the Flask application."""
    global _app, _state
    from flask import Flask, Response, jsonify, redirect, render_template_string, request

    _state = shared_state
    _app = Flask(__name__)
    _app.config["HOTSPOT_IP"] = hotspot_ip

    # ── captive portal redirect ───────────────────────────────────────────────

    @_app.before_request
    def _captive_portal_check():
        """Redirect unknown hostnames so OS captive portal detector fires."""
        _state.touch()
        host = request.headers.get("Host", "").split(":")[0]
        if host not in (hotspot_ip, "localhost", "127.0.0.1"):
            # Special Android endpoint: must return 204, not redirect
            if request.path == "/generate_204":
                return Response(status=204)
            return redirect(f"http://{hotspot_ip}/", 302)

    # ── static captive-portal probes ────────────────────────────────────────

    @_app.route("/generate_204")
    def _generate_204():
        return Response(status=204)

    @_app.route("/hotspot-detect.html")
    @_app.route("/ncsi.txt")
    @_app.route("/favicon.ico")
    def _probe_redirect():
        return redirect("/", 302)

    # ── MJPEG stream ─────────────────────────────────────────────────────────

    @_app.route("/stream")
    def stream():
        def generate():
            placeholder = _make_placeholder(640, 360)
            while True:
                frame = _state.get_frame()
                if frame is None:
                    jpg = placeholder
                else:
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    jpg = buf.tobytes() if ok else placeholder
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                )
                time.sleep(0.1)

        return Response(
            generate(),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )

    # ── status API ───────────────────────────────────────────────────────────

    @_app.route("/api/status")
    def api_status():
        status = _state.get_status()
        w, h = _state.get_frame_size()
        timeout_cfg = _app.config.get("TIMEOUT_SECONDS", 1200)
        status["timeout_remaining"] = _state.timeout_remaining(timeout_cfg)
        status["frame_width"] = w
        status["frame_height"] = h
        return jsonify(status)

    # ── line config API ──────────────────────────────────────────────────────

    @_app.route("/api/line", methods=["POST"])
    def api_line():
        data = request.get_json(force=True)
        p1 = data.get("point1")
        p2 = data.get("point2")
        direction = data.get("in_direction", "top")
        if not (p1 and p2 and len(p1) == 2 and len(p2) == 2):
            return jsonify({"error": "point1 and point2 required"}), 400
        _state.request_line_update(
            point1=[float(p1[0]), float(p1[1])],
            point2=[float(p2[0]), float(p2[1])],
            in_direction=direction,
        )
        logger.info("Line update: p1=%s p2=%s dir=%s", p1, p2, direction)
        return jsonify({"ok": True})

    # ── main page ─────────────────────────────────────────────────────────────

    @_app.route("/")
    def index():
        ip = hotspot_ip
        return render_template_string(_HTML, hotspot_ip=ip)

    return _app


def _make_placeholder(w: int, h: int) -> bytes:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.putText(img, "Venter på kamera...", (w // 2 - 130, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 2)
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML = """<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Borneland — Setup</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0 }

  :root {
    --bg:    #0f1117;
    --panel: #1c1f2b;
    --border:#2e3347;
    --text:  #e8eaf0;
    --muted: #8a8fa8;
    --accent:#4f9eff;
    --ok:    #00dc50;
    --warn:  #f59e0b;
    --bad:   #ef4444;
    --top:   #00dc50;
    --bottom:#00c8ff;
    --left:  #ffc800;
    --right: #c800ff;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 15px;
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }

  header {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 17px; font-weight: 600; letter-spacing: .03em }
  header .tag {
    background: var(--accent);
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    letter-spacing: .08em;
    text-transform: uppercase;
  }

  main {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 300px;
    gap: 0;
    max-width: 1200px;
    margin: 0 auto;
    width: 100%;
    padding: 20px;
    gap: 16px;
    align-items: start;
  }

  @media (max-width: 700px) {
    main { grid-template-columns: 1fr; }
  }

  /* ── stream + overlay ── */
  .stream-wrap {
    background: #000;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    position: relative;
  }
  .stream-wrap img {
    display: block;
    width: 100%;
    height: auto;
  }
  .stream-wrap canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    cursor: crosshair;
  }
  .stream-hint {
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    padding: 6px;
    background: rgba(0,0,0,.55);
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
  }

  /* ── sidebar ── */
  .sidebar { display: flex; flex-direction: column; gap: 14px }

  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
  }
  .card h2 {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: var(--muted);
    margin-bottom: 12px;
  }

  /* status rows */
  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }
  .stat-row:last-child { border-bottom: none }
  .stat-row .label { color: var(--muted) }
  .dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 5px;
  }
  .dot.ok { background: var(--ok) }
  .dot.bad { background: var(--bad) }

  /* line configurator */
  .pt-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    font-size: 13px;
  }
  .pt-badge {
    display: inline-block;
    width: 12px; height: 12px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }
  .pt-clear {
    background: none;
    border: none;
    color: var(--muted);
    cursor: pointer;
    font-size: 15px;
    line-height: 1;
    padding: 0;
  }
  .pt-clear:hover { color: var(--bad) }

  .dir-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    margin-bottom: 12px;
  }
  .dir-btn {
    background: var(--bg);
    border: 1.5px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    cursor: pointer;
    font-size: 12px;
    padding: 7px 4px;
    text-align: center;
    transition: border-color .15s, background .15s;
  }
  .dir-btn:hover { border-color: var(--accent) }
  .dir-btn.active-top    { border-color: var(--top);    background: rgba(0,220,80,.12) }
  .dir-btn.active-bottom { border-color: var(--bottom); background: rgba(0,200,255,.12) }
  .dir-btn.active-left   { border-color: var(--left);   background: rgba(255,200,0,.12) }
  .dir-btn.active-right  { border-color: var(--right);  background: rgba(200,0,255,.12) }

  .btn {
    width: 100%;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    padding: 10px;
    transition: opacity .15s;
  }
  .btn:disabled { opacity: .4; cursor: default }
  .btn-save { background: var(--accent); color: #fff }
  .btn-save:not(:disabled):hover { opacity: .85 }
  .btn-reset { background: var(--panel); border: 1px solid var(--border); color: var(--muted); margin-top: 8px }
  .btn-reset:hover { color: var(--text) }

  .feedback {
    font-size: 12px;
    text-align: center;
    margin-top: 8px;
    min-height: 18px;
    color: var(--ok);
  }

  /* timeout bar */
  .timeout-bar-wrap {
    background: var(--bg);
    border-radius: 4px;
    height: 6px;
    overflow: hidden;
    margin-top: 8px;
  }
  .timeout-bar {
    height: 100%;
    background: var(--accent);
    transition: width .5s linear;
  }
</style>
</head>
<body>

<header>
  <h1>Borneland Tællesystem</h1>
  <span class="tag">Setup</span>
</header>

<main>
  <!-- ── stream + overlay ── -->
  <div>
    <div class="stream-wrap" id="streamWrap">
      <img id="streamImg" src="/stream" alt="live">
      <canvas id="overlay"></canvas>
      <div class="stream-hint" id="hint">Klik for at sætte punkt 1</div>
    </div>
  </div>

  <!-- ── sidebar ── -->
  <div class="sidebar">

    <!-- status -->
    <div class="card">
      <h2>Status</h2>
      <div class="stat-row">
        <span class="label">Kamera</span>
        <span id="camStatus"><span class="dot bad"></span>Ukendt</span>
      </div>
      <div class="stat-row">
        <span class="label">Detektioner/sek</span>
        <span id="dps">—</span>
      </div>
      <div class="stat-row">
        <span class="label">LoRa sendt</span>
        <span id="loraSent">—</span>
      </div>
      <div class="stat-row">
        <span class="label">Auto-timeout</span>
        <span id="timeout">—</span>
      </div>
      <div class="timeout-bar-wrap">
        <div class="timeout-bar" id="timeoutBar" style="width:100%"></div>
      </div>
    </div>

    <!-- line config -->
    <div class="card">
      <h2>Tællelinje</h2>

      <div class="pt-row">
        <span>
          <span class="pt-badge" id="p1Badge" style="background:#555"></span>
          <span id="p1Label">Punkt 1 — klik på billede</span>
        </span>
        <button class="pt-clear" id="p1Clear" title="Nulstil punkt 1">✕</button>
      </div>
      <div class="pt-row">
        <span>
          <span class="pt-badge" id="p2Badge" style="background:#555"></span>
          <span id="p2Label">Punkt 2 — klik på billede</span>
        </span>
        <button class="pt-clear" id="p2Clear" title="Nulstil punkt 2">✕</button>
      </div>

      <p style="font-size:12px;color:var(--muted);margin-bottom:10px">
        Ind-retning: hvorfra tæller vi ind?
      </p>
      <div class="dir-grid">
        <button class="dir-btn active-top" data-dir="top"    onclick="setDir('top')">↑ Oppefra</button>
        <button class="dir-btn"            data-dir="bottom" onclick="setDir('bottom')">↓ Nedefra</button>
        <button class="dir-btn"            data-dir="left"   onclick="setDir('left')">← Venstre</button>
        <button class="dir-btn"            data-dir="right"  onclick="setDir('right')">→ Højre</button>
      </div>

      <button class="btn btn-save" id="saveBtn" disabled onclick="saveLine()">
        Gem linjekonfiguration
      </button>
      <button class="btn btn-reset" onclick="resetPoints()">Nulstil punkter</button>

      <div class="feedback" id="feedback"></div>
    </div>

  </div><!-- /sidebar -->
</main>

<script>
const DIR_COLORS = { top:"#00dc50", bottom:"#00c8ff", left:"#ffc800", right:"#c800ff" };
let pt1 = null, pt2 = null, currentDir = "top";
let frameW = 640, frameH = 360;
let maxTimeout = null;

const canvas  = document.getElementById("overlay");
const ctx     = canvas.getContext("2d");
const img     = document.getElementById("streamImg");
const hint    = document.getElementById("hint");
const saveBtn = document.getElementById("saveBtn");
const feedEl  = document.getElementById("feedback");

// Sync canvas resolution to its CSS size
function resizeCanvas() {
  canvas.width  = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  redraw();
}
new ResizeObserver(resizeCanvas).observe(canvas);

// Convert canvas CSS pixels → normalised [0,1] coords
function normalise(cx, cy) {
  return [cx / canvas.width, cy / canvas.height];
}

// Convert normalised coords → canvas CSS pixels
function toCanvas(nx, ny) {
  return [nx * canvas.width, ny * canvas.height];
}

canvas.addEventListener("click", e => {
  const r = canvas.getBoundingClientRect();
  const cx = e.clientX - r.left;
  const cy = e.clientY - r.top;
  if (!pt1) {
    pt1 = normalise(cx, cy);
    hint.textContent = "Klik for at sætte punkt 2";
  } else if (!pt2) {
    pt2 = normalise(cx, cy);
    hint.textContent = "Klar — klik 'Gem' for at gemme";
    saveBtn.disabled = false;
  }
  updateLabels();
  redraw();
});

function setDir(dir) {
  currentDir = dir;
  document.querySelectorAll(".dir-btn").forEach(b => {
    b.className = "dir-btn" + (b.dataset.dir === dir ? " active-" + dir : "");
  });
  redraw();
}

function resetPoints() {
  pt1 = null; pt2 = null;
  saveBtn.disabled = true;
  hint.textContent = "Klik for at sætte punkt 1";
  updateLabels();
  redraw();
  feedEl.textContent = "";
}

document.getElementById("p1Clear").addEventListener("click", () => {
  pt1 = null; pt2 = null;
  saveBtn.disabled = true;
  hint.textContent = "Klik for at sætte punkt 1";
  updateLabels(); redraw();
});
document.getElementById("p2Clear").addEventListener("click", () => {
  pt2 = null;
  saveBtn.disabled = true;
  hint.textContent = pt1 ? "Klik for at sætte punkt 2" : "Klik for at sætte punkt 1";
  updateLabels(); redraw();
});

function fmt(pt) {
  if (!pt) return null;
  return `(${(pt[0]*frameW).toFixed(0)}, ${(pt[1]*frameH).toFixed(0)})`;
}
function updateLabels() {
  const color = DIR_COLORS[currentDir];
  document.getElementById("p1Badge").style.background = pt1 ? color : "#555";
  document.getElementById("p2Badge").style.background = pt2 ? color : "#555";
  document.getElementById("p1Label").textContent = pt1 ? `Punkt 1 — ${fmt(pt1)}` : "Punkt 1 — klik på billede";
  document.getElementById("p2Label").textContent = pt2 ? `Punkt 2 — ${fmt(pt2)}` : "Punkt 2 — klik på billede";
}

function redraw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const color = DIR_COLORS[currentDir];
  if (pt1) {
    const [x, y] = toCanvas(...pt1);
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2*Math.PI);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
  }
  if (pt1 && pt2) {
    const [x1,y1] = toCanvas(...pt1);
    const [x2,y2] = toCanvas(...pt2);
    ctx.beginPath();
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.stroke();
    ctx.beginPath(); ctx.arc(x2, y2, 7, 0, 2*Math.PI);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
  }
}

async function saveLine() {
  if (!pt1 || !pt2) return;
  saveBtn.disabled = true;
  try {
    const resp = await fetch("/api/line", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({point1: pt1, point2: pt2, in_direction: currentDir}),
    });
    if (resp.ok) {
      feedEl.style.color = "var(--ok)";
      feedEl.textContent = "✓ Linje gemt og aktiveret";
    } else {
      feedEl.style.color = "var(--bad)";
      feedEl.textContent = "Fejl ved gemning — prøv igen";
      saveBtn.disabled = false;
    }
  } catch {
    feedEl.style.color = "var(--bad)";
    feedEl.textContent = "Netværksfejl";
    saveBtn.disabled = false;
  }
}

// ── status polling ────────────────────────────────────────────────────────────
function fmtSecs(s) {
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2,"0")}`;
}

async function pollStatus() {
  try {
    const r = await fetch("/api/status");
    if (!r.ok) return;
    const d = await r.json();

    frameW = d.frame_width  || 640;
    frameH = d.frame_height || 360;

    const cam = document.getElementById("camStatus");
    if (d.camera_ok) {
      cam.innerHTML = '<span class="dot ok"></span>OK';
    } else {
      cam.innerHTML = '<span class="dot bad"></span>Ingen forbindelse';
    }

    document.getElementById("dps").textContent =
      d.detections_per_sec != null ? d.detections_per_sec.toFixed(1) : "—";

    document.getElementById("loraSent").textContent =
      d.lora_last_sent || "—";

    const rem = d.timeout_remaining ?? 0;
    document.getElementById("timeout").textContent = fmtSecs(rem);
    if (maxTimeout === null && rem > 0) maxTimeout = rem;
    if (maxTimeout) {
      document.getElementById("timeoutBar").style.width =
        Math.round(rem / maxTimeout * 100) + "%";
    }

    updateLabels();
  } catch {}
}

pollStatus();
setInterval(pollStatus, 2000);
</script>
</body>
</html>"""
