"""JARVIS server -- standard library only.

    python server.py          then open http://localhost:4700 in Chrome
    python server.py --open   ...and open it for you

Serves ONLY the viewer/ folder (config.json and your notes are never reachable
from the browser) and exposes:
    GET  /api/status   note count + which brain is active
    POST /chat         {"message", "session"} -> {"answer", "nodes": [note indexes used]}
    POST /remember     {"text": "remember that ..."} -> writes notes/captures/<title>.md
"""
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import build
from brain import Brain, BrainError

MAX_BODY = 64 * 1024
REMEMBER_PREFIX = re.compile(r"^\s*(?:(?:hey\s+)?jarvis[\s,.!:]*)?(?:please\s+)?remember(?:\s+that)?[\s,:;-]*", re.I)
BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

REMEMBER_LINES = [
    "Noted, {t}. I've filed it under '{title}', where it will outlive us both.",
    "Committed to memory, {t}. Unlike most of what's said in meetings.",
    "Done, {t}. A new star is born. Try not to let it go to your head.",
    "Filed as '{title}', {t}. The galaxy grows ever so slightly wiser.",
    "Remembered, {t}. I shall bring it up at the most inconvenient moment possible.",
]

STATE = {}
LOCK = threading.Lock()


def load_state():
    cfg = build.load_config()
    notes_dir = build.notes_dir_from(cfg)
    if not os.path.isdir(notes_dir):
        sys.exit(f"Notes folder not found: {notes_dir}\nSet \"notes_dir\" in config.json.")
    notes, pairs, payload = build.build(notes_dir)
    STATE.update(cfg=cfg, notes_dir=notes_dir, pairs=list(pairs), brain=Brain(cfg, notes))
    return payload["meta"]


def reload_config():
    """Pick up edits to config.json (e.g. a freshly pasted API key) without a restart."""
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
    }


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
        path = os.path.join(capture_dir, title + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

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

    t = STATE["cfg"].get("user_title", "sir")
    return {
        "node": payload["nodes"][new_id],
        "links": [{"source": a, "target": b} for a, b in new_pairs],
        "anchor": related[0] if related else (min(neighbours) if neighbours else None),
        "line": random.choice(REMEMBER_LINES).format(t=t, title=title),
        "path": note["path"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS/1.0"

    def log_message(self, fmt, *args):
        if self.command == "POST" or (args and str(args[1]).startswith(("4", "5"))):
            sys.stderr.write("  %s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/api/status":
            return self._send(200, status())
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
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

        if self.path == "/chat":
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

        if self.path == "/remember":
            try:
                return self._send(200, remember(str(body.get("text", ""))[:2000]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})

        self._send(404, {"error": "not found"})


def main():
    meta = load_state()
    port = int(STATE["cfg"].get("port", 4700))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    st = status()
    print(f"\n  J.A.R.V.I.S. online -> {url}  (open in Chrome or Edge)")
    print(f"  {meta['count']} notes, {meta['links']} links from {STATE['notes_dir']}")
    print(f"  Brain: {st['brain']}")
    if st["backend"] == "offline":
        print("  Tip: setx OPENCODE_API_KEY / OPENROUTER_API_KEY (free models) for the full brain.")
    print("  Ctrl+C to stop.\n")
    if "--open" in sys.argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Powering down. Good night, sir.")


if __name__ == "__main__":
    main()
