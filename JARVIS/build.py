"""Build the JARVIS knowledge galaxy.

Scans every .md file in the notes folder and writes viewer/graph-data.js:

    const GRAPH = {nodes: [...], links: [...], meta: {...}};

Each node's numeric id equals its index in the nodes array -- the viewer and
the server both look nodes up by that index.

Usage:
    python build.py                 # notes folder from config.json (default ./notes)
    python build.py "D:/My Vault"   # any folder of markdown notes

Standard library only.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
VIEWER_DIR = os.path.join(ROOT, "viewer")
GRAPH_JS = os.path.join(VIEWER_DIR, "graph-data.js")
CONFIG_PATH = os.path.join(ROOT, "config.json")

EXCERPT_CHARS = 700
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", "__pycache__"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)

DEFAULT_CONFIG = {
    "api_key": "PUT-YOUR-KEY-HERE",
    "model": "claude-opus-5-5",
    "effort": "low",
    "brain": "auto",
    # "auto" uses the first brain here that has a key, and fails over down the list
    # (model by model) when a free model is rate-limited or down.
    "brain_order": ["groq", "openrouter", "opencode", "anthropic", "claude-cli", "ollama"],
    "providers": {
        "groq": {
            "label": "Groq",
            "base_url": "https://api.groq.com/openai/v1",
            "key_env": "GROQ_API_KEY",
            "daily_cap": "model",  # Groq's daily limits are per model
            "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
            "params": {"include_reasoning": False},
            "model_params": {
                "openai/gpt-oss-120b": {"reasoning_effort": "low"},
                "openai/gpt-oss-20b": {"reasoning_effort": "low"},
                "qwen/qwen3.8-27b": {"reasoning_effort": "none"},
            },
        },
        "opencode": {
            "label": "OpenCode Zen",
            "base_url": "https://opencode.ai/zen/v1",
            "key_env": "OPENCODE_API_KEY",
            # Zen's free models only work inside the OpenCode app; paid IDs go here (billed).
            "models": [],
        },
        "openrouter": {
            "label": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "key_env": "OPENROUTER_API_KEY",
            "daily_cap": "account",  # one 50/day allowance shared by every free model
            "params": {"reasoning": {"effort": "low", "exclude": True}},
            "models": [
                "qwen/qwen3.8-27b:free",
                "nvidia/nemotron-3-super-120b-a12b:free",
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
            ],
        },
    },
    "notes_dir": "notes",
    "ollama_model": "",
    "ollama_url": "http://localhost:11434",
    "user_title": "sir",
    "port": 4700,
}


def load_config():
    """config.json merged over defaults (providers merged one level deep)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
    providers = user.pop("providers", {}) or {}
    cfg.update(user)
    for name, overrides in providers.items():
        cfg["providers"].setdefault(name, {}).update(overrides or {})
    return cfg


def notes_dir_from(cfg, override=None):
    path = override or cfg.get("notes_dir") or "notes"
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    return os.path.normpath(path)


def clean_body(text):
    """Note text without frontmatter or the leading '# Title' line."""
    text = FRONTMATTER_RE.sub("", text, count=1).strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def make_excerpt(body, limit=EXCERPT_CHARS):
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


def read_note(path, notes_dir):
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    rel = os.path.relpath(path, notes_dir).replace("\\", "/")
    title = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.dirname(rel)
    group = folder.split("/")[0] if folder else "notes"
    body = clean_body(raw)
    return {
        "title": title,
        "path": rel,
        "group": group,
        "body": body,
        "excerpt": make_excerpt(body),
        "wikilinks": {w.strip().lower() for w in WIKILINK_RE.findall(raw)},
    }


def scan_notes(notes_dir):
    paths = []
    for dirpath, dirnames, filenames in os.walk(notes_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.lower().endswith(".md"):
                paths.append(os.path.join(dirpath, name))
    paths.sort(key=lambda p: os.path.relpath(p, notes_dir).lower())
    return [read_note(p, notes_dir) for p in paths]


def _mention_patterns(notes):
    pats = []
    for note in notes:
        t = note["title"].strip()
        # Very short titles ("Q4", "Ideas") would link everything to everything.
        pats.append(re.compile(r"(?<!\w)" + re.escape(t.lower()) + r"(?!\w)") if len(t) >= 4 else None)
    return pats


def links_for(i, notes, title_index, patterns):
    """Neighbours of note i: wikilinks to, or plain-text mentions of, another note's title."""
    note = notes[i]
    text = (note["title"] + "\n" + note["body"]).lower()
    out = set()
    for target in note["wikilinks"]:
        j = title_index.get(target)
        if j is not None and j != i:
            out.add(j)
    for j, pat in enumerate(patterns):
        if j != i and pat is not None and pat.search(text):
            out.add(j)
    return out


def build_links(notes):
    title_index = {n["title"].lower(): i for i, n in enumerate(notes)}
    patterns = _mention_patterns(notes)
    pairs = set()
    for i in range(len(notes)):
        for j in links_for(i, notes, title_index, patterns):
            pairs.add((min(i, j), max(i, j)))
    # Notes that share a [[wikilink]] to something that isn't itself a note
    # (an unresolved topic like [[pricing]]) are related through that topic.
    by_topic = {}
    for i, n in enumerate(notes):
        for w in n["wikilinks"]:
            if w not in title_index:
                by_topic.setdefault(w, []).append(i)
    for members in by_topic.values():
        if 2 <= len(members) <= 12:
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    pairs.add((members[a], members[b]))
    return sorted(pairs)


def graph_payload(notes, pairs):
    degree = [0] * len(notes)
    for a, b in pairs:
        degree[a] += 1
        degree[b] += 1
    nodes = [
        {
            "id": i,
            "label": n["title"],
            "group": n["group"],
            "excerpt": n["excerpt"],
            "path": n["path"],
            "degree": degree[i],
        }
        for i, n in enumerate(notes)
    ]
    links = [{"source": a, "target": b} for a, b in pairs]
    meta = {
        "count": len(nodes),
        "links": len(links),
        "groups": sorted({n["group"] for n in notes}),
        "built": time.strftime("%Y-%m-%d %H:%M"),
    }
    return {"nodes": nodes, "links": links, "meta": meta}


def write_graph_js(payload, path=GRAPH_JS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("// Generated by build.py -- do not edit by hand.\n")
        f.write("const GRAPH = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")
    os.replace(tmp, path)


def build(notes_dir):
    notes = scan_notes(notes_dir)
    pairs = build_links(notes)
    payload = graph_payload(notes, pairs)
    write_graph_js(payload)
    return notes, pairs, payload


def main():
    cfg = load_config()
    notes_dir = notes_dir_from(cfg, sys.argv[1] if len(sys.argv) > 1 else None)
    if not os.path.isdir(notes_dir):
        sys.exit(f"Notes folder not found: {notes_dir}")
    _, _, payload = build(notes_dir)
    m = payload["meta"]
    print(f"Indexed {m['count']} notes, {m['links']} links, groups: {', '.join(m['groups'])}")
    print(f"Wrote {os.path.relpath(GRAPH_JS, ROOT)}")


if __name__ == "__main__":
    main()
