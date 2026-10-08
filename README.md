

# Implementation Notes

## Architecture

The stack has FastAPI, PostgreSQL, Redis, and a one-shot migration service.

- FastAPI listens on `0.0.0.0:8080`.
- PostgreSQL stores posts, comments, scores, reports, and moderation logs.
- Redis stores short-lived salted device buckets, rate-limit counters, vote/report markers, and live vote counts.
- The migration service applies every SQL file in filename order before FastAPI starts.

## Docker decisions

`Dockerfile` uses the pinned `python:3.12.7-slim-bookworm` image. Dependencies are copied and installed before application source, so Docker can reuse the dependency layer when source code changes.

The application runs as the non-root `appuser`. Port `8080` is exposed and Uvicorn is bound to `0.0.0.0`.

## Compose decisions

Inside Compose, the application reaches dependencies using service names:

```text
DATABASE_URL=postgresql://confessions:confessions@postgres:5432/confessions
REDIS_URL=redis://redis:6379/0
```

It does **not** use `localhost`. Postgres and Redis have health checks, and the migration service waits for Postgres. FastAPI waits for the migration service to complete successfully.

## Testing

`tests/unit/test_moderation.py` contains database-free and network-free tests for the pure moderation functions.

The word filter tests all required disguises:

```text
stupid
STUPID
stup1d
$tupid
stuuupid
s.t.u.p.i.d
```

It also confirms that `classmate`, `assignment`, `analysis`, and `dumbbell` pass. `dumbbell` should pass because the implementation matches complete candidate words rather than arbitrary substrings.

Report thresholds are tested around the requested boundaries:

```text
9  -> 3 reports
10 -> 4 reports
11 -> 4 reports
19 -> 4 reports
20 -> 5 reports
21 -> 5 reports
```

The rate limiter is tested at the first allowed request, last allowed request, first rejected request, and both ends of the retry window. Device buckets are checked for stability with the same salt and change with a different salt.

The current unit suite passes with approximately 95% coverage of `app.moderation`, above the required 70%.

`tests/integration/test_api.py` uses real Postgres and Redis. It verifies health, seeded data, creation, voting and duplicate-vote protection, moderation approval, and report-based re-moderation.

## CircleCI

The pipeline is:

```text
lint → unit tests → integration tests → secret scan → image build
```

The integration job uses CircleCI service containers. In CircleCI, these services are reached through `localhost`, which is intentionally different from Docker Compose.

The image build is performed only after the tests and secret scan succeed.

## Anonymous but not unaccountable

The privacy model separates persistent content from short-lived abuse controls.

Postgres stores no author, device ID, IP address, or session ID. A request's device identifier is converted into a short HMAC-SHA256 digest using a server-side daily salt. Only the derived bucket is used for Redis controls; the original identifier is never persisted.

The daily salt rotation prevents one device from retaining the same application-level handle indefinitely. The trade-off is that a device can receive a new bucket after rotation, so perfect long-term rate limiting is intentionally sacrificed to avoid persistent tracking.

Redis keys expire automatically. Rate-limit counters expire with their window, vote/report markers expire after their configured period, and live vote counts are a cache that can be rebuilt from Postgres.

This provides practical abuse resistance without making the database an identity store.

## Fixed-window trade-off

The rate limiter deliberately uses a fixed window. Its known weakness is the boundary burst: a device can use all `RATE_LIMIT` requests at the end of one window and all `RATE_LIMIT` requests at the beginning of the next.

Therefore the theoretical short-period burst is:

```text
2 × RATE_LIMIT
```

A sliding-window or token-bucket limiter would reduce this behavior but would require additional state and complexity. The implementation documents this limitation instead of hiding it.

## Run locally

```bash
docker compose up --build
```

Then:

```bash
curl http://localhost:8080/health
```

Expected:

```json
{"status":"ok","postgres":true,"redis":true}
```

Stop with:

```bash
docker compose down
```

## Docker Hub

After creating/logging into your own Docker Hub account:

```bash
docker login
docker build -t YOUR_DOCKERHUB_USERNAME/campus-confessions:1.0 .
docker push YOUR_DOCKERHUB_USERNAME/campus-confessions:1.0
```

The required release tag is `:1.0`, and the image must be under your own Docker Hub account.
