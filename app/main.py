from datetime import datetime, timezone

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from . import cache, db
from .moderation import (
    RATE_LIMIT,
    RATE_WINDOW,
    ModerationError,
    check_rate,
    device_bucket,
    find_blocked,
    moderate,
    reports_needed,
    status_after_report,
    window_start,
)

app = FastAPI(title="confessions-board")

VOTE_MEMORY = 86400  # how long the "you already voted" marker lives


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def _salt():
    """Rotates daily. Yesterday's device handles do not match today's."""
    return "confess-" + datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bucket(device_id):
    try:
        return device_bucket(device_id, _salt())
    except ModerationError as e:
        raise HTTPException(400, str(e))


def _score(post_id, db_score):
    """Live count from Redis, lazily warmed from Postgres on a cold cache."""
    c = cache.client()
    key = f"votes:{post_id}"
    raw = c.get(key)
    if raw is None:
        c.set(key, int(db_score))
        return int(db_score)
    return int(raw)


@app.post("/posts", status_code=201)
def create_post(payload: dict = Body(...), x_device_id: str = Header(None)):
    bucket = _bucket(x_device_id)
    # Validate before claiming a slot, so a typo does not cost somebody one
    # of their five posts.
    try:
        verdict = moderate(payload.get("body"))
    except ModerationError as e:
        raise HTTPException(400, str(e))

    now = int(datetime.now(timezone.utc).timestamp())
    key = f"rl:{bucket}:{window_start(now, RATE_WINDOW)}"
    c = cache.client()
    # INCR first, then judge the value it returned. Reading the counter and
    # incrementing it later is two round trips with a gap in the middle, and
    # a device firing twenty requests at once slips the whole burst through
    # that gap. INCR is atomic, so every racing request gets its own number.
    n = c.incr(key)
    if n == 1:
        c.expire(key, RATE_WINDOW + 60)
    verdict_rate = check_rate(n - 1, now, RATE_LIMIT, RATE_WINDOW)
    if not verdict_rate["allowed"]:
        raise HTTPException(429, f"slow down - try again in {verdict_rate['retry_after']}s")

    row = db.one("INSERT INTO posts (body, status) VALUES (%s,%s)"
                 " RETURNING id, body, status, score, reports, created_at",
                 (verdict["body"], verdict["status"]))
    if verdict["status"] == "held":
        db.query("INSERT INTO moderation_log (post_id, action, reason)"
                 " VALUES (%s,'held',%s)",
                 (row["id"], "word filter: " + ", ".join(verdict["matched"])), fetch=False)
    cache.client().set(f"votes:{row['id']}", 0)
    return {**row, "matched": verdict["matched"], "remaining_today": verdict_rate["remaining"]}


@app.get("/posts")
def list_posts(limit: int = 20):
    rows = db.query("SELECT id, body, score, reports, created_at FROM posts"
                    " WHERE status='published' ORDER BY created_at DESC LIMIT %s",
                    (max(1, min(100, limit)),))
    out = []
    for r in rows:
        out.append({**r, "score": _score(r["id"], r["score"]),
                    "comments": db.one("SELECT count(*) AS n FROM comments WHERE post_id=%s",
                                       (r["id"],))["n"]})
    out.sort(key=lambda p: (-p["score"], p["id"]))
    return {"posts": out}


@app.post("/posts/{post_id}/vote")
def vote(post_id: int, payload: dict = Body(...), x_device_id: str = Header(None)):
    bucket = _bucket(x_device_id)
    direction = str(payload.get("direction", "up")).lower()
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    post = db.one("SELECT id, status FROM posts WHERE id=%s", (post_id,))
    if not post:
        raise HTTPException(404, "no such post")
    if post["status"] != "published":
        raise HTTPException(409, "that post is not published")

    # SET NX is the whole trick: the marker proves a vote happened without
    # ever recording who. It expires, so the key store does not grow.
    if not cache.client().set(f"voted:{post_id}:{bucket}", direction,
                              nx=True, ex=VOTE_MEMORY):
        raise HTTPException(409, "this device already voted on that post")

    delta = 1 if direction == "up" else -1
    row = db.one("UPDATE posts SET score = score + %s WHERE id=%s RETURNING id, score",
                 (delta, post_id))
    cache.client().set(f"votes:{post_id}", int(row["score"]))
    return {"post_id": post_id, "direction": direction, "score": int(row["score"])}


