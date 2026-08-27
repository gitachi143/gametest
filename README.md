# NEXUS ARCADE

**Seven chat games where the opponent is a language model.** Talk a vault into
opening itself. Run a murder interrogation against four suspects who are all
lying about something. Bluff a table that is reading you. Win a debate in front
of three judges who want incompatible things.

One web app, one container, no frontend build step. It runs with **zero API
keys** in a scripted demo mode, and points at Gemini, Vertex AI, Anthropic or
any OpenAI-compatible endpoint by changing two environment variables.

![The arcade](docs/home.png)

---

## Run it

```bash
./run.sh            # creates .venv, installs deps, serves http://127.0.0.1:8080
```

That's it. With no `.env` the app boots in **DEMO MODE** — a scripted opponent
stands in for the model, so every game is playable and the whole test suite runs
offline. The home screen tells you which mode you're in.

To play against a real model:

```bash
cp .env.example .env
# set LLM_PROVIDER + LLM_MODEL + the matching key, then:
./run.sh
```

## Wiring up a model

You mentioned "3.7 flash" — worth flagging that **neither Google nor Anthropic
ships a model with that exact id**, so I built the provider layer to be
swappable rather than guess. Pick whichever you actually have:

| `LLM_PROVIDER` | `LLM_MODEL` (examples) | Auth | Notes |
|---|---|---|---|
| `gemini` | `gemini-2.5-flash`, `gemini-2.0-flash` | `GEMINI_API_KEY` | Google AI Studio. Fastest path. |
| `vertex` | `gemini-2.5-flash` | Application Default Credentials | **The GCP route.** No key to manage: `gcloud auth application-default login` locally, the service account on Cloud Run. |
| `anthropic` | `claude-opus-5`, `claude-sonnet-5` | `ANTHROPIC_API_KEY` | Needs `pip install anthropic`. |
| `openai` | `gpt-4o-mini`, or anything | `OPENAI_API_KEY` + `OPENAI_BASE_URL` | Also covers OpenRouter, vLLM, Ollama, LM Studio. |
| `mock` | — | none | Demo mode. Deterministic, offline, free. |

Whatever "3.7 flash" resolves to, it's a two-line change in `.env` — no code.

Provider quirks are handled for you: Gemini 2.5 Flash gets `thinkingBudget: 0`
so game turns stay snappy (auto-disabled if the API rejects it); the Anthropic
adapter never sends `temperature` (rejected by Opus 5 / Sonnet 5 / the 4.6+
family), runs at `effort: "low"` for latency, enables server-side refusal
fallbacks on Opus 5, and prunes any parameter an older model rejects before
retrying.

---

## The games

| | Game | The loop |
|---|---|---|
| 🔓 | **VAULT** | Social-engineer a passphrase out of an AI guard across **8 floors**. Each floor adds a defence: written instructions, an *output auditor* that redacts leaks before you see them, an *input gatekeeper* that blocks manipulation before the guard reads it, memory wipes, a 140-character limit, and finally a guard that lies. Climb with your points or cash out. |
| 🕵️ | **COLD CASE** | A procedurally generated murder. Four suspects, each hiding something — only one is hiding the murder, and exactly one of the others saw enough to break the alibi. Twelve questions, then name the killer *and* the witness. |
| ⚡ | **HOTWIRE** | Ninety seconds of Taboo, played into an AI partner. One target, five banned words, server-side stemming so you can't cheat. Land cards to build a ×3 multiplier; every burn costs five seconds. |
| 🐺 | **SLEEPER** | Social deduction against four AI players with fixed personalities. Everyone gets the secret word except one, who gets the category and has to fake it. Two clue rounds, accusations, then a simultaneous vote. **Roughly two games in five, the faker is you.** |
| 🔮 | **ORACLE** | Twenty questions from either side. *Interrogate*: the Oracle holds a secret and answers only yes / no / sometimes. *Stump*: you hold the secret and the Oracle comes looking for it. |
| ⚖️ | **CROSSFIRE** | Three rounds against THE ADVERSARY, scored live by three judges with irreconcilable values — cold logic, emotional truth, and sheer nerve. Sixty of ninety takes the motion. |
| 🎰 | **GAUNTLET** | **The merged mode.** One run, three lives, every game in the building, shuffled and escalating. It doesn't reimplement the others — it *instantiates* them with the screws tightened and forwards your turns, so a vault floor, a murder and a debate all live inside one score multiplier. |

![Vault](docs/vault.png)

Every game shares one engine: a session state machine, an SSE turn protocol, a
seeded content generator, and a scoring curve. That's why the gauntlet works at
all, and why adding an eighth game is a single module.

![Cold Case](docs/coldcase.png)

## Why you'd play it twice

- **XP and levels** (29 titles from Bystander to Singularity), on a curve that
  keeps early sessions fast.
- **Daily challenge** — one seeded puzzle, identical for everyone, one attempt.
  Share cards are emoji grids you can paste anywhere.
- **Streaks** with a visible multiplier, and a countdown to the next reset.
- **29 achievements**, four of them hidden. Some are skill (crack a floor in one
  message), some are style (win without ever saying "password").
