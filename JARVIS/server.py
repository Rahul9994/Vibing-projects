"""JARVIS server -- standard library only.

Local:
    python server.py          then open http://localhost:4700 in Chrome
    python server.py --open   ...and open it for you

Hosted (JARVIS_HOSTED=1, or Render's own RENDER variable):
    binds 0.0.0.0:$PORT, requires the JARVIS_ACCESS_KEY passphrase on every API call,
    allows the browser origins in ALLOWED_ORIGINS (e.g. https://rahul9994.github.io),
    and rate-limits each visitor. See render.yaml in the repo root.

Serves ONLY the viewer/ folder (config.json and your notes are never reachable as files)
and exposes:
    GET  /api/health   no auth -- for wake-up pings and the host's health check
    GET  /api/status   note count + which brain is active
    GET  /api/graph    the galaxy (nodes + links), including new captures
    POST /chat         {"message", "session"} -> {"answer", "nodes": [note indexes used]}
    POST /remember     {"text": "remember that ..."} -> writes notes/captures/<title>.md
"""
import hmac
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
import webbrowser
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import build
import github_sync
from brain import Brain, BrainError

MAX_BODY = 64 * 1024
REMEMBER_PREFIX = re.compile(r"^\s*(?:(?:hey\s+)?jarvis[\s,.!:]*)?(?:please\s+)?remember(?:\s+that)?[\s,:;-]*", re.I)
BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Explicit flag, not PORT: plenty of local launchers set PORT too.
HOSTED = bool(os.environ.get("JARVIS_HOSTED") or os.environ.get("RENDER"))
ACCESS_KEY = os.environ.get("JARVIS_ACCESS_KEY", "").strip()
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
API_PATHS = {"/api/status", "/api/graph", "/chat", "/remember"}

# Per-visitor limits (only matter when hosted): questions per minute / per day, and
# wrong passphrases per 10 minutes before that visitor is locked out for a while.
LIMITS = {"minute": 20, "day": 500, "bad_auth": 10}

REMEMBER_LINES = [
    "Noted, {t}. I've filed it under '{title}', where it will outlive us both.",
    "Committed to memory, {t}. Unlike most of what's said in meetings.",
    "Done, {t}. A new star is born. Try not to let it go to your head.",
    "Filed as '{title}', {t}. The galaxy grows ever so slightly wiser.",
    "Remembered, {t}. I shall bring it up at the most inconvenient moment possible.",
]

STATE = {}
LOCK = threading.Lock()
HITS = defaultdict(deque)       # ip -> timestamps of API calls
BAD_AUTH = defaultdict(deque)   # ip -> timestamps of wrong passphrases


def load_state():
    cfg = build.load_config()
    notes_dir = build.notes_dir_from(cfg)
    if not os.path.isdir(notes_dir):
        sys.exit(f"Notes folder not found: {notes_dir}\nSet \"notes_dir\" in config.json.")
    pulled = github_sync.pull_captures(notes_dir)
    if pulled:
        print(f"  GitHub sync: pulled {pulled} capture(s)")
    notes, pairs, payload = build.build(notes_dir)
    STATE.update(cfg=cfg, notes_dir=notes_dir, pairs=list(pairs), brain=Brain(cfg, notes))
    return payload["meta"]


def reload_config():
    """Pick up edits to config.json without a restart."""
    cfg = build.load_config()
    STATE["cfg"] = cfg
    STATE["brain"].cfg = cfg


def status():
    reload_config()
    brain = STATE["brain"]
    info = brain.describe()
    return {
        "count": len(brain.notes),
        "links": len(STATE["pairs"]),
        "brain": info["brain"],
        "backend": info["backend"],
        "fallbacks": info["fallbacks"],
        "quota": brain.quota(),
        "user_title": STATE["cfg"].get("user_title", "sir"),
        "github_sync": bool(github_sync.settings()),
    }


def graph():
    with LOCK:
        return build.graph_payload(STATE["brain"].notes, STATE["pairs"])


def make_title(text):
    words = re.sub(r"[^\w\s'£$%-]", " ", text).split()
    title = " ".join(words[:6]) or "Captured thought"
    title = BAD_FILENAME_CHARS.sub("", title).strip(" .")
    return title[:1].upper() + title[1:]


