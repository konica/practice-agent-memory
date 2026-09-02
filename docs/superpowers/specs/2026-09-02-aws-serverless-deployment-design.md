# Deploying the mem0 chatbot to AWS — serverless design

**Status:** design, approved for planning
**Date:** 2026-09-02
**Scope:** `use_mem0/` (FastAPI backend + Vite SPA) deployed to a greenfield AWS account

## 1. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Posture | Cheap-to-free until the app has users | The earlier "production, modest traffic" design cost ~$153/mo, ~94% of it fixed cost paid at zero traffic |
| Compute | Lambda container image + Function URL, response streaming | No ALB ($22/mo floor), no NAT ($34/mo), per-request billing |
| Edge | CloudFront, one distribution, two origins | Single origin for the browser: first-party cookies, no CORS |
| SPA hosting | S3 private bucket behind CloudFront OAC | Serving static assets from Lambda would bill GB-seconds |
| Database | Neon free tier (external managed Postgres) | Lambda must run outside a VPC (see 2.1), so the DB needs a public endpoint |
| IaC | Terraform | Team standard |
| TLS / domain | CloudFront default `*.cloudfront.net` initially | ACM cannot validate a duckdns.org name — see §10 |
| Region | us-east-1 | CloudFront cert requirement; Neon colocation |

Rejected: ECS Fargate (~$57/mo floor, ALB is unavoidable), a Lightsail box (~$14/mo flat), Lightsail Container Service (~$10/mo flat). All three are simpler and cheaper to *operate*; the serverless design was chosen because it is the only one that is genuinely ~$0 with no users. The trade-offs accepted in exchange are recorded in §9.

## 2. Architecture

```
                    ┌───────────────────────────────────────┐
   browser ────────▶│ CloudFront distribution               │
   (one origin)     │                                       │
                    │  /agent, /auth/*, /conversations*,     │
                    │  /health, /ready ──────────┐           │
                    │                            │           │
                    │  /*  ──────────┐           │           │
                    └────────────────┼───────────┼───────────┘
                                     │           │
                          ┌──────────▼──┐   ┌────▼─────────────────┐
                          │ S3 (OAC)    │   │ Lambda Function URL  │
                          │ Vite bundle │   │ InvokeMode=          │
                          └─────────────┘   │  RESPONSE_STREAM     │
                                            │ arm64 container      │
                                            │ LWA + uvicorn        │
                                            └────┬─────────────────┘
                                                 │ (no VPC — free egress)
                    ┌────────────────────────────┼───────────────┐
                    ▼              ▼             ▼               ▼
                 Neon           OpenAI      mem0 Platform    Google OAuth
              (Postgres)                     LangSmith
```

The browser only ever talks to the CloudFront domain. That is what makes the session cookie first-party, keeps `SameSite=Lax` valid, and removes CORS from the picture entirely.

### 2.1 Why Lambda must run outside a VPC

AWS Lambda Function URLs **do not support response streaming from a function attached to a VPC**. Streaming inside a VPC requires calling `InvokeWithResponseStream` through the SDK plus an interface endpoint — which a browser cannot do.

This single constraint cascades:

- The function runs outside any VPC → **internet egress is free**, no NAT Gateway, no VPC endpoints.
- The function has no route into a private subnet → **the database must have a public endpoint**.
- RDS and Aurora Serverless v2 would have to be made publicly accessible with a permissive security group to work. That is worse than using a managed Postgres designed for public access.
- → **Neon**, which also gives scale-to-zero and a free tier.

### 2.2 Components

**CloudFront distribution.** Two origins, five behaviours.

| Path pattern | Origin | Cache policy | Origin request policy |
|---|---|---|---|
| `/agent` | Lambda Function URL | CachingDisabled | AllViewerExceptHostHeader |
| `/auth/*` | Lambda Function URL | CachingDisabled | AllViewerExceptHostHeader |
| `/conversations*` | Lambda Function URL | CachingDisabled | AllViewerExceptHostHeader |
| `/health`, `/ready` | Lambda Function URL | CachingDisabled | AllViewerExceptHostHeader |
| `*` (default) | S3 | CachingOptimized | — |

