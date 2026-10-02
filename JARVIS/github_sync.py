"""Keep "remember that..." captures safe on hosts with throwaway disks (Render free tier).

When these environment variables are set, every capture is also committed to the
GitHub repo, and on startup the server pulls captures back down before indexing:

    GITHUB_TOKEN       fine-grained token: this repo only, "Contents: Read and write"
    GITHUB_REPO        e.g. Rahul9994/Vibing-projects
    GITHUB_NOTES_PATH  notes folder inside the repo (default JARVIS/notes)
    GITHUB_BRANCH      default main

Without them JARVIS works exactly as before (captures stay on local disk).
Standard library only.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
USER_AGENT = "JARVIS/1.0"


def settings():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if not token or not repo:
        return None
    return {
        "token": token,
        "repo": repo,
        "notes_path": os.environ.get("GITHUB_NOTES_PATH", "JARVIS/notes").strip("/"),
        "branch": os.environ.get("GITHUB_BRANCH", "main").strip() or "main",
    }


def _request(s, method, path, body=None, accept="application/vnd.github+json"):
    url = f"{API}/repos/{s['repo']}/contents/{urllib.parse.quote(path)}"
    if method == "GET":
        url += "?" + urllib.parse.urlencode({"ref": s["branch"]})
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "authorization": f"Bearer {s['token']}",
        "accept": accept,
        "x-github-api-version": "2022-11-28",
        "user-agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def pull_captures(notes_dir):
    """Download every captures/*.md from GitHub that isn't on disk yet. Returns count."""
    s = settings()
    if not s:
        return 0
    folder = f"{s['notes_path']}/captures"
    try:
        listing = json.loads(_request(s, "GET", folder))
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 404 = no captures yet
            print(f"  GitHub sync: couldn't list captures ({e.code})", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"  GitHub sync: {e}", file=sys.stderr)
        return 0
    local_dir = os.path.join(notes_dir, "captures")
    os.makedirs(local_dir, exist_ok=True)
    pulled = 0
    for item in listing if isinstance(listing, list) else []:
        name = item.get("name", "")
        if item.get("type") != "file" or not name.endswith(".md") or "/" in name or "\\" in name:
            continue
        target = os.path.join(local_dir, name)
        if os.path.exists(target):
            continue
        try:
            raw = _request(s, "GET", f"{folder}/{name}", accept="application/vnd.github.raw")
            with open(target, "wb") as f:
                f.write(raw)
            pulled += 1
        except Exception as e:
            print(f"  GitHub sync: couldn't fetch {name}: {e}", file=sys.stderr)
    return pulled


def push_capture(rel_path, text, title):
    """Commit one capture (path relative to the notes folder). Safe to call from a thread."""
    s = settings()
    if not s:
        return False
    body = {
        "message": f"JARVIS remembered: {title}",
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": s["branch"],
    }
    try:
        _request(s, "PUT", f"{s['notes_path']}/{rel_path}", body)
        return True
    except urllib.error.HTTPError as e:
        print(f"  GitHub sync: couldn't save {rel_path} ({e.code}: {e.read()[:200]!r})", file=sys.stderr)
    except Exception as e:
        print(f"  GitHub sync: couldn't save {rel_path}: {e}", file=sys.stderr)
    return False
