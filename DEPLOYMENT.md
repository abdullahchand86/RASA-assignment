# Deployment — Sustainable Trip Planner (Task 6)

## Architecture

```
frontend (3000) ---> rasa server (5005) ---> action-server (5055, internal only)
                                                     |
                                          Climatiq / Amadeus / OpenCage APIs
```

The action server is exposed only on the internal Docker network
(`expose`, not `ports`, in `docker-compose.yml`) — it should never be
reachable directly from outside the container network, since it holds the
logic that calls third-party APIs with your keys.

## Option A — Local / grading demo: Docker Compose

**Prerequisites:** Docker + Docker Compose installed.

1. Clone the repository:
   ```bash
   git clone <your-github-repo-url>
   cd eco-travel-advisor
   ```
2. Copy the environment template and fill in real API keys:
   ```bash
   cp .env.example .env
   # edit .env with your Climatiq / Amadeus / OpenCage keys
   ```
3. Train the Rasa model (one-off, before first run):
   ```bash
   docker run -v $(pwd):/app rasa/rasa:3.6.20-full train
   ```
4. Start all services:
   ```bash
   docker compose up --build
   ```
5. Open the frontend at `http://localhost:3000`. The Rasa REST endpoint is
   at `http://localhost:5005/webhooks/rest/webhook`.
6. Stop with `docker compose down`.

## Option B — Zero-cost cloud demo: HuggingFace Spaces (recommended)

HuggingFace Spaces with the Docker SDK is the recommended target for this
assignment: no billing risk (unlike AWS/GCP, where an unattended instance
can incur charges), and it's sufficient for a graded demonstration.

1. Create a new Space at huggingface.co → Spaces → New Space → SDK: **Docker**.
2. Since Spaces runs a single container, combine the Rasa server and action
   server into one `Dockerfile` for this deployment target only (a
   multi-service Compose setup like Option A is not directly supported on
   Spaces' free tier). A simple approach: use a process manager (e.g.
   `honcho` or a shell script starting both processes) inside one image, or
   deploy the frontend as a separate static Space and point it at the Rasa
   Space's public URL.
3. Add your API keys as **Space secrets** (Settings → Repository secrets),
   not as committed `.env` values — Spaces injects these as environment
   variables at runtime, which keeps them out of the public repo history.
4. Push your code (`git push`) — the Space builds and deploys automatically
   on each push.
5. For local development against the deployed Space, `pyngrok` can tunnel a
   local action server temporarily during testing:
   ```python
   from pyngrok import ngrok
   public_url = ngrok.connect(5055)
   print(public_url)
   ```

## Environment variables and secrets

| Variable | Purpose | Where set |
|---|---|---|
| `CLIMATIQ_API_KEY` | Carbon calculation | `.env` (local) / Space secret (cloud) |
| `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` | Hotel/flight sandbox data | `.env` / Space secret |
| `OPENCAGE_API_KEY` | Geocoding | `.env` / Space secret |

`.env` is listed in `.gitignore` — verify with `git status` before your
first commit that it does not appear as a tracked file.

## GDPR / data handling in deployment

- No persistent tracker store is configured by default (in-memory only),
  meaning conversation data does not survive a container restart — this is
  a deliberate minimisation choice for the coursework deployment, documented
  in `endpoints.yml`.
- If you switch to a persistent SQL tracker store for a longer-running demo,
  document a retention/deletion policy in your report and implement a way to
  fulfil a user's erasure request (even a manual `DELETE FROM events WHERE
  sender_id = ?` script is acceptable to demonstrate the capability exists).

## Replication checklist for your submission

- [ ] GitHub repo link included in your report (required graded deliverable)
- [ ] `README.md` in the repo root with: clone → `.env` → `docker compose up`
- [ ] All code commented (see `actions.py` docstrings)
- [ ] `.env` confirmed absent from git history
- [ ] Screenshot of a successful `docker compose up` run included in report
- [ ] Screenshot or recording of the deployed Space (if used) included in report