Resolve policy IDs with Terraform `data "aws_cloudfront_cache_policy"` / `data "aws_cloudfront_origin_request_policy"` data sources rather than hardcoding UUIDs.

**`AllViewerExceptHostHeader` is mandatory, not a preference.** A Lambda Function URL rejects requests whose `Host` header does not match its own `*.lambda-url.us-east-1.on.aws` domain. Forwarding the viewer's Host produces a 403. This is the opposite of the ALB case, and it is precisely why `request.url_for()` cannot be used to build the OAuth redirect URI (§4.2).

Other required settings:

- `compress = false` on the Lambda behaviours. Compression needs a `Content-Length`; a chunked SSE response has none, so it buys nothing and adds `Accept-Encoding` to the cache key.
- `origin_read_timeout = 60` on the Lambda origin. CloudFront's origin response timeout is an **inter-packet** timeout, not a total-response timeout, so a heartbeat (§4.5) keeps a multi-minute stream alive indefinitely.
- All HTTP methods allowed on the Lambda behaviours.
- Custom error responses must **not** be used for the SPA 404→`index.html` rewrite. They are per-distribution, not per-behaviour, and would rewrite the API's deliberate 404s — `conversations/ownership.py` returns 404 so another user's conversation is indistinguishable from a nonexistent one. Use a **CloudFront Function** on the default behaviour instead.

**S3 bucket.** Private, origin access control (OAC, not the legacy OAI, not the website endpoint). Hashed assets under `/assets/*` uploaded with `Cache-Control: public,max-age=31536000,immutable`; `index.html` with `Cache-Control: no-cache`.

**Lambda function.** Container image on `linux/arm64`.

| Setting | Value | Reason |
|---|---|---|
| Memory | 2048 MB | Lambda CPU scales with memory; this is the main lever on cold-start time. Free at low traffic (§8) |
| Timeout | 120 s | You are billed for the **full** streaming duration, and a client disconnect does **not** stop the billing. Never 900 |
| Reserved concurrency | 10 | Bounds both the monthly cost blast radius and the database connection count |
| Function URL | `InvokeMode = RESPONSE_STREAM`, `AuthType = NONE` | Streaming; see §9.3 for why AuthType cannot be AWS_IAM |
| Architecture | arm64 | $0.0000133334/GB-s vs $0.0000166667 on x86 |

Environment: `AWS_LWA_INVOKE_MODE=response_stream`, `AWS_LWA_READINESS_CHECK_PATH=/health`, `PORT=8080`.

**Database — Neon free tier.** 0.5 GB storage, 100 CU-hours/month, 5 GB egress, scale-to-zero after 5 minutes idle (not disableable), resume in milliseconds. Use the **pooled** endpoint, which requires the checkpointer change in §4.1.

## 3. The Docker image — one image, four targets

This is the highest-leverage decision in the design. A single image runs under `docker compose` locally, on Lambda, on Fargate, and on Lightsail, so a future migration is a deployment change rather than a rewrite.

```
FROM python:3.12-slim AS deps        # uv sync --frozen --no-dev -> /app/.venv
FROM node:22 AS web                  # npm ci && npm run build (VITE_API_BASE="")
FROM python:3.12-slim AS runtime
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:<pinned-release> /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=deps /app/.venv /app/.venv
COPY --from=web  /app/dist   /app/static
ENV PORT=8080
CMD ["uvicorn","app.main:app","--factory","--host","0.0.0.0","--port","8080", \
     "--proxy-headers","--forwarded-allow-ips=*"]
```

Notes:

