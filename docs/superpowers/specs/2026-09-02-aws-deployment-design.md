# Deploying the mem0 chatbot to AWS

**Date:** 2026-09-02
**Status:** Approved design, ready for implementation planning
**Scope:** Take `use_mem0/` from a local `make up` dev stack to a personal demo running on a public HTTPS URL in AWS.

## Purpose and scope

The chatbot works end to end locally. It has never been deployed. There is no Dockerfile,
no CI workflow, and no infrastructure code anywhere in the repository.

This deployment is a **personal demo**: the owner plus a handful of people he shares the
link with. That is the confirmed scope, and it sets the budget. Single AZ. No autoscaling.
No dev/staging/prod pipeline. No disaster recovery beyond backups. Target cost is under
$40/month; the design lands at roughly $13.

Data durability is nevertheless in scope. Postgres holds every conversation transcript in
LangGraph's checkpoint tables. A chatbot that remembers you is the entire point of the
project, so losing that database loses the demo itself.

## Decisions

### No custom domain

The owner does not own a domain and does not want to buy one, so the design uses
AWS-provided hostnames only.

This is viable, and it was verified rather than assumed. Google accepts
`https://<id>.cloudfront.net/auth/callback` as an OAuth redirect URI. Google's rule is that
"Host TLDs must belong to the public suffix list"; `cloudfront.net` is a plain PSL entry,
which is precisely what makes a distribution subdomain a valid top private domain. The same
mechanism is why `*.herokuapp.com` and `*.vercel.app` work. By contrast a raw EC2 hostname
(`ec2-1-2-3-4.compute-1.amazonaws.com`) is rejected, because it matches the PSL wildcard
exactly and leaves no label to form a private domain.

Two consequences follow.

**An Application Load Balancer is impossible.** ACM will not issue a certificate for
`*.elb.amazonaws.com`, so a load balancer's default hostname can only serve HTTP, and Google
rejects non-localhost HTTP redirect URIs. This eliminates the textbook "ALB in front of
Fargate" architecture outright, which is why it does not appear among the options below.

**One accepted residual risk.** Google's OAuth policy asks callers to use redirect URIs on
"domains that you own, that you have been authorized to use, or that you have been explicitly
given license to use." Nothing enforces this at registration time, and no evidence was found
of enforcement against low-scope hobby applications. Registering a domain (~$12/year) would
remove the clause entirely. The risk is accepted for a demo and recorded here so the decision
is deliberate.

Keeping the Google OAuth app in **Testing** publishing status avoids domain-ownership
verification, which cannot be satisfied for a domain the owner does not control.

### Architecture: one box behind CloudFront

```
Browser
  |  HTTPS
  v
CloudFront  https://<id>.cloudfront.net        TLS terminates here
  |  HTTP + X-Origin-Secret header
  v
Lightsail instance, 2 GB bundle ($12/mo, static IPv4 and 3 TB transfer included)
  |
  +-- app container      FastAPI + uvicorn, serving the built frontend from dist/
  +-- postgres:16        bind-mounted volume
                         ^ same box: no load balancer, no NAT,
                           nothing reaping idle connections
```

Two alternatives were costed and rejected.

**Lightsail container service plus Lightsail managed Postgres (~$25/mo)** buys managed
database backups and removes OS patching. It costs roughly double for that one benefit, its
secrets sit readable in the deployment JSON, the $15 database bundle offers no encryption at
rest, there is no shell access for debugging, and SSE streaming through that endpoint is
undocumented and would need smoke-testing before commitment.

**ECS Express Mode plus RDS (~$50/mo)** is the most production-shaped option and the most
SSE-proven transport, with proper SSM-backed secrets and canary deploys. It is roughly four
times the cost and exceeds the stated budget, with an ALB floor of about $16/month buying
load balancing that five users do not need. Note for any future revisit: Express Mode
defaults to 1 vCPU / 2 GB, about $71/month unless `--cpu 0.5 --memory 1` is passed
explicitly, and the task must stay in a **public** subnet, since moving it to a private one
requires a NAT Gateway at $32.85/month.

**Serverless (Lambda) is rejected**, and the reasoning is recorded because it is the option
most likely to be proposed again later. Three independent failures:

- The graph holds a single `AsyncPostgresSaver` connection with no reconnect logic, opened in
  the FastAPI lifespan and held for the process lifetime. Lambda freezes the execution
  environment between invocations, so nothing keeps that socket alive; it is reaped, and the
  code has no path back. Every concurrent environment also opens its own connection plus its
  own `ConnectionPool`.
- `POST /agent` streams SSE for 30 seconds or more. API Gateway buffers the entire response
  body and enforces a 30-second integration timeout, so the stream fails rather than degrades.
  Lambda Function URL streaming is exposed through a Node.js wrapper with no first-class
  Python support.
