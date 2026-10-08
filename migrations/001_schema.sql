-- Note what is NOT here: no author, no device id, no IP, no session.
-- A row in `posts` cannot be traced to a person, by us or by anyone who
-- steals the database. Everything that limits abuse lives in Redis, salted
-- and short-lived.
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('published', 'held', 'rejected')),
    score INT NOT NULL DEFAULT 0,
    reports INT NOT NULL DEFAULT 0 CHECK (reports >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS moderation_log (
    id SERIAL PRIMARY KEY,
    post_id INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE INDEX IF NOT EXISTS posts_status ON posts (status, created_at DESC);
CREATE INDEX IF NOT EXISTS comments_post ON comments (post_id, id);