- **Python 3.12**, not 3.14 — better wheel coverage, and it is the floor for SnapStart should it ever become viable.
- The Lambda Web Adapter binary is **inert outside Lambda**. It is a file in `/opt/extensions` that nothing reads unless the Lambda runtime is present. ~10 MB for full portability. Pin an exact released tag and verify it exists (`docker manifest inspect`) — never `latest`, which would make every rebuild a silent behaviour change.
- The SPA is baked into the image at `/app/static` so local dev is single-origin too. In the deployed design CloudFront serves the SPA from S3 instead; the same bundle is uploaded from the build.
- A **second entrypoint in the same image** runs migrations: `python -m app.db.migrate`, overridden at run time. Same image, three roles: web, migrate, local dev.

## 4. Required application changes

These are prerequisites, not nice-to-haves. The deployment is not correct without them. All are in scope for this project.

### 4.1 Give the checkpointer a connection pool

`main.py:55` calls `AsyncPostgresSaver.from_conn_string(...)`, which opens exactly **one** `psycopg.AsyncConnection` guarded by a single `asyncio.Lock`, held for the process lifetime, with no reconnect. If that connection is poisoned, every chat in the process fails forever while `GET /health` still returns `ok`.

`AsyncPostgresSaver.__init__` explicitly accepts an `AsyncConnectionPool` (it only rejects pool + pipeline together):

```python
AsyncPostgresSaver(conn=AsyncConnectionPool(
    conninfo=url,
    min_size=0, max_size=1,
    kwargs={"prepare_threshold": None, "autocommit": True},
))
```

`prepare_threshold=None` is load-bearing. `from_conn_string` passes `prepare_threshold=0`, which means *prepare on first execution* — so the saver prepares every statement, making it **incompatible with any transaction-mode pooler**, including Neon's pooled endpoint, PgBouncer transaction mode, and Supabase's Supavisor transaction port. Passing `None` disables prepared statements and unlocks the pooled endpoint.

This change also dissolves the single-`asyncio.Lock` bottleneck and gives reconnect-on-failure.

### 4.2 Replace `request.url_for()` with `PUBLIC_BASE_URL`

`auth/routes.py:21-22`:

```python
def _redirect_uri(request: Request) -> str:
    return str(request.url_for("auth_callback"))
```

Behind CloudFront → Function URL this yields the **`*.lambda-url.on.aws` domain**, because CloudFront must not forward the viewer Host (§2.2). Google then rejects the redirect as unregistered, and login is dead.

Add a `PUBLIC_BASE_URL` setting and build the redirect URI from it. This is one line and it kills the hazard permanently, across every future hosting change.

Related, and easy to miss: `auth/routes.py:69` redirects to `settings.frontend_origin` after the OAuth callback. In a single-origin deployment that value must be the public base URL (or simply `/`), not a separate frontend host. `FRONTEND_ORIGIN` is also the CORS allowlist entry in `main.py`; single-origin makes CORS unnecessary but harmless.

### 4.3 Secure cookies

`auth/routes.py:32` (OAuth state) and `auth/routes.py:76` (session) both pass `secure=False`. Both become `True`. `localhost` is a secure context, so local dev keeps working without a flag.

`samesite="lax"` is correct **only because** everything is on one origin. If the SPA and API are ever split onto different domains this must become `SameSite=None; Secure`, and Safari's ITP plus third-party-cookie deprecation will break login for a share of users with no error message. Do not split the origins.

### 4.4 Move migrations out of the lifespan

`main.py` runs `run_migrations()` inside the FastAPI lifespan. On Lambda, "startup" happens per cold start per concurrent execution, so every cold start would race. Three concrete failures, all verified against the installed `langgraph-checkpoint-postgres==3.1.2`:

1. Concurrent `CREATE TABLE IF NOT EXISTS` collides in the Postgres catalog: `duplicate key value violates unique constraint "pg_type_typname_nsp_index"`.
2. LangGraph's `checkpoint_migrations` has an `INTEGER PRIMARY KEY`. Two processes both read `-1`, both apply, the loser gets a `UniqueViolation` — and it does not retry, it dies.
3. `MIGRATIONS[6..8]` in `langgraph/checkpoint/postgres/base.py` are `CREATE INDEX CONCURRENTLY IF NOT EXISTS`. Two concurrent builds can deadlock, and an aborted build leaves a permanently **INVALID** index that `IF NOT EXISTS` skips forever. Silent, permanent, and you find out months later as a slow query.

