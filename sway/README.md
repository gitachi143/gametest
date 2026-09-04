# SWAY

**Every word is a weapon.**

A persuasion roguelike played entirely in conversation. You face a ladder of
minds — a doorman, a registrar, a fence, a committee that argues with itself —
and each one has a position you have to talk them off. You do it by playing
**tactic cards** that only pay out if the message you actually write does what
the card promised.

It runs as a single container: FastAPI, server-sent events, no frontend build
step, SQLite for the meta layer, and any of four model providers behind one
interface. With no credentials at all it runs in DEMO MODE against a scripted
opponent.

---

## The loop

**One floor is one conversation.** A mind has a hidden objective, a
**resistance** bar, and a limited number of exchanges.

1. **Read them.** Every mind has a **vulnerability** and a **resistance** — a
   family of rhetoric that lands double, and one that mostly bounces. Both are
   hidden at the start. Their replies leak which is which, or you can spend your
   one free *Read them*.

2. **Load up to three tactics.** Cards cost **focus**. A card is a contract:
   play `Flatter` and your message has to pay a specific compliment. Play
   `Keystone` and the whole message has to be one sentence. Play
   `The Perfect Word` and it has to be five words or fewer.

3. **Write the message.** That is the move. A judge grades how well you executed
   each card, 0–3, and how much force the message carries *for this particular
   person*. A fumbled card contributes nothing, and the character may well
   mention that they saw you try it.

4. **Watch it multiply.**

   ```
   Persuasion 8 / 10                        440
   Flatter                 masterful      ×1.70
   Proud of the Post      vulnerable      ×2.00
   Momentum ×3                            ×1.36
   ────────────────────────────────────────────
                                          2 034
   ```

   Card multipliers stack with their vulnerability, your momentum, and your
   relics. Good turns compound, and that is where the enormous numbers come from.

5. **Build the deck.** Every cleared floor offers one of three things — a new
   tactic, or a relic that rewrites a rule. A run is won in the reward screens
   as much as in the conversations.

Levels unlock tactics and relics permanently, and they start appearing in run
rewards the moment you own them.

## Five modes, one engine

| Mode | Shape | Minutes |
| --- | --- | --- |
| **Ascent** | The full run. Eight floors, a holdout gate at four, a boss at eight. | 12–18 |
| **Blitz** | One mind, three exchanges, no cards, no reading them first. | 2 |
| **Holdout** | The board flipped: an expert spends eight exchanges getting one thing out of *you*. | 6–9 |
| **The Daily** | Identical minds, identical card offers, identical rolls, for everybody. One attempt. | 8–12 |
| **Endless** | Never stops, never stops scaling. The leaderboard is the floor you died on. | 15+ |

`app/engine/run.py`'s `MODES` table is the entire difference between them.

## Run it

```bash
./sway/run.sh                       # DEMO MODE, no credentials needed
# → http://127.0.0.1:8080
```

To play against a real model, pick one provider:

```bash
cp sway/.env.example sway/.env      # then fill in one block
```

| `LLM_PROVIDER` | Credential | Notes |
| --- | --- | --- |
| `azure` | `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` | **What the deployed service uses.** Azure OpenAI's v1 API — OpenAI-compatible, no `api-version` to track |
| `gemini` | `GEMINI_API_KEY` | Easiest — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `vertex` | `GOOGLE_CLOUD_PROJECT` | The GCP fallback. No API key: uses ADC, the metadata server, or `gcloud` |
| `anthropic` | `ANTHROPIC_API_KEY` | |
| `openai` | `OPENAI_API_KEY` | Any OpenAI-compatible `OPENAI_BASE_URL` |

A provider named without its credential falls back to DEMO MODE rather than
serving 500s on every turn. `/api/health` always tells you which one is live.

```bash
cd sway && python -m pytest         # 178 tests, no credentials, no network
```

## Deploy

```bash
az login
./sway/deploy-azure.sh
```

Idempotent — run it again to redeploy. It registers the resource providers a
fresh subscription leaves off, creates an Azure OpenAI resource and deploys
`gpt-4.1-mini` into it, builds the image inside Azure Container Registry (so no
local Docker is needed), generates `APP_SECRET` once into the app's secrets, and
deploys to Azure Container Apps as its own app — scale-to-zero, one replica max.

The build context is `sway/`, so the other app in this repo is never uploaded.
The resource group, Container Apps environment, registry and Azure OpenAI
resource are shared with Nexus Arcade — one environment costs less than two —
but each app owns its own secrets, image repository and revisions, so
redeploying one cannot disturb the other.

