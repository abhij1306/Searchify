> NOTE (v1 integrations branch): this is the MVP-era base-stack doc kept for context. For the integrations/Traffic/LLM-Analytics stack, follow `README.md` in this directory and `docs/integrations-traffic-analytics.md` — they supersede the details below.

# Searchify -- Known Issues / Limitations

These are expected limitations of the sandboxed local setup, not application
defects. Documented here so future sessions don't re-debug them.

## 1. BYOK provider connections use fake API keys

The seed script (`/memory/testing/Searchify/seed.sh`) creates 3 provider
connections (OpenAI/ChatGPT, Anthropic/Claude, Google/Gemini) with fake keys
(`sk-fake-demo-key-not-a-real-secret-0000000000`). There are no real
OpenAI/Anthropic/Google API keys available in this sandbox.

**Impact:**
- Clicking "Test connection" on the Providers page will fail (401/invalid-key
  response from the real provider APIs).
- Launching a brand-new audit ("Launch audit" button) will have all its tasks
  fail with `auth_failure` (401 Unauthorized) once the live `audit_worker`
  picks them up -- this is real behavior, not a bug, since the keys are not
  valid.
- The 4 pre-seeded audits (completed/partially_completed/failed/running) are
  **fabricated directly via SQLAlchemy ORM** (`seed_audits.py`), not run
  through real provider calls, specifically to give the UI realistic data to
  display without needing real credentials.

**Workaround (if real testing against live LLM providers is needed):** obtain
real OpenAI/Anthropic/Google API keys and use "Update key" on the Providers
page to replace the fake ones, then use "Test connection" or "Launch audit".

## 2. The live audit_worker can silently corrupt seeded "running"-state demo data if not careful

**Root cause:** `app/workers/audit_worker.py` + `app/orchestration/
postgres_task_queue.py` continuously polls Postgres for any `AuditTask` row
with `status IN (queued, retry_wait)` and `available_at <= now`, claims it,
and attempts a real provider call.

If a seed script creates a "running" demo audit (meant to represent an
in-flight run with no results yet, for UI screenshots) using a normal
`available_at = now`, the live worker will claim and execute those tasks
against the fake BYOK keys within seconds to minutes, and the whole audit
flips to `failed` -- destroying the intended "running" demo state.

**Fix applied:** `seed_audits.py`'s `seed_running()` pins `available_at` (and
`lease_expires_at` for the one task marked "running") to `_utcnow() +
timedelta(days=3650)` so the live worker's `available_at <= now` filter never
matches them. This was hit once during initial setup (2026-07-17) -- the
first version of the running-audit seed used `now`, and the audit flipped
from `running` to `failed` within ~5 minutes as the live worker consumed all
18 tasks and got 401/400 errors from the fake provider keys.

**If this recurs:** delete the corrupted `Audit` row (cascades to its
`AuditTask`/`AuditEvent` rows via the ORM's `cascade="all, delete-orphan"`)
and re-run `seed_audits.py` -- it is idempotent per-audit via a `seed_marker`
stored in `Audit.configuration`, so re-running only re-creates the missing
one.

## 3. `/health` is not reachable through the frontend's proxy

`GET localhost:3000/health` returns 404. This is expected: the Next.js
`rewrites()` config in `frontend/next.config.ts` only proxies `/api/:path*`,
and the root FastAPI `/health` route lives outside `/api/v1`. Always check
backend health at `localhost:8000/health` directly.

## 4. `audit_worker` log noise from the fake-key launch attempt

During this setup, launching/seeding activity produced worker log lines like:

```
HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 401 Unauthorized"
HTTP Request: POST https://generativelanguage.googleapis.com/v1beta/interactions "HTTP/1.1 400 Bad Request"
audit task crashed ... AttributeError("'list' object has no attribute 'get'")
```

The 401/400 lines are expected (fake keys). The `AttributeError` on the
Gemini connector path (`app.connectors.answer_engines.gemini`) appears to be
a real minor bug in how the worker/connector handles a certain
error-response shape from the Gemini API (a `list` where a `dict` was
expected) -- it does not block anything in this setup (the crashed task is
just marked failed and the sweeper reclaims/finalizes it), but is worth
flagging for the app's own maintainers as a possible defensive-coding gap in
the Gemini connector's error-parsing path. Not something this setup session
fixed, since it's a repo source-code issue outside the scope of environment
setup.