Fix: `db/migrate.py` becomes a module entrypoint run as a **CI deploy step** against Neon (which is publicly reachable, so no bastion is needed), wrapped in a session-level advisory lock held across both steps on one connection:

```python
conn.execute("SET lock_timeout = '30s'")
conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
conn.execute(SCHEMA_PATH.read_text())
with PostgresSaver.from_conn_string(url) as cp:
    cp.setup()
conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
```

Use blocking `pg_advisory_lock`, not `try_lock` — a late starter must wait, not proceed against a half-migrated schema. Keep the lock even though CI is single-threaded: local `./up`, a recreated dev DB, and a hand-run container all still race.

### 4.5 Split `/health` and `/ready`, and add an SSE heartbeat

`/health` must **not** touch the database. The Lambda Web Adapter polls it as a readiness check, as would any future ECS health check, and a DB blip would kill the container. Add `/ready` that does touch the DB and does a saver round-trip — which is also the correct fix for the invisibility half of §4.1.

Separately, emit an SSE comment heartbeat (`: ping\n\n`) every 10–15 s on `/agent`. CloudFront's origin timeout is inter-packet, so the heartbeat is what makes a long turn safe. Set `Cache-Control: no-cache, no-store, no-transform` and `X-Accel-Buffering: no` on the stream (the latter for client-side corporate proxies, not for AWS).

### 4.6 Make the connection pool size configurable

`db/engine.py:6-10` constructs `ConnectionPool` without `min_size`, so it defaults to **4**. On Lambda, connections multiply by concurrency: 10 reserved × (4 + 1 saver) = 50 connections against a free-tier Neon compute. Make `min_size`/`max_size` configurable, defaulting to `1`/`4`, and set `DB_POOL_MAX_SIZE=1` on Lambda.

**`min_size` must stay at least 1.** `open_pool` calls `pool.wait()`, which blocks until `min_size` connections are established — that is the mechanism behind the existing `test_startup_fails_loudly_when_postgres_is_unavailable` guarantee. With `min_size=0` the wait returns immediately and an unreachable database would no longer abort startup, silently converting a loud failure into a broken deployment. Bound Lambda with `max_size` instead, giving 2 connections per execution environment (1 pool + 1 checkpointer).

### 4.7 Load secrets from SSM at init

Add an SSM-backed config path so the eight secrets never enter Terraform state. One batched `GetParameters` call at module init (~100–200 ms, once per cold start), IAM-scoped to `/mem0-chatbot/prod/*`. `config.py` continues to read from a mapping, so tests are unaffected.

### 4.8 Build the SPA with an empty API base

`frontend/src/api.ts:1` is `import.meta.env.VITE_API_BASE ?? "http://localhost:8000"`. `??` only falls back on null/undefined, so `VITE_API_BASE=""` survives correctly and requests become same-origin (`/auth/login`, `/agent`).

Do not bake a backend hostname into the bundle. If a bundle ships containing `https://xyz.lambda-url.on.aws`, every future backend move forces a coordinated frontend rebuild and redeploy.

### 4.9 Mount the built SPA in the app

`main.py` currently serves no static files. For the image to be genuinely portable (§3) it needs a `StaticFiles` mount at `/` serving `/app/static`, with an SPA fallback to `index.html` for unknown paths, registered **after** the API routers so it never shadows them.

In the deployed serverless design this mount is unused — CloudFront serves the SPA from S3. It exists so that local `docker run` and any future box or container deployment are single-origin without a separate web server. Gate the mount on the directory existing, so a dev checkout without a build still starts.

## 5. Data flow

**A chat turn.** Browser POSTs `/agent` to CloudFront → CloudFront forwards to the Function URL (viewer Host stripped, everything else forwarded) → Lambda cold-starts if needed → LWA proxies to uvicorn → `AgentAuthMiddleware` resolves the session cookie against Neon → LangGraph runs, streaming AG-UI events → chunked response flows back through CloudFront to the browser. Checkpoints are written to Neon per turn.