def remember(raw_text):
    text = REMEMBER_PREFIX.sub("", raw_text).strip()
    if len(text) < 3:
        raise ValueError("Nothing to remember -- say 'remember that' followed by the thought.")
    sentence = text[:1].upper() + text[1:]
    if sentence[-1] not in ".!?":
        sentence += "."

    with LOCK:
        brain = STATE["brain"]
        notes = brain.notes
        capture_dir = os.path.join(STATE["notes_dir"], "captures")
        os.makedirs(capture_dir, exist_ok=True)

        title = make_title(text)
        existing = {n["title"].lower() for n in notes}
        base, n = title, 2
        while title.lower() in existing or os.path.exists(os.path.join(capture_dir, title + ".md")):
            title, n = f"{base} {n}", n + 1

        related = [i for score, i in brain.index.search(text, k=3) if score >= 1.0]
        lines = [f"# {title}", "", sentence, "", f"Captured by voice on {time.strftime('%d %B %Y at %H:%M')}."]
        if related:
            lines.append("Related: " + ", ".join(f"[[{notes[i]['title']}]]" for i in related))
        content = "\n".join(lines) + "\n"
        path = os.path.join(capture_dir, title + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        note = build.read_note(path, STATE["notes_dir"])
        new_id = len(notes)
        brain.add_note(note)
        title_index = {nn["title"].lower(): i for i, nn in enumerate(notes)}
        patterns = build._mention_patterns(notes)
        neighbours = build.links_for(new_id, notes, title_index, patterns)
        new_pairs = sorted((min(new_id, j), max(new_id, j)) for j in neighbours)
        STATE["pairs"].extend(new_pairs)
        payload = build.graph_payload(notes, STATE["pairs"])
        build.write_graph_js(payload)

    # Hosted disks are wiped on restart: commit the capture to GitHub in the background.
    synced = bool(github_sync.settings())
    if synced:
        threading.Thread(target=github_sync.push_capture, args=(note["path"], content, title), daemon=True).start()

    t = STATE["cfg"].get("user_title", "sir")
    return {
        "node": payload["nodes"][new_id],
        "links": [{"source": a, "target": b} for a, b in new_pairs],
        "anchor": related[0] if related else (min(neighbours) if neighbours else None),
        "line": random.choice(REMEMBER_LINES).format(t=t, title=title),
        "path": note["path"],
        "saved_to_github": synced,
    }


def _prune(q, window, now):
    while q and now - q[0] > window:
        q.popleft()


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS/1.0"

    # ---------------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):
        if self.command == "POST" or (args and str(args[1]).startswith(("4", "5"))):
            sys.stderr.write("  %s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _client_ip(self):
        if HOSTED:  # behind the host's proxy: the first X-Forwarded-For hop is the visitor
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def _cors_headers(self):
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and (origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Jarvis-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network"):
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def _guard(self, path):
        """Passphrase + rate limits for API calls. Returns True if the request may proceed."""
        if path not in API_PATHS:
            return True
        ip, now = self._client_ip(), time.time()
        bad = BAD_AUTH[ip]
        _prune(bad, 600, now)
        if len(bad) >= LIMITS["bad_auth"]:
            self._send(429, {"error": "Too many wrong passphrases. Try again in 10 minutes."})
            return False
        if ACCESS_KEY:
            given = (self.headers.get("X-Jarvis-Key") or "").strip()
            if not hmac.compare_digest(given.encode(), ACCESS_KEY.encode()):
                bad.append(now)
                self._send(401, {"error": "passphrase required" if not given else "wrong passphrase"})
                return False
        if HOSTED and self.command == "POST":
            hits = HITS[ip]
            _prune(hits, 86400, now)
            last_minute = sum(1 for t in hits if now - t < 60)
            if last_minute >= LIMITS["minute"] or len(hits) >= LIMITS["day"]:
                self._send(429, {"error": "Easy, sir -- rate limit reached. Give it a minute."})
                return False
            hits.append(now)
        return True

    # ---------------------------------------------------------------- routes
    def do_OPTIONS(self):  # CORS preflight
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/api/health":
            return self._send(200, {"ok": True, "auth": bool(ACCESS_KEY)})
        if not self._guard(path):
            return
        if path == "/api/status":
            return self._send(200, status())
        if path == "/api/graph":
            return self._send(200, graph())
        if path == "/":
            path = "/index.html"
        viewer = os.path.realpath(build.VIEWER_DIR)
        target = os.path.realpath(os.path.join(viewer, path.lstrip("/")))
        if not target.startswith(viewer + os.sep) or not os.path.isfile(target):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._guard(path):
            return
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

        if path == "/chat":
            message = str(body.get("message", "")).strip()[:2000]
            if not message:
                return self._send(400, {"error": "empty message"})
            reload_config()
            try:
                result = STATE["brain"].ask(str(body.get("session", ""))[:64], message)
            except BrainError as e:
                t = STATE["cfg"].get("user_title", "sir")
                result = {"answer": f"My apologies, {t}, I've hit a snag. {e}", "nodes": [], "error": str(e)}
            except Exception as e:  # keep the server alive, tell the user what broke
                result = {"answer": f"Something went wrong on my side: {e}", "nodes": [], "error": str(e)}
            return self._send(200, result)

        if path == "/remember":
            try:
                return self._send(200, remember(str(body.get("text", ""))[:2000]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})

        self._send(404, {"error": "not found"})


def main():
    if HOSTED and not ACCESS_KEY:
        sys.exit("Refusing to start: hosted mode needs JARVIS_ACCESS_KEY, or anyone could spend your API quota.")
    meta = load_state()
    host = "0.0.0.0" if HOSTED else "127.0.0.1"
    port = int(os.environ.get("PORT") or STATE["cfg"].get("port", 4700))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://localhost:{port}"
    st = status()
    print(f"\n  J.A.R.V.I.S. online -> {url if not HOSTED else f'{host}:{port} (hosted)'}")
    print(f"  {meta['count']} notes, {meta['links']} links from {STATE['notes_dir']}")
    print(f"  Brain: {st['brain']}")
    print(f"  Passphrase: {'required' if ACCESS_KEY else 'off (local only)'} · "
          f"browser origins: {', '.join(sorted(ALLOWED_ORIGINS)) or 'same-origin only'} · "
          f"GitHub sync: {'on' if st['github_sync'] else 'off'}")
    if st["backend"] == "offline":
        print("  Tip: set GROQ_API_KEY / OPENROUTER_API_KEY (free models) for the full brain.")
    print("  Ctrl+C to stop.\n", flush=True)
    if "--open" in sys.argv and not HOSTED:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Powering down. Good night, sir.")


if __name__ == "__main__":
    main()