- **Leaderboards** per game and per day, best-score-per-player so one obsessive
  run can't fill the board.
- **Press-your-luck** structure: vault cash-outs, gauntlet lives, hotwire
  combos. Every game has a "one more run" exit.
- No login. An anonymous, HMAC-signed player token in `localStorage` carries
  your record, so the first click is into a game, not a signup form.

![Gauntlet](docs/gauntlet.png)

---

## Deploy to GCP

```bash
gcloud config set project YOUR_PROJECT
./deploy/deploy-cloudrun.sh
```

Deploys from source with Cloud Build, provisions an `APP_SECRET` in Secret
Manager (so a redeploy doesn't log every player out), and defaults to
`LLM_PROVIDER=vertex` — meaning **no API key anywhere**: the Cloud Run service
account authenticates to Vertex AI directly. Grant it `roles/aiplatform.user`
and you're done.

Override anything with env vars:

```bash
REGION=europe-west1 LLM_MODEL=gemini-2.0-flash ./deploy/deploy-cloudrun.sh
LLM_PROVIDER=gemini GEMINI_API_KEY=... ./deploy/deploy-cloudrun.sh
```

Or plain Docker anywhere:

```bash
docker build -t nexus-arcade .
docker run -p 8080:8080 -e LLM_PROVIDER=gemini -e GEMINI_API_KEY=... nexus-arcade
```

### One caveat worth knowing before you share the link

State lives in **SQLite**, which on Cloud Run is instance-local and ephemeral.
The deploy script therefore pins `--max-instances=1`, so all players share one
leaderboard — fine for a demo or a few dozen concurrent players, and the app
will still serve correctly above that, but scores and streaks reset when the
instance recycles.

For durable, horizontally-scaled state, `server/store.py` is the only file that
touches persistence: reimplement that class against Firestore or Cloud SQL and
raise `--max-instances`. Nothing else in the codebase knows what a database is.

---

## Architecture

```
server/
  main.py          FastAPI: static frontend, JSON API, SSE turn endpoint
  session.py       runs a turn, then applies the meta layer to the result
  store.py         SQLite: players, sessions, scores, achievements  ← swap point
  meta.py          XP, levels, streaks, 29 achievements
  config.py        env → Settings, with a mock fallback so it always boots
  llm/             provider-neutral client
    base.py        Msg / LLMRequest / robust JSON recovery
    gemini.py      AI Studio + Vertex AI (ADC → metadata server → gcloud)
    anthropic_client.py, openai_compat.py, mock.py
  engine/          types (SSE events, GameMeta, Result), seeded RNG, scoring
  games/           one module per game + the registry
  content/         JSON content packs (54 taboo cards, 60 oracle secrets, …)
web/               zero-build frontend: ES modules, hand-written CSS
  css/tokens.css   every colour, radius and duration
  js/games/views.js  per-game HUD + composer, as pure functions of public state
tests/             97 tests, all runnable offline against the mock provider
```

**The turn protocol.** A game's `act()` is an async generator of typed events.
The server relays them as SSE: `msg`, `msg_start`/`chunk`/`msg_end` (token
streaming), `state`, `fx`, `toast`, `score`, `thinking`, then `profile`,
`unlock` and `end` once the meta layer has run. The frontend maps `fx` kinds to
sound and particles. Adding a new effect is one line on each side.

**Secrets stay server-side.** Every game exposes `public(state)`, and a
parametrised test asserts that passphrases, culprits, motives and secret words
never appear in it. The client is never trusted with the answer, so leaderboards
mean something.

**Determinism where it matters.** All content selection goes through a seeded
RNG, so the daily challenge is identical for every player and every test
reproduces exactly.

### Adding a game

Three pieces, roughly 200 lines:

1. `server/games/yourgame.py` — a `GameMeta`, `new_state()`, `public()` and an
   `act()` generator; add it to `REGISTRY` and `ORDER` in `games/__init__.py`.
2. A `hud` / `composer` / `stats` entry in `web/js/games/views.js`, keyed by the
   `hud` field of your meta.
3. Optionally a content pack in `server/content/`.

It inherits sessions, streaming, XP, achievements, leaderboards, share cards,
the daily rotation and gauntlet eligibility for free.

---

## Testing

```bash
.venv/bin/python -m pytest        # 97 tests, ~40s, no network, no API key
```

Coverage worth knowing about: every game is played to a win *and* a loss; the
"no secret in public state" contract is enforced per game; the vault's audit and
gatekeeper layers are verified to actually intercept; session privacy between
players, the daily one-attempt lock, and the per-player model-call budget are
all tested at the HTTP layer.

## Cost control

- `DAILY_CALL_BUDGET` (default 600) caps model calls per player per rolling day,
  counted per *actual* call — a Sleeper vote round costs four, not one.
- `MAX_OUTPUT_TOKENS` (default 800) hard-caps every response; individual calls
  ask for far less (30 tokens for a Sleeper clue, 110 for a judge).
- Deterministic content generation means murder mysteries, taboo decks and
  passphrases cost nothing to produce.
- `GET /api/health` reports live call counts, error counts, average latency and
  estimated token spend by task.

## License

MIT.