@app.post("/posts/{post_id}/report")
def report(post_id: int, payload: dict = Body(None), x_device_id: str = Header(None)):
    bucket = _bucket(x_device_id)
    post = db.one("SELECT id, status, score, reports FROM posts WHERE id=%s", (post_id,))
    if not post:
        raise HTTPException(404, "no such post")
    if not cache.client().set(f"reported:{post_id}:{bucket}", "1", nx=True, ex=VOTE_MEMORY):
        raise HTTPException(409, "this device already reported that post")

    row = db.one("UPDATE posts SET reports = reports + 1 WHERE id=%s"
                 " RETURNING id, status, score, reports", (post_id,))
    new_status = status_after_report(row["status"], row["reports"], row["score"])
    if new_status != row["status"]:
        db.query("UPDATE posts SET status=%s WHERE id=%s", (new_status, post_id), fetch=False)
        db.query("INSERT INTO moderation_log (post_id, action, reason) VALUES (%s,'held',%s)",
                 (post_id, f"pulled by {row['reports']} reports"), fetch=False)
    return {"post_id": post_id, "reports": row["reports"],
            "needed": reports_needed(row["score"]),
            "status": new_status,
            "back_in_queue": new_status != row["status"]}


@app.get("/moderation/queue")
def queue():
    rows = db.query("SELECT id, body, score, reports, created_at FROM posts"
                    " WHERE status='held' ORDER BY reports DESC, id")
    return {"held": [{**r, "matched": find_blocked(r["body"]),
                      "needed": reports_needed(r["score"])} for r in rows]}


@app.post("/moderation/{post_id}")
def decide(post_id: int, payload: dict = Body(...)):
    action = str(payload.get("action", "")).lower()
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    post = db.one("SELECT id, status FROM posts WHERE id=%s", (post_id,))
    if not post:
        raise HTTPException(404, "no such post")
    if post["status"] != "held":
        raise HTTPException(409, "that post is not awaiting moderation")
    status = "published" if action == "approve" else "rejected"
    db.query("UPDATE posts SET status=%s, reports=0 WHERE id=%s", (status, post_id), fetch=False)
    db.query("INSERT INTO moderation_log (post_id, action, reason) VALUES (%s,%s,'moderator')",
             (post_id, action), fetch=False)
    return {"post_id": post_id, "status": status}


@app.post("/posts/{post_id}/comments", status_code=201)
def comment(post_id: int, payload: dict = Body(...), x_device_id: str = Header(None)):
    _bucket(x_device_id)
    post = db.one("SELECT id, status FROM posts WHERE id=%s", (post_id,))
    if not post:
        raise HTTPException(404, "no such post")
    if post["status"] != "published":
        raise HTTPException(409, "that post is not open for comments")
    try:
        verdict = moderate(payload.get("body"))
    except ModerationError as e:
        raise HTTPException(400, str(e))
    if verdict["status"] == "held":
        raise HTTPException(400, "comment tripped the word filter: "
                                 + ", ".join(verdict["matched"]))
    row = db.one("INSERT INTO comments (post_id, body) VALUES (%s,%s)"
                 " RETURNING id, post_id, body, created_at", (post_id, verdict["body"]))
    return row


@app.get("/posts/{post_id}")
def get_post(post_id: int):
    post = db.one("SELECT id, body, status, score, reports, created_at FROM posts"
                  " WHERE id=%s", (post_id,))
    if not post:
        raise HTTPException(404, "no such post")
    # Held means held. A post waiting on a moderator is not readable by url
    # either - otherwise holding it achieved nothing. Moderators read the
    # queue instead.
    if post["status"] != "published":
        raise HTTPException(404, "no such post")
    comments = db.query("SELECT id, body, created_at FROM comments WHERE post_id=%s"
                        " ORDER BY id", (post_id,))
    return {**post, "score": _score(post_id, post["score"]),
            "needed_to_remoderate": reports_needed(post["score"]),
            "comments": comments}