- Every cold start re-runs SQL migrations and `PostgresSaver.setup()` and aborts hard if
  Postgres is unreachable, which means concurrent cold environments racing on the same DDL.

It does not win on cost either, since the database cannot scale to zero regardless.

The decisive argument for the chosen architecture is not price. The application's most
fragile property is that single un-reconnectable checkpointer connection, and every
alternative inserts a load balancer, a NAT, or a managed-database network boundary between
the application and Postgres — each a component whose job includes reaping idle connections.
Co-locating them removes a failure mode that cannot otherwise be fixed without patching
`langgraph-checkpoint-postgres`.

### Single origin

The frontend and API are served from **one origin**. The Vite bundle is baked into the
backend image and mounted with `StaticFiles(html=True)`; FastAPI's router matches `/auth/*`,
`/agent`, `/conversations` and `/health` first, and everything else falls through to
`index.html`.

This is a design decision, not an optimisation. On two origins the session cookie becomes
cross-site and needs `SameSite=None`, which Safari blocks outright and Chrome restricts — a
two-origin demo can simply fail to sign in. Serving one origin makes the cookie first-party
and deletes the CORS constraint rather than configuring around it.

### Access control

Anyone with a Google account can currently sign in. On a public URL that means strangers
spend the owner's OpenAI credits and mem0 quota, and their conversations land in his
database. Access is restricted by an `ALLOWED_EMAILS` allowlist enforced in the application.

## Application changes

All paths are relative to `use_mem0/`.

### 1. `PUBLIC_BASE_URL` replaces request-derived URLs

`backend/src/app/auth/routes.py:_redirect_uri()` currently returns
`str(request.url_for("auth_callback"))`, deriving the public URL from the incoming request.

**This breaks sign-in behind CloudFront.** The request reaches the origin as plain HTTP
carrying the origin's own Host header, so `url_for` produces
`http://<origin-host>/auth/callback`. Google rejects it with `redirect_uri_mismatch`, and the
same wrong value is sent in the token exchange, where it must match exactly.

Depending on `X-Forwarded-*` headers to correct this is fragile: it requires the CloudFront
origin request policy and uvicorn's `--forwarded-allow-ips` to agree, and a misconfiguration
fails silently. Make the public URL explicit configuration instead.

- Add `PUBLIC_BASE_URL` to `REQUIRED_KEYS` and `Settings` in `config.py`.
- `_redirect_uri()` returns `f"{settings.public_base_url}/auth/callback"`.
- `frontend_origin` derives from `PUBLIC_BASE_URL` rather than being set separately.
- The callback's post-login redirect targets the same origin.

The configured value is also the exact string registered in the Google console, so there is
one source of truth for the public URL.

### 2. Secure cookies

Set `secure=True` on all three `set_cookie` calls in `auth/routes.py` — the session cookie
and the `oauth_state` cookie — keeping `httponly=True` and `samesite="lax"`.

`Lax` is correct and must not be changed to `None`: the OAuth callback is a top-level GET
navigation, on which Lax cookies are sent. Do not set a `Domain` attribute; on a Public
Suffix List host it would be silently rejected.

### 3. Email allowlist

Add `ALLOWED_EMAILS` (comma-separated) to configuration. In `/auth/callback`, immediately
after `exchange_code` returns and **before** the `users` upsert, reject an identity whose
email is not on the list with HTTP 403.

Checking before the upsert means a rejected stranger never gets a row in the database.
Comparison is case-insensitive on the whole address. An empty or unset `ALLOWED_EMAILS`
must **fail closed** (reject everyone) rather than admit everyone, so that a misconfigured
deployment cannot silently become open.

### 4. Serve the frontend from the backend

Mount the built bundle in `create_app()` after all routers are included:
`app.mount("/", StaticFiles(directory=<dist>, html=True))`. Mount order matters — the API
routers must be registered first. The directory is configurable so tests and local
development do not require a built bundle to exist.

### 5. `/health` checks the database

`/health` currently returns a static `{"status": "ok"}`. Change it to execute `SELECT 1`
through `app.state.pool` and return 503 when that fails.

Without this, the failure described in the risks section below is invisible: the process
stays up, the health check keeps returning 200, and every `POST /agent` fails.

### 6. Origin shared-secret header

The Lightsail instance has a public IP, so the origin is reachable directly, bypassing
CloudFront and its TLS. CloudFront injects a custom header (`X-Origin-Secret`); ASGI
middleware rejects any request that does not carry the expected value.

Lightsail's firewall cannot reference CloudFront's managed prefix list — it accepts CIDRs
only — so a header check is the available mitigation. The residual risk is recorded below.

### 7. Multi-stage Dockerfile