**A login.** Browser hits `/auth/login` → app builds the Google authorization URL from `PUBLIC_BASE_URL` → Google redirects to `{PUBLIC_BASE_URL}/auth/callback` → app exchanges the code, upserts the user, mints a signed session cookie with `Secure`, `HttpOnly`, `SameSite=Lax` → redirects to `/`.

**A deploy.** CI builds the arm64 image → pushes to ECR → runs the migration entrypoint against Neon → updates the Lambda function to the new image **digest** → publishes a version → uploads the SPA to S3 → invalidates `/index.html` only.

## 6. Infrastructure layout

Flat root module (single environment), split by concern:

```
infra/
  versions.tf      providers, required_version, S3 backend (use_lockfile = true)
  variables.tf
  ecr.tf           repository + lifecycle policy (keep last 5 images)
  lambda.tf        function, function URL, log group with retention
  s3.tf            SPA bucket, OAC, bucket policy
  cloudfront.tf    distribution, behaviours, CloudFront Function for SPA routing
  ssm.tf           parameter definitions (names only; values seeded out of band)
  iam.tf           Lambda execution role, GitHub OIDC provider + roles
  observability.tf alarms, SNS topic, AWS Budgets
  outputs.tf       distribution domain, function URL, ECR repo
```

State in an encrypted S3 bucket with S3 native locking (`use_lockfile = true` — DynamoDB is no longer required).

## 7. CI/CD

GitHub Actions with OIDC into AWS; no long-lived keys. Two roles: `gha-plan` (read-only + state, `sub = repo:konica/practice-agent-memory:*`) and `gha-deploy` (write, `sub` pinned to `refs/heads/main`, never wildcarded), both asserting `aud = sts.amazonaws.com`.

1. **test** (PR + main) — `uv sync --all-extras && uv run pytest`; `npm ci && npm run lint && npm run build`; `terraform fmt -check`, `tflint`, `checkov`.
2. **build** (main) — OIDC assume, ECR login, `docker/build-push-action` with `platforms: linux/arm64` on an `ubuntu-24.04-arm` runner (native, not QEMU). Tag by `$GITHUB_SHA`. Trivy gate on HIGH/CRITICAL-with-fix.
3. **migrate** (main) — run the migration entrypoint against Neon. Fails the pipeline before any traffic sees a new schema.
4. **deploy** (main, GitHub Environment `production`) — `aws lambda update-function-code --image-uri <digest>`, wait for `LastUpdateStatus=Successful`, publish a version. Then `aws s3 sync` the SPA and invalidate `/index.html` only (hashed assets never need invalidation; 1,000 free paths/month).

Order matters: API first, then SPA. The SPA is what calls the new API.

Infra `terraform apply` is a separate, human-gated workflow. App deploys should be push-button; infra deploys should not.

Rollback: `update-function-code` to the previous digest, or repoint the alias. Both are one command.

## 8. Cost

Verified against us-east-1 price-list offer files (version 2026-08-31).

Rates: Lambda arm64 $0.0000133334/GB-s, $0.20/1M requests, streaming $0.008/GiB beyond the first 6 MB per request. Free tier: 400,000 GB-s + 1M requests + 100 GB streamed per month.

| | Zero users | ~20 DAU | ~500 DAU |
|---|---|---|---|
| Lambda compute | $0 | $0 (~48k GB-s, under free tier) | ~$18.67 |
| Lambda streaming bytes | $0 | $0 | $0 (responses well under 6 MB) |
| CloudFront | ~$0 | ~$0 | $2–4 |
| S3 | $0.02 | $0.02 | $0.10 |
| ECR (~1.5 GB, 5 images) | ~$0.75 | ~$0.75 | ~$0.75 |
| Neon | $0 | $0 | $30–45 (Launch tier) |
| CloudWatch Logs | ~$0 | $0.50 | $3–8 |
| **Total** | **≈ $1** | **≈ $1.50** | **≈ $55–75** |

