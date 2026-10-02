# J.A.R.V.I.S. — a talking second brain

A 3D galaxy of your markdown notes that you can talk to. Ask a question out loud and JARVIS answers from your notes in a dry British butler's voice, flies the camera to the note the answer came from, and lights it up. Say "remember that…" and a new star is born. It runs on Python's standard library plus one web page: no pip, no npm, no build step.

## Start it

You need Python 3.9+ (no packages to install) and Chrome or Edge. On Windows, double-click **`start-jarvis.bat`**. Or, from this folder:

```
python server.py --open
```

Then use **Chrome or Edge** at http://localhost:4700 and click **Engage**. The click is what lets the browser play JARVIS's voice.

## Use it anywhere: GitHub Pages + Render

- **The page** is published to **https://rahul9994.github.io/Vibing-projects/jarvis/** by `.github/workflows/pages.yml` on every push.
- **The brain** (`server.py`, holding your API keys) runs on **Render's free tier**, set up from `render.yaml` in the repo root.

**Set up Render once (about 5 minutes):**

1. Go to [render.com](https://render.com), sign in with GitHub, then choose **New + → Blueprint** and pick `Rahul9994/Vibing-projects`. Render reads `render.yaml` and proposes a free web service called **rahul9994-jarvis**.
2. When it asks for the secret values, paste your **`GROQ_API_KEY`** and **`OPENROUTER_API_KEY`** into Render's form. `GITHUB_TOKEN` is optional (see below); leave it blank for now. Then click **Apply**.
3. Wait until the service says **Live**. Its address will be `https://rahul9994-jarvis.onrender.com`. The name was free on 2026-10-03; if Render adds a suffix, use the address it shows.
4. In the service, open **Environment** and reveal **`JARVIS_ACCESS_KEY`**. Render generated it, and it's your **passphrase**.
5. Open the page, click the **link icon** in the top-left panel, and enter the brain's URL and the passphrase. Each browser remembers them, so you do this once per device. Once the URL is final, putting it in `viewer/config.js` saves typing it on new devices.

**Good to know:**

- **Naps:** Render's free tier sleeps after about 15 idle minutes. The first question after a nap takes up to a minute, and JARVIS says "waking up…" while it waits.
- **Security:** the passphrase protects your free model allowances from strangers. Wrong guesses are rate-limited, and only `https://rahul9994.github.io` may call the brain from a browser. Your keys only ever live in Render's settings and your Windows environment, never in the repo.
- **Keeping "remember that…" notes:** Render's free disk is wiped on every restart. To keep notes made by voice, create a [fine-grained token](https://github.com/settings/personal-access-tokens/new) with access to **only** `Vibing-projects` and the permission **Contents: Read and write**, and put it in Render's `GITHUB_TOKEN`. JARVIS then commits each capture to `JARVIS/notes/captures/`, pulls them back on restart, and the Pages galaxy picks them up on its next publish.
- **Default URL:** `viewer/config.js` points at `https://rahul9994-jarvis.onrender.com`, verified as this JARVIS. Only ever put an address there that you have confirmed is yours.
- **Privacy:** this repo is public, so everything in `notes/` is public too. Keep private notes out of this repo.

## Give it a brain: free models

JARVIS works out of the box as an **offline librarian**: it finds the right note and reads the most relevant line from it. For real answers with wit it uses **free models from Groq (first) and OpenRouter (backup)**.

Run this once in PowerShell, typing your own keys. Never paste them into a chat window.

```
setx GROQ_API_KEY "gsk_your-groq-key"
setx OPENROUTER_API_KEY "sk-or-your-openrouter-key"
```

You don't need to restart anything: JARVIS reads keys from your Windows user environment on every question. Environment variables are safer than putting keys in `config.json`, because this folder syncs to OneDrive. Check that everything works with `python brain.py --test`. Note that it uses one free request per model it tests.

**Groq is first** because it's very fast: answers took 0.3–1.4 s in testing on 2026-10-03, against 6–40 s on OpenRouter and its free plan is generous. Each model allows 1,000 requests and 200,000 tokens a day, at 30 requests and 8,000 tokens a minute. Each model is told to think briefly or not at all, and to keep its thinking out of the reply (`reasoning_effort` and `include_reasoning` in `config.json`).

| Order | Provider · model | Notes |
|---|---|---|
| 1 | Groq · `openai/gpt-oss-120b` | **0.3–1.4 s** in testing; richest, wittiest answers; reasoning effort `low` |
| 2 | Groq · `qwen/qwen3.8-27b` | **0.4–1.3 s**; concise; reasoning off |
| 3 | Groq · `openai/gpt-oss-20b` | **0.6–0.7 s**; solid backup |
| 4 | OpenRouter · `qwen/qwen3.8-27b:free` | best OpenRouter model in testing, about 2–10 s, cites sources |
| 5 | OpenRouter · `nvidia/nemotron-3-super-120b-a12b:free` | very good, wittiest, 3–16 s |
| 6 | OpenRouter · `google/gemma-4-31b-it:free` | backup (was rate-limited during testing) |
| 7 | OpenRouter · `nvidia/nemotron-3-ultra-550b-a55b:free` | excellent but slow (up to 40 s) |

Left out on purpose:
- `inkling` and `inkling-small` only work inside approved coding apps.
- `openrouter/free` read its raw reasoning out as the answer.
- `apodex` and `dots` returned empty replies.
- $0 models without `:free` can start charging.

**Daily limits work differently per provider:**
- **Groq** limits each model separately. If one model hits its cap, only that model is skipped for an hour.
- **OpenRouter** shares one allowance across all models (see below), so hitting it pauses all of OpenRouter until the reset.
- Each question tries **at most 2 models per provider and 4 in total**, so a Groq outage falls straight through to OpenRouter.

**OpenRouter's allowance: 50 requests per day.** OpenRouter's free tier allows 50 free-model requests per UTC day, resetting at 5:30 am IST. The HUD shows what's left, e.g. "26/50 free today". To make that go further, JARVIS:
- starts each question with whichever model answered last time;
- skips a model for 90 seconds after it fails, and gives a stalled model 22 seconds before moving on;
- when the daily cap is reached, stops calling OpenRouter until it resets and relies on Groq, or the index if Groq is busy too.

**OpenCode Zen.** Your Zen key works, but Zen's free models refuse outside the OpenCode app ("free tier can only be used from within OpenCode"). Its `models` list is therefore empty. Adding paid Zen model IDs would work, but those are billed.

- **Free models change often.** `python brain.py --free` lists what's free right now (it uses no requests); edit `providers.openrouter.models` to match.
- **Privacy:** free endpoints may log prompts, and some use them for training. Your questions and the matching note text are sent to the provider. That's fine for the sample notes; think before pointing it at private notes.

**Other brains** (optional, for later in the chain):

| Brain | Needs |
|---|---|
| `anthropic`: Claude (`claude-opus-5-5`, effort `low`, with server-side refusal fallback) | `setx ANTHROPIC_API_KEY "sk-ant-..."` |
| `claude-cli`: `claude -p` on your Claude Code subscription | `claude` on PATH |
| `ollama`: a local model, free and private | `ollama pull llama3.2`, then set `"ollama_model": "llama3.2"` |

## Use your own notes

Set `"notes_dir"` in `config.json` to any folder of `.md` files (an Obsidian vault works), using forward slashes, e.g. `"C:/Users/rockz/Documents/Vault"`. Then restart the server. It re-indexes on every start; `python build.py` re-indexes by hand. The 29 notes in `notes/` are a sample vault for a fictional coffee roastery.

## Things to try (sample notes)

- "When will the drive-through open?"
- "How much cash do we have in the bank?"
- "Should we take the bank loan or the equipment finance for the new roaster?"
- "Who is Priya and why does she want a second roaster?"
- "Tell me a joke" (small talk: the camera stays put)
- "Remember that prompt packs make excellent free gifts" (a new star is born in `notes/captures/`)

## Voice test plan

1. Click **Engage**. You should hear "Good evening, sir. 29 notes indexed…". No sound? Click the page once and check the volume and the site's sound permission.
2. Type a question and press Enter. You should see "● thinking…", then the answer is spoken, the reactor pulses, the camera dives to the source note, and its panel opens.
3. Click the **mic** (or press **M**), allow the microphone, and ask a question aloud. You should see "● listening…", your words appear in the box, and it answers out loud.
4. While it's talking, press **M** or start typing. It stops mid-sentence (barge-in).
5. Turn on **Wake word "Jarvis"** (top right) and say "Jarvis, what are our goals for 2026?" with your hands off the keyboard. Or say "Jarvis…", wait for "Yes, sir?", then ask.

Keys: `/` ask · `M` talk · `Esc` stop talking and reset · `R` overview · click a constellation in the legend to light up that group.

## Files

| File | What it does |
|---|---|
| `build.py` | scans the notes and writes `viewer/graph-data.js` (node id = array index) |
| `brain.py` | BM25 note search, the butler persona, the provider chain (Zen, OpenRouter, Claude, CLI, Ollama, offline); `--test` and `--free` |
| `server.py` | serves **only** `viewer/`, plus `/chat`, `/remember`, `/api/status`, `/api/graph` and `/api/health`. Localhost-only by default; in hosted mode it adds the passphrase, CORS and rate limits |
| `github_sync.py` | optional: commits "remember that…" notes to GitHub so they survive Render restarts |
| `viewer/config.js` | where the page finds the brain when opened from GitHub Pages |
| `viewer/` | the page: 3d-force-graph + three.js (from a CDN), bloom, starfield, voice |
| `config.example.json` | all settings: models, order, notes folder. Optional: copy it to `config.json` to customise (`config.json` is git-ignored; the built-in defaults match the example) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Mic button does nothing | Use Chrome or Edge. Click the lock icon in the address bar and allow the microphone. |
| No sound | Click the page once (browsers block audio until you interact) and check that **Speak answers** is on. |
| Blank page | The 3D libraries load from esm.sh, so you need internet. Hard-reload with Ctrl+Shift+R. |
| "rejected the API key" | Re-run `setx` with the right key, then `python brain.py --test`. |
| "all models busy" | Free tiers are rate-limited. Wait a minute, add the other provider's key, or reorder the `models` lists. |
| Answers are generic | `notes_dir` points at the wrong folder. Use the full path, then restart. |