A node stage runs `npm run build`; a python stage installs backend dependencies with `uv`
from `pyproject.toml`/`uv.lock` and copies the built `dist/` in. One image, one process.

The image build must not depend on `use_mem0/npm-install` or `use_mem0/venv-path`. Those
exist to work around a host filesystem that refuses symlinks; inside a Linux container image
symlinks work normally and the standard `npm install` and `uv sync` are correct.

### 8. Production compose file

`docker-compose.prod.yml` defines the app and `postgres:16` with:

- `depends_on: {postgres: {condition: service_healthy}}` and a `pg_isready` healthcheck, so a
  reboot does not start the app against an unready database and abort;
- `restart: unless-stopped` on both services;
- TCP keepalives in `DATABASE_URL`
  (`?keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5`);
- a bind-mounted volume for Postgres data.

Existing unit tests already cover the modules touched by changes 1, 2, 3 and 5, so these
extend the suite rather than introducing a new testing approach.

## Infrastructure

Provisioned with Terraform, in a new `infra/` directory. Resource identifiers are read from
Terraform outputs at run time rather than hardcoded.

| Resource | Notes |
| --- | --- |
| Lightsail instance | 2 GB bundle. Not the $5/512 MB or $7/1 GB bundles: LangChain, LangGraph, psycopg and uvicorn together occupy roughly 350-500 MB RSS before serving a request, and Postgres shares the box. |
| Lightsail static IP | Attached to the instance; the CloudFront origin. |
| Lightsail firewall | Port 80 from anywhere (CloudFront reaches the origin over HTTP; there is no origin certificate, so 443 is not opened). SSH restricted to the owner's address. |
| CloudFront distribution | Two cache behaviours, see below. |
| ECR repository | Holds the built image. Lifecycle policy to expire untagged images. |
| S3 bucket | `pg_dump` destination. Versioned, lifecycle-expired, public access blocked. |
| IAM role / instance credentials | ECR pull and S3 write only. |
| CloudWatch billing alarm | One alarm at $30. The only monitoring in scope. |

### CloudFront configuration

Three settings are non-obvious, and each one silently breaks a different thing:

1. **The default cache behaviour allows only GET and HEAD.** `POST /agent` returns 403 until
   all HTTP methods are allowed on the API behaviour.
2. **The origin request policy must forward cookies.** Use `AllViewerExceptHostHeader`.
   Without cookie forwarding, authentication fails while everything else appears to work.
3. **The origin response timeout defaults to 30 seconds** and governs both time-to-first-byte
   and the gap between packets. Raise it to 120 (the quota maximum; a quota increase may be
   required, as the console may cap at 60).

Behaviours:

- **API** — path patterns `/agent`, `/auth/*`, `/conversations*`, `/health`:
  `CachingDisabled`, `AllViewerExceptHostHeader`, all methods, 120s origin timeout.
- **Default** — the static bundle, cached.

Use CloudFront **pay-as-you-go** pricing, not the flat-rate "Free plan". The $0 flat-rate
plan excludes custom caching and private origins, which this configuration requires.
Pay-as-you-go retains the Always Free tier of 1 TB egress and 10 M requests per month, which
comfortably covers a demo.

### Secrets

Eleven environment values: the eight existing `REQUIRED_KEYS` plus `PUBLIC_BASE_URL`,
`ALLOWED_EMAILS` and the origin shared secret. `FRONTEND_ORIGIN` is dropped, since it is now
derived from `PUBLIC_BASE_URL`. Not all eleven are secret — `LANGSMITH_PROJECT` and
`PUBLIC_BASE_URL` are not — but they travel together in one file.

Stored in a root-owned `/opt/app/.env` at mode `0600`, referenced by compose `env_file`.
Free SSM Parameter Store SecureString, pulled at boot by the container entrypoint, is an
acceptable alternative if AWS-managed storage is preferred.

Do **not** use Secrets Manager: at $0.40 per secret per month, these would cost roughly
$4/month — a third of the total infrastructure bill — for no benefit at this scale.

Terraform must not store secret values in state. They are placed on the instance out of band.

## CI

A GitHub Actions workflow (new; the repository has no `.github/` today) builds the
multi-stage image on push to `main` and pushes it to ECR tagged with the commit SHA, and
additionally as `latest`.

Building in CI rather than on the instance is deliberate: a 2 GB box running Postgres cannot
comfortably also run `npm install` and a Vite build, so building in place would mean stopping
the application to free memory on every deploy, turning a routine push into an outage.

Release stays manual and deliberate: a script on the instance runs
`docker compose pull && docker compose up -d`. Tagging by commit SHA means rollback is the
same command pinned to the previous tag.

## Backups

This architecture trades managed database backups for cost. That debt is paid here, cheaply,
with two independent mechanisms that fail in different ways:

