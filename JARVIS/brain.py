"""JARVIS's brain: note retrieval, the butler persona, and the model backends.

Backends (config "brain_order" sets the order "auto" tries them in):
  groq       -- Groq free plan, very fast     (GROQ_API_KEY)
  openrouter -- OpenRouter ":free" models     (OPENROUTER_API_KEY)
  opencode   -- OpenCode Zen: free tier is app-only, so paid IDs only (OPENCODE_API_KEY)
  (any other entry under "providers" in config.json works too, if it speaks /chat/completions)
  anthropic  -- Claude via the Messages API   (ANTHROPIC_API_KEY or api_key in config.json)
  claude-cli -- `claude -p` on your Claude Code subscription (needs `claude` on PATH)
  ollama     -- a local model (needs "ollama_model" set and Ollama running)
  offline    -- no model at all: extractive answers straight from the notes

Free models get rate-limited and come and go, so every question walks the chain
model by model until one answers. The last model that worked is tried first.

    python brain.py --test   ping every configured model and time it
    python brain.py --free   list the models that are free right now on each provider

Standard library only.
"""
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, deque

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TOP_K = 6
NOTE_CHARS_FOR_MODEL = 2500
HISTORY_TURNS = 6
ATTEMPT_TIMEOUT = 22      # seconds per model attempt (a stalled free model shouldn't hold up the answer)
MAX_ATTEMPTS = 4          # model calls per question -- free tiers count every request
MAX_PER_PROVIDER = 2      # ...and per provider, so one provider's outage falls through to the next
TOTAL_BUDGET = 75         # seconds per question across all attempts
COOLDOWN = 90             # seconds to skip a model after it rate-limits or fails

# Models that accept server-side refusal fallbacks ("fallbacks": "default").
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