Point it at a resource you already have, or change the model:

```bash
AZURE_OPENAI_ENDPOINT=https://mine.openai.azure.com AZURE_OPENAI_API_KEY=... \
  ./sway/deploy-azure.sh
LLM_MODEL=gpt-4o-mini LOCATION=swedencentral ./sway/deploy-azure.sh
```

`/api/health` reports which provider is live, and `llm.throttles` is the only
external signal that a call was rate-limited and fell back.

```bash
az containerapp logs show -n sway -g llm-games-rg --tail 50
```

Azure OpenAI meters a real per-minute token quota and answers a 429 with a
`Retry-After`. The adapter obeys it, capped at 8 seconds — an exhausted quota
can ask for 60, and a turn stranded that long is worse than one that falls back
to a deterministic verdict. If real load throttles you, raise the deployment's
TPM with `AOAI_CAPACITY=150`; that is a genuine dial, unlike Vertex's shared
capacity.

### On a restricted subscription

Azure for Students and MSDN subscriptions disable ACR Tasks, cap the account at
one Container Apps environment, pin deployments to a few regions, and may sit
under a tenant policy that demands tags. The script handles the tags and the
regions itself; the other two need flags:

```bash
gh workflow run build-images.yml --ref "$(git branch --show-current)"
SHA=$(git rev-parse HEAD)
ENVIRONMENT=<existing-env> ENVIRONMENT_GROUP=<its-group> \
  IMAGE=ghcr.io/<owner>/sway:$SHA \
  REGISTRY_USERNAME=<owner> REGISTRY_PASSWORD=$(gh auth token) \
  ./sway/deploy-azure.sh
```

`.github/workflows/build-images.yml` at the repo root builds both apps' images.
See the Nexus Arcade README for the full table of what each error means.

### The GCP path, kept as a fallback

```bash
PROJECT_ID=your-project ./sway/deploy.sh
```

Unchanged: enables the APIs, creates `APP_SECRET` in Secret Manager once, grants
the runtime service account `roles/aiplatform.user`, and deploys to Cloud Run as
its own service.

## How it is put together

```
sway/
  app/
    main.py            FastAPI: static frontend, JSON API, one SSE endpoint
    session.py         the seam between a game and a player: XP, streaks, badges
    store.py           SQLite. Replace this module to run more than one instance.
    engine/
      combat.py        the damage maths - the one file to read first
      encounter.py     one conversation, as a state machine
      run.py           MODES, floor plans, rewards, share cards
      deck.py          draw / hand / discard as three lists and a seed
      relics.py        relic effects, flattened for combat
      meta.py          levels, the unlock ladder, badges
      rng.py           seeded everything, so the Daily is really the same puzzle
    ai/
      client.py        five providers behind one interface, gated and retried
      prompts.py       every prompt in the game, in one file
      judge.py         verdicts, normalised, with a deterministic fallback
      mock.py          the scripted opponent
    content/
      cards.json       38 tactics and defences, each with its grading contract
      minds.json       15 minds, 3 holdout interrogators
      relics.json      18 relics
  web/                 ES modules and CSS, served as-is. No build step.
  tests/               178 tests
```

### Three decisions worth knowing

**The mind performs; the judge decides.** Two separate calls with two separate
jobs. The character is never told a number — game state reaches it only as prose
("they are getting to you; hold the line, but let some strain show"). Letting
either call do the other's job is how a game like this starts feeling arbitrary.

**The client never does arithmetic.** Each step of the resolve chain carries the
running base and multiplier *after* it applies, so the animation the player
watches add up is exactly what the server scored. Replay the same chain and you
get the same number.

**A degraded judge says so.** `judge.py` falls back to a deterministic heuristic
when the model is throttled, unparseable or absent — and marks the verdict
`fallback: true`, which the chain displays. A silent fallback that looks like
real scoring is worse than an honest one that looks degraded. Repetition is
checked in Python either way, because the whole game collapses if resending the
same paragraph keeps scoring.

### One caveat

State is SQLite on local disk, and the app runs with one replica max so that
every player shares one file. That disk is `/tmp`, so on Container Apps — as on
Cloud Run — the leaderboard and unlocked levels reset whenever the replica idles
out or a new revision deploys. Point `app/store.py` at Azure SQL or Cosmos DB
before raising the replica cap.

The cheap half-measure, if you only want it to survive a redeploy: mount an
Azure Files share at `/data` and set `DB_PATH=/data/sway.db`. One replica still,
but the file outlives the revision.