- **Lightsail automatic snapshots** — daily, 7-day retention. Whole-disk and physical;
  restores the box. Roughly $0.50/month.
- **Nightly `pg_dump | gzip | aws s3 cp`** — logical and portable; restores the data
  anywhere, including into RDS should the deployment later outgrow this design. Roughly
  $0.05/month.

**A restore drill is part of the implementation, not a follow-up.** An untested backup is a
rumour. The drill restores the most recent dump into a scratch database and confirms
conversation transcripts are readable.

## Cutover

The ordering is forced by a circular dependency: both `PUBLIC_BASE_URL` and the Google
redirect URI need the CloudFront hostname, which does not exist until the distribution does.

1. `terraform apply` — creates the Lightsail instance, static IP, ECR, S3 bucket, IAM.
2. Create the CloudFront distribution pointing at the static IP; record the assigned
   `<id>.cloudfront.net` hostname.
3. Set `PUBLIC_BASE_URL=https://<id>.cloudfront.net`. Register
   `https://<id>.cloudfront.net/auth/callback` as an authorised redirect URI in the Google
   Cloud console. Keep the OAuth app in **Testing** status and add each person as a test
   user. Set `ALLOWED_EMAILS`.
4. Place `/opt/app/.env`, pull the image, start the compose stack.
5. Run the acceptance checks below.

The CloudFront hostname is **regenerated if the distribution is ever deleted and recreated**,
which breaks sign-in with `redirect_uri_mismatch` long after the cause has been forgotten.
This must be noted in the Terraform configuration next to the distribution resource.

## Acceptance criteria

Each check targets a specific identified risk rather than generic smoke testing.

1. Sign-in completes end to end through Google, and the session cookie is set with
   `Secure` and `HttpOnly`.
2. **A response long enough to exceed 30 seconds streams to completion.** This proves the
   CloudFront origin-timeout change. It is the check most likely to fail, and the failure
   mode is a silently truncated SSE stream rather than an error.
3. Reloading mid-conversation rehydrates the transcript.
4. A Google account not on `ALLOWED_EMAILS` is rejected with 403 and creates no `users` row.
5. Restarting the Postgres container makes `/health` report unhealthy.
6. The restore drill recovers a database from the nightly dump.
7. `POST /agent` succeeds through CloudFront, confirming all HTTP methods are allowed.
8. A request sent directly to the Lightsail public IP without the shared-secret header is
   rejected.

## Risks

**The checkpointer connection has no reconnect, and nothing notices when it dies.**
`AsyncPostgresSaver.from_conn_string(...).__aenter__()` opens one connection at startup and
holds it for the process lifetime. If Postgres restarts — container restart, reboot, OOM kill
— every subsequent `POST /agent` fails while the process stays up. Mitigated by the compose
health-check ordering, `restart: unless-stopped`, keepalives, and the `/health` change; a
proper fix requires reconnect logic in `langgraph-checkpoint-postgres` and is out of scope.

**CloudFront's origin timeout truncates SSE streams silently.** The default 30 seconds covers
both first-byte latency and inter-packet gaps. `/agent` performs a mem0 lookup and graph setup
before the model's first token, so first-byte latency is not small. When it trips, the browser
sees a truncated stream, not an error — the UI simply stops. Raising the timeout to 120s is
the mitigation; a periodic SSE heartbeat comment would eliminate the class of problem and is
worth considering if the AG-UI adapter permits it.

**CloudFront-to-origin traffic is plaintext HTTP over the public internet**, so the session
cookie traverses that hop unencrypted. This is inherent to terminating TLS at CloudFront with
no domain for an origin certificate. The shared-secret header prevents bypass but not
interception. Accepted as a named residual risk at this scope; it is the strongest argument
for buying a domain later.

**Public Suffix List cookie semantics.** `cloudfront.net` is a PSL entry. Cookies must be
host-only with no `Domain` attribute. Optionally the session cookie could take the `__Host-`
prefix for defence against cookie injection from other CloudFront sites; this was considered
and deferred as unnecessary churn for a demo.

**Single box, single AZ.** It is down when it is down, and a bad `docker compose up` takes the
demo with it. Accepted, and the reason rollback is a pinned image tag.

## Out of scope

NAT Gateway, ALB/NLB, RDS, Secrets Manager, custom VPC and private subnets, VPC endpoints,
flow logs, Multi-AZ, read replicas, Aurora, Route 53, WAF, CloudFront standard logging,
CloudWatch dashboards and alarms beyond the single billing alarm, autoscaling, blue/green
deployment, separate dev/staging/prod environments, and a disaster-recovery runbook.

Each was considered and cut as unnecessary for a personal demo. The NAT Gateway is worth
naming specifically: at $32.85/month it alone would exceed this design's entire cost.