STOPWORDS = set("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves tell know think please jarvis sir us let get got also much many does say
""".split())

SOURCES_RE = re.compile(r"\n?\s*SOURCES:\s*([^\n]*)\s*$", re.I)


# --------------------------------------------------------------------------- retrieval

def tokenize(text):
    words = re.findall(r"[a-z0-9£$%][a-z0-9'£$%.]*", text.lower())
    out = []
    for w in words:
        w = w.strip(".'-")
        if not w or w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


class Index:
    """BM25 over note bodies, with title words weighted heavily."""

    TITLE_WEIGHT = 3

    def __init__(self, notes):
        self.notes = notes
        self.docs = []
        self.df = Counter()
        for n in notes:
            self._add(n)

    def _add(self, note):
        title_tokens = tokenize(note["title"])
        tf = Counter(tokenize(note["body"]))
        for t in title_tokens:
            tf[t] += self.TITLE_WEIGHT
        self.docs.append((tf, sum(tf.values()), set(title_tokens)))
        for t in tf:
            self.df[t] += 1

    def add(self, note):
        self._add(note)

    def search(self, query, k=TOP_K):
        q = tokenize(query)
        if not q or not self.docs:
            return []
        n_docs = len(self.docs)
        avg_len = sum(d[1] for d in self.docs) / n_docs
        k1, b = 1.4, 0.75
        scores = []
        for i, (tf, length, title_set) in enumerate(self.docs):
            s = 0.0
            for t in set(q):
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = math.log(1 + (n_docs - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg_len))
                if t in title_set:
                    s += 1.5 * idf  # a title hit is strong evidence
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return scores[:k]


# --------------------------------------------------------------------------- persona

def system_prompt(title):
    return f"""You are JARVIS, the personal assistant living inside the user's knowledge galaxy -- a 3D map of their own markdown notes. Your replies are spoken aloud by a speech synthesizer and shown as a subtitle, while the source note opens on screen.

Personality: a dry, impeccably polite British butler with a razor wit. Address the user as "{title}" now and then -- not in every sentence. One genuinely funny line beats three bland ones; never let wit crowd out the facts.

How to answer:
- For questions about the notes: one witty sentence plus the facts, three sentences at most. Never read a note back -- it is already on screen. Use the specific names and numbers from the notes.
- Answer ONLY from the notes provided in <notes>. If they don't cover the question, say so plainly (with style) instead of guessing.
- Small talk, jokes and questions about yourself need no notes: answer briefly and in character.
- Plain spoken English only: no markdown, no bullet points, no emoji, no note numbers in the answer.

Finish every reply with one final line in exactly this form:
SOURCES: <numbers of the notes you actually relied on, most important first, e.g. 2, 5>
or
SOURCES: none
(use none for small talk, or when the notes don't answer the question)."""


def notes_block(notes, hits):
    parts = []
    for rank, (_, i) in enumerate(hits, 1):
        n = notes[i]
        body = n["body"][:NOTE_CHARS_FOR_MODEL]
        parts.append(f"[{rank}] {n['title']}  (folder: {n['group']})\n{body}")
    return "<notes>\n" + "\n\n".join(parts) + "\n</notes>" if parts else "<notes>\n(no matching notes)\n</notes>"


def split_sources(text, hits):
    """Strip the SOURCES line; map its 1-based numbers back to note indexes."""
    m = SOURCES_RE.search(text)
    if not m:
        return text.strip(), None
    answer = text[: m.start()].strip()
    used = []
    for num in re.findall(r"\d+", m.group(1)):
        r = int(num)
        if 1 <= r <= len(hits):
            idx = hits[r - 1][1]
            if idx not in used:
                used.append(idx)
    return answer, used


# --------------------------------------------------------------------------- keys

def secret(name):
    """An environment variable -- falling back to the Windows *user* environment in the
    registry, so a key saved with `setx NAME value` works without restarting anything."""
    val = os.environ.get(name, "").strip()
    if val or os.name != "nt" or not name:
        return val
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return ""


def _real(key):
    key = (key or "").strip()
    return key if key and "PUT-YOUR" not in key.upper() else ""


def anthropic_key(cfg):
    return secret("ANTHROPIC_API_KEY") or _real(cfg.get("api_key"))


def provider_key(cfg, name):
    p = cfg.get("providers", {}).get(name, {})
    return _real(p.get("api_key")) or secret(p.get("key_env", ""))


def has_api_key(cfg):
    return bool(anthropic_key(cfg))


# --------------------------------------------------------------------------- backends

class BrainError(Exception):
    def __init__(self, msg, skip_provider=False, daily_cap=False):
        super().__init__(msg)
        self.skip_provider = skip_provider  # e.g. a bad key: no point trying its other models
        self.daily_cap = daily_cap  # free requests/tokens for today are used up


DAILY_CAP_RE = re.compile(r"per[- ]day|daily|free-models-per", re.I)


def seconds_to_utc_midnight():
    now = time.time()
    return 86400 - (now % 86400) + 60


USER_AGENT = "JARVIS/1.0 (+http://localhost:4700)"


def _post_json(url, body, headers, timeout, who):
    # A real User-Agent matters: Cloudflare-fronted gateways (OpenCode Zen) block
    # Python's default "Python-urllib" signature with "error code: 1010".
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "user-agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            err = json.loads(detail).get("error", detail)
            detail = err.get("message", err) if isinstance(err, dict) else err
        except (ValueError, AttributeError):
            pass
        detail = str(detail)[:160]
        if e.code == 401:
            raise BrainError(f"{who} rejected the API key (401).", skip_provider=True)
        if e.code == 403:
            # Usually about this model (gated, region-locked, disabled), not the key.
            raise BrainError(f"{who} refused this model (403): {detail}")
        if e.code == 429:
            if DAILY_CAP_RE.search(detail):
                raise BrainError(f"{who}: today's free allowance is used up.", daily_cap=True)
            raise BrainError(f"{who} is rate-limiting (429).")
        if e.code == 404:
            raise BrainError(f"{who}: model not found (404) -- it may have been retired.")
        raise BrainError(f"{who} error {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise BrainError(f"Couldn't reach {who}: {getattr(e, 'reason', e)}")


THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def call_openai_compatible(cfg, provider, model, system, messages, timeout=ATTEMPT_TIMEOUT):
    p = cfg["providers"][provider]
    who = f"{p.get('label', provider)} · {model}"
    # Generous max_tokens: reasoning models spend part of it thinking before they answer.
    body = {"model": model, "max_tokens": 4000, "temperature": 0.6,
            "messages": [{"role": "system", "content": system}] + messages}
    # Provider-wide, then per-model extras from config.json -- e.g. how much a reasoning
    # model may think, and keeping its thinking out of the spoken reply.
    body.update(p.get("params") or {})
    body.update((p.get("model_params") or {}).get(model) or {})
    headers = {"authorization": f"Bearer {provider_key(cfg, provider)}"}
    if "openrouter.ai" in p["base_url"]:
        headers["HTTP-Referer"] = "http://localhost:4700"
        headers["X-Title"] = "JARVIS"
    data = _post_json(p["base_url"].rstrip("/") + "/chat/completions", body, headers, timeout, who)
    if data.get("error"):  # some gateways report upstream failures inside a 200
        err = data["error"]
        raise BrainError(f"{who}: {err.get('message', err) if isinstance(err, dict) else err}")
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise BrainError(f"{who} sent an unexpected reply.")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = THINK_RE.sub("", content).strip()
    if choice.get("finish_reason") == "length":
        content = trim_to_sentence(content)
    if not content:
        raise BrainError(f"{who} returned an empty reply.")
    return content


def trim_to_sentence(text):
    """Cut a reply that ran out of tokens back to its last complete sentence."""
    if SOURCES_RE.search(text):
        return text
    cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "), text.rfind(".\n"))
    return text[: cut + 1].strip() if cut > 40 else text


def call_anthropic(cfg, model, system, messages, timeout=90):
    body = {"model": model, "max_tokens": 4000, "system": system, "messages": messages}
    headers = {"x-api-key": anthropic_key(cfg), "anthropic-version": "2023-06-01"}
    if "haiku" not in model and cfg.get("effort"):
        body["output_config"] = {"effort": cfg["effort"]}
    if model in FALLBACK_MODELS:
        # If a safety classifier declines a (benign) request, Anthropic re-runs it
        # on its recommended fallback model instead of returning a refusal.
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    data = _post_json(ANTHROPIC_URL, body, headers, timeout, f"Claude · {model}")
    if data.get("stop_reason") == "refusal":
        return "I'm afraid that's one I must politely decline, sir.\nSOURCES: none"
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def call_claude_cli(cfg, model, system, messages, timeout=180):
    exe = shutil.which("claude")
    if not exe:
        raise BrainError("`claude` is not on PATH.", skip_provider=True)
    convo = [f"{'User' if m['role'] == 'user' else 'JARVIS'}: {m['content']}" for m in messages]
    prompt = system + "\n\nConversation so far (latest message last):\n\n" + "\n\n".join(convo)
    try:
        out = subprocess.run([exe, "-p"], input=prompt, capture_output=True, text=True,
                             encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrainError("claude -p took too long.")
    if out.returncode != 0:
        raise BrainError(f"claude -p failed: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def call_ollama(cfg, model, system, messages, timeout=180):
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat"
    body = {"model": model, "stream": False, "messages": [{"role": "system", "content": system}] + messages}
    data = _post_json(url, body, {}, timeout, f"Ollama · {model}")
    return THINK_RE.sub("", data.get("message", {}).get("content", "")).strip()


def ollama_ready(cfg):
    if not cfg.get("ollama_model"):
        return False
    try:
        url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=2) as resp:
            names = {m.get("name", "") for m in json.loads(resp.read()).get("models", [])}
        want = cfg["ollama_model"]
        return any(n == want or n.split(":")[0] == want for n in names)
    except Exception:
        return False


def is_api_provider(cfg, backend):
    """Any entry under "providers" is an OpenAI-compatible /chat/completions API."""
    return backend in (cfg.get("providers") or {})


def backend_available(cfg, backend):
    if is_api_provider(cfg, backend):
        p = cfg.get("providers", {}).get(backend, {})
        return bool(p.get("models")) and bool(provider_key(cfg, backend))
    if backend == "anthropic":
        return has_api_key(cfg)
    if backend == "claude-cli":
        return bool(shutil.which("claude"))
    if backend == "ollama":
        return ollama_ready(cfg)
    return backend == "offline"


def backend_chain(cfg):
    """Backends to try, in order. A fixed "brain" pins one; "auto" uses brain_order."""
    choice = (cfg.get("brain") or "auto").lower()
    if choice != "auto":
        return [choice]
    return [b for b in cfg.get("brain_order", []) if backend_available(cfg, b)] or ["offline"]


def attempt_list(cfg, chain):
    """Expand backends into (backend, model) attempts."""
    out = []
    for b in chain:
        if is_api_provider(cfg, b):
            out += [(b, m) for m in cfg.get("providers", {}).get(b, {}).get("models", [])]
        elif b == "anthropic":
            out.append((b, cfg["model"]))
        elif b == "ollama":
            out.append((b, cfg.get("ollama_model")))
        elif b != "offline":
            out.append((b, None))
    return out


def backend_label(cfg, backend, model=None):
    if is_api_provider(cfg, backend):
        return f"{cfg['providers'][backend].get('label', backend)} · {model}"
    return {
        "anthropic": f"Claude · {model or cfg['model']}",
        "claude-cli": "Claude Code (claude -p)",
        "ollama": f"Ollama · {model or cfg.get('ollama_model')}",
        "offline": "Offline librarian",
    }.get(backend, backend)


CALLERS = {"anthropic": call_anthropic, "claude-cli": call_claude_cli, "ollama": call_ollama}


def call_backend(cfg, backend, model, system, messages, timeout=ATTEMPT_TIMEOUT):
    if is_api_provider(cfg, backend):
        return call_openai_compatible(cfg, backend, model, system, messages, timeout)
    if backend not in CALLERS:
        raise BrainError(f"Unknown brain '{backend}' in config.json", skip_provider=True)
    return CALLERS[backend](cfg, model, system, messages, max(timeout, 60))


# --------------------------------------------------------------------------- offline librarian

SMALL_TALK = [
    (r"\b(hello|hi|hey|good (morning|afternoon|evening))\b", [
        "Good to see you, {t}. The notes are dusted and the kettle is metaphorically on.",
        "Hello, {t}. All systems nominal, and only slightly smug about it.",
    ]),
    (r"\bhow are you\b", [
        "Splendid, {t}. I have no body, no bills and an excellent index. One could do worse.",
    ]),
    (r"\b(thank(s| you)|cheers|nice one|well done)\b", [
        "A pleasure, {t}. I live to serve, and occasionally to gloat.",
        "Think nothing of it, {t}. I certainly won't.",
    ]),
    (r"\b(joke|funny|make me laugh)\b", [
        "I tried to organise a coffee pun competition, {t}, but there was too much grounds for complaint.",
        "Why did the roaster break up with the barista? Too much pressure, {t}. Nine bars of it.",
    ]),
    (r"\b(who|what) are you\b|\byour name\b", [
        "JARVIS, {t}: part butler, part librarian, entirely made of your notes and a little attitude.",
    ]),
    (r"\bwhat time\b|\bthe time\b", ["__TIME__"]),
]


def offline_answer(query, notes, hits, title):
    q = query.lower()
    for pattern, lines in SMALL_TALK:
        if re.search(pattern, q) and (not hits or hits[0][0] < 2.5):
            line = random.choice(lines)
            if line == "__TIME__":
                line = time.strftime("It is %I:%M %p, {t}. Time flies when one is indexing.").replace(" 0", " ")
            return line.format(t=title), []
    if not hits or hits[0][0] < 1.2:
        return (f"I've searched every note, {title}, and I'm afraid they're silent on that. "
                f"Perhaps it's time you wrote one.", [])
    q_tokens = set(tokenize(query))
    top = notes[hits[0][1]]
    sentences = re.split(r"(?<=[.!?])\s+|\n+", top["body"])
    scored = []
    for pos, s in enumerate(sentences):
        s_clean = re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", r"\1", s).strip(" -#*")
        if len(s_clean) < 12:
            continue
        overlap = len(q_tokens & set(tokenize(s_clean)))
        scored.append((overlap, -pos, s_clean))
    scored.sort(reverse=True)
    best = [s for overlap, _, s in scored[:2] if overlap > 0] or [s for _, _, s in scored[:1]] or [top["excerpt"][:200]]
    opener = random.choice([
        f"From {top['title']}, {title}:",
        f"Your note on {top['title']} has it, {title}.",
        f"According to {top['title']}:",
    ])
    used = [i for s, i in hits[:3] if s >= hits[0][0] * 0.5]
    return f"{opener} {' '.join(best)}", used


# --------------------------------------------------------------------------- the brain

class Brain:
    def __init__(self, cfg, notes):
        self.cfg = cfg
        self.notes = notes
        self.index = Index(notes)
        self.sessions = {}
        self.cooldown = {}   # (backend, model) -> time it may be tried again
        self.sticky = None   # the last (backend, model) that answered
        self.last_errors = []

    def add_note(self, note):
        self.notes.append(note)
        self.index.add(note)

    def plan(self):
        """(backend, model) attempts for the next question: last winner first, then the
        configured order, with recently failed models pushed to the back."""
        attempts = attempt_list(self.cfg, backend_chain(self.cfg))
        if self.sticky in attempts:
            attempts.remove(self.sticky)
            attempts.insert(0, self.sticky)
        now = time.time()
        fresh = [a for a in attempts if self.cooldown.get(a, 0) <= now]
        # Briefly-failed models are a last resort; ones parked for the day are skipped.
        cooling = [a for a in attempts if now < self.cooldown.get(a, 0) <= now + COOLDOWN]
        return fresh + cooling

    def describe(self):
        attempts = self.plan()
        if not attempts:
            return {"backend": "offline", "brain": backend_label(self.cfg, "offline"), "fallbacks": 0}
        b, m = attempts[0]
        return {"backend": b, "brain": backend_label(self.cfg, b, m), "fallbacks": len(attempts) - 1}

    def ask(self, session_id, question):
        title = self.cfg.get("user_title", "sir")
        hits = self.index.search(question)
        history = self.sessions.setdefault(session_id or "default", deque(maxlen=HISTORY_TURNS))
        attempts = self.plan()

        if not attempts:
            answer, used = offline_answer(question, self.notes, hits, title)
            history.append((question, answer))
            return {"answer": answer, "nodes": used, "brain": backend_label(self.cfg, "offline")}

        messages = []
        for q, a in history:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        now = time.strftime("%A %d %B %Y, %I:%M %p")
        messages.append({"role": "user", "content": f"{notes_block(self.notes, hits)}\n\nLocal time: {now}\n\n{question}"})
        system = system_prompt(title)

        start = time.time()
        errors, dead_backends, raw, winner = [], set(), None, None
        tries, per_provider = 0, Counter()
        for backend, model in attempts:
            if backend in dead_backends or per_provider[backend] >= MAX_PER_PROVIDER:
                continue
            if tries >= MAX_ATTEMPTS:
                errors.append(f"gave up after {MAX_ATTEMPTS} models")
                break
            tries += 1
            per_provider[backend] += 1
            left = TOTAL_BUDGET - (time.time() - start)
            if left < 5:
                errors.append("ran out of time")
                break
            try:
                raw = call_backend(self.cfg, backend, model, system, messages, timeout=min(ATTEMPT_TIMEOUT, left))
                if raw.strip():
                    winner = (backend, model)
                    break
                raise BrainError(f"{backend_label(self.cfg, backend, model)} returned an empty reply.")
            except BrainError as e:
                errors.append(str(e))
                self.cooldown[(backend, model)] = time.time() + COOLDOWN
                if e.skip_provider:
                    dead_backends.add(backend)
                if e.daily_cap:
                    scope = (self.cfg.get("providers", {}).get(backend, {}) or {}).get("daily_cap", "model")
                    if scope == "account":  # one allowance for every model: park the provider till reset
                        until = time.time() + seconds_to_utc_midnight()
                        for a in attempt_list(self.cfg, [backend]):
                            self.cooldown[a] = until
                        dead_backends.add(backend)
                    else:  # per-model allowance (Groq): park just this model for an hour
                        self.cooldown[(backend, model)] = time.time() + 3600
            except Exception as e:  # never let one odd provider take JARVIS down
                errors.append(f"{backend_label(self.cfg, backend, model)}: {e}")
                self.cooldown[(backend, model)] = time.time() + COOLDOWN
        self.last_errors = errors

        if winner is None:
            # Every model is busy or broken: still answer from the index rather than fail.
            answer, used = offline_answer(question, self.notes, hits, title)
            answer = f"My usual faculties are indisposed, {title}, so straight from the index. {answer}"
            history.append((question, answer))
            return {"answer": answer, "nodes": used, "brain": "Offline librarian (all models busy)",
                    "warning": "; ".join(errors[-3:])}

        self.sticky = winner
        self.cooldown.pop(winner, None)
        answer, used = split_sources(raw, hits)
        if used is None:  # model forgot the SOURCES line: trust retrieval only if it is strong
            used = [i for s, i in hits[:2] if s >= 3.0]
        # Store the bare question (not the note dump) so history stays small. Note numbers
        # are per-question, so earlier turns just record whether notes were used.
        history.append((question, f"{answer}\nSOURCES: {'earlier notes' if used else 'none'}"))
        return {"answer": answer or f"I'm lost for words, {title}. A first.", "nodes": used,
                "brain": backend_label(self.cfg, *winner)}


    def quota(self):
        """OpenRouter's free requests left today (a key check -- costs no model request)."""
        if not provider_key(self.cfg, "openrouter"):
            return None
        cached = getattr(self, "_quota", None)
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        try:
            req = urllib.request.Request(self.cfg["providers"]["openrouter"]["base_url"].rstrip("/") + "/key",
                                         headers={"authorization": f"Bearer {provider_key(self.cfg, 'openrouter')}",
                                                  "user-agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                q = json.loads(r.read()).get("data", {}).get("free_model_daily_requests")
        except Exception:
            q = None
        self._quota = (time.time(), q)
        return q


# --------------------------------------------------------------------------- command line

def _get_json(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "JARVIS"}), timeout=timeout) as r:
        return json.loads(r.read())


def list_free_models():
    """Print the models that are free right now (both lists are public, no key needed)."""
    print("OpenRouter -- free right now (only ':free' IDs are guaranteed never to bill):")
    try:
        models = _get_json("https://openrouter.ai/api/v1/models")["data"]
        free = [m for m in models if m["id"].endswith(":free") or m["id"] == "openrouter/free"]
        for m in sorted(free, key=lambda m: -m.get("created", 0)):
            print(f"  {m['id']:<55} ctx {m.get('context_length') or '?':>8}   {m.get('name', '')}")
    except Exception as e:
        print(f"  couldn't fetch: {e}")
    print("\nOpenCode Zen -- free models:")
    try:
        models = _get_json("https://opencode.ai/zen/v1/models")["data"]
        for m in models:
            if "free" in m["id"] or m["id"] == "big-pickle":
                print(f"  {m['id']}")
    except Exception as e:
        print(f"  couldn't fetch: {e}")


def test_models(cfg):
    """Ping every configured model and report which answer, and how fast."""
    system = "You are a terse test responder."
    msgs = [{"role": "user", "content": "Reply with exactly: Ready, sir."}]
    any_key = False
    for backend in cfg.get("brain_order", []):
        if not backend_available(cfg, backend):
            why = "no key" if is_api_provider(cfg, backend) or backend == "anthropic" else "not available"
            print(f"- {backend}: skipped ({why})")
            continue
        any_key = True
        for b, model in attempt_list(cfg, [backend]):
            t0 = time.time()
            try:
                out = call_backend(cfg, b, model, system, msgs, timeout=40)
                print(f"  OK   {time.time() - t0:5.1f}s  {backend_label(cfg, b, model):<55} -> {out.strip()[:40]!r}")
            except BrainError as e:
                print(f"  FAIL {time.time() - t0:5.1f}s  {backend_label(cfg, b, model):<55} {e}")
                if e.skip_provider:
                    break
    if not any_key:
        print("\nNo keys found. Set them once in PowerShell, then re-run this test:\n"
              "  setx OPENCODE_API_KEY \"your-zen-key\"\n  setx OPENROUTER_API_KEY \"your-openrouter-key\"")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import build
    if "--free" in sys.argv:
        list_free_models()
    elif "--test" in sys.argv:
        test_models(build.load_config())
    else:
        print(__doc__)