Traffic model: 20 DAU = 4 turns/user/day = 2,400 turns/month; 500 DAU = 6 turns/user/day = 90,000 turns/month; 10 s and 2048 MB per turn.

**Memory is free at low traffic, so spend it on cold start.** At 20 DAU, 2048 MB costs 48,000 GB-s against a 400,000 GB-s allowance. At 500 DAU the same choice costs ~$12/month more than 1024 MB — that is the point to reconsider, not before.

**Where "free" is marketing:**

- The **AWS Free Plan is a 6-month trial, not a free tier**: $100 in credits (up to $200 via onboarding activities), ending at 6 months or when credits run out, whichever comes first. The 12-month free tier was discontinued for accounts created on or after 2025-07-15. Anything built in this account is on a countdown.
- **CloudFront's $0/month flat-rate plan is unavailable to accounts on the Free Tier**, so you pay pay-as-you-go CloudFront during exactly the period you are cheapest.
- Lambda's 1M requests / 400,000 GB-s is still published without expiry but no longer appears among the featured always-free services. **Verify it in the Billing console's Free Tier page.** It does not change the recommendation — 48,000 GB-s is well under $1 either way.
- **Neon's 0.5 GB is the real ceiling, not the 100 CU-hours** (§9.5).

## 9. Hazards and mitigations

### 9.1 Cold start — 8–20 s p99 to first byte

The largest UX risk and the main cost of this design. Importing `langchain_openai` + `langgraph` plus lifespan setup dominates.

Mitigations, in order:

- 2048 MB memory (CPU scales with it) — already specified.
- Keep heavy imports lazy. `agent/memory.py` already does this well: `build_client` imports `mem0` *inside* the function, so mem0's transitive weight (qdrant, grpc, numpy, sqlalchemy) lands on the first memory call rather than on cold start. Preserve that property; audit `main.py`'s module-level imports for the same treatment.
- Migrations and pool warm-up leave the lifespan (§4.4, §4.6), removing DB round-trips from init.
- Log `AWS_LAMBDA_INITIALIZATION_TYPE` so cold starts are measurable rather than assumed.

**SnapStart cannot help.** It does not support container images, and the zip route caps at 250 MB unzipped against a measured 234 MB of site-packages — pruning to fit is its own project, and a warm SnapStart cache costs $3.95/month, which defeats the purpose.

Provisioned concurrency would eliminate cold starts and also the $0 bill. If cold starts prove unacceptable, that is the signal to move to Lightsail or Fargate, not to buy provisioned concurrency.

### 9.2 Billed for the full streaming duration

Streamed responses are **not** stopped when the client disconnects, and you are billed for the whole invocation. Mitigations: 120 s timeout, reserved concurrency of 10, and a client that treats a truncated stream as resumable — it can, since the whole conversation is checkpointed by `thread_id`.

### 9.3 The Function URL is publicly reachable

`AuthType` must be `NONE`. Locking a Function URL to CloudFront with OAC requires the **browser** to compute and send `x-amz-content-sha256` on every POST, which the AG-UI client will never do. So the `*.lambda-url.on.aws` host is a second, unauthenticated entry point that bypasses CloudFront.

Mitigation: CloudFront injects a secret custom origin header that middleware validates, rejecting anything else. This is security by obscurity and should be labelled as such — it raises the bar, it does not close the door. Application auth still gates every route that matters.

### 9.4 Never destroy and recreate the CloudFront distribution

The distribution's `*.cloudfront.net` domain is the OAuth redirect host and the cookie domain. Recreating it issues a new domain, which breaks login until the redirect URI is re-registered in Google's console by hand. Add `lifecycle { prevent_destroy = true }`.

### 9.5 Neon's 0.5 GB storage ceiling

LangGraph writes a full checkpoint JSONB plus blobs **on every turn**. `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` grow far faster than the application tables and will reach 0.5 GB well before the app feels large.

This needs a pruning story from day one, not later: delete checkpoint rows by `thread_id` when a conversation is archived, and/or retain only the last N checkpoints per thread. Track storage as a first-class metric.

### 9.6 The OAuth console is not IaC

Every redirect URI must be registered by hand in the Google Cloud console. It is the one step Terraform cannot perform, and it is what will break a future environment. Document the exact URI in the runbook.

### 9.7 Reduced quotas on new accounts

New AWS accounts have reduced Lambda concurrency and memory quotas. Request an increase before showing the app to anyone.

### 9.8 Synchronous DB calls in async paths

`guard_agent_request` and `get_current_user` call the synchronous `pool.connection()` from inside async request paths, blocking the event loop. On Lambda this is harmless — one invocation per execution environment — but it is a throughput ceiling on any long-lived-process design. Worth knowing before a future migration; not worth fixing now.

## 10. Open decision — custom domain

ACM **cannot** issue a certificate for a `duckdns.org` name. DuckDNS's API sets only A/AAAA records and a single TXT record shared across sub-subdomains; ACM DNS validation requires a CNAME at a randomly-named label, and email validation would go to `admin@duckdns.org`, which is not yours. CloudFront requires an ACM certificate in us-east-1 for any custom domain.

Options:

1. **Ship on the default `*.cloudfront.net` domain** (recommended for launch). Valid TLS, zero cert work. `cloudfront.net` is on the Public Suffix List, so Google OAuth accepts it and cookies scope correctly. Subject to §9.4.
2. **Import a Let's Encrypt certificate into ACM.** LE's DNS-01 uses a fixed TXT name, which is the one thing DuckDNS supports. Imported certs never auto-renew, so this needs a scheduled job re-importing every ~60 days plus an ACM `DaysToExpiry` alarm. If that job breaks silently, the site goes down on day 90.
3. **Register a real domain** (~$14/yr in Route 53). ACM issues and auto-renews with no ops. Removes the failure mode entirely for about 1% of the annual bill.

Note that in the *box* designs this decision disappears — Caddy's DuckDNS DNS-01 plugin renews Let's Encrypt certs automatically forever with no ACM involvement. That advantage does not transfer to CloudFront.

## 11. Observability

- **Logs:** CloudWatch Logs with **explicit 14-day retention** (the default is Never Expire). JSON-formatted Python logging so Logs Insights can query fields.
- **Metrics:** Lambda `Errors`, `Throttles`, `Duration` p99, `ConcurrentExecutions`; CloudFront `5xxErrorRate`.
- **Traces:** skip X-Ray. LangSmith already covers the only interesting latency, and X-Ray would report that the CloudFront hop took 2 ms.

Alarm on:

1. Lambda `Errors` ≥ 5 in 5 minutes.
2. Lambda `Throttles` ≥ 1 — means reserved concurrency is binding and users are being turned away.
3. Lambda `Duration` p99 approaching the 120 s timeout.
4. CloudFront `5xxErrorRate` > 1%.
5. **AWS Budgets at 50% and 80% of remaining credits.** With a 6-month credit clock on a new account, this is the alarm that matters most.

Track as a metric, not an alarm: Neon storage against the 0.5 GB ceiling (§9.5).

## 12. Out of scope

Deliberately excluded as over-engineering at this stage: WAF, X-Ray, provisioned concurrency, a staging environment, multi-region, RDS Proxy, VPC endpoints, blue/green deployment, and any migration of OpenAI to Bedrock.

## 13. Migration path

The signal to leave this design is any of:

- Cold-start-affected requests exceed ~5% of sessions.
- Lambda compute exceeds ~$25/month — at which point Lambda Managed Instances or Fargate is cheaper.
- Neon storage approaches 0.4 GB, or CU-hours approach 80/month.
- The AWS Free Plan's 6-month window closes.

Because the image is portable (§3), the first move is a deployment change: the same image runs on **Lambda Managed Instances** (EC2 pricing + 15%, multi-concurrency, no cold starts) by changing a compute-type setting, or on Lightsail/Fargate by pointing a different runtime at it. Moving to ECS + ALB additionally means redoing the front door, DNS, and the Google OAuth registration.
