"""Pure moderation rules for an anonymous board. No database, no HTTP.

Three separate problems live here, and each is worth its own tests.

1. THE WORD FILTER. A blocked word is easy. A blocked word someone has tried
   to disguise is the actual job: `stup1d`, `$tupid`, `s.t.u.p.i.d`,
   `stuuupid`, `STUPID`. The filter must see through leetspeak substitution,
   inserted punctuation and stretched letters - and must NOT fire on innocent
   words that happen to contain a blocked one (the Scunthorpe problem).
   `classmate`, `assignment` and `analysis` must sail through.

2. THE REPORT RULE. A published post that enough people report goes back to
   the moderation queue. The threshold is not a flat number: a post the whole
   class upvoted needs more reports to be pulled than one nobody read, or
   three friends can silence anything.

3. THE RATE LIMIT. A fixed-window counter. Given how many posts a device has
   already made in the current window, decide whether the next one is allowed
   and when the window resets. Fixed windows have a known flaw - a device can
   post `limit` at the end of one window and `limit` at the start of the next.
   `burst_capacity()` states that flaw rather than hiding it.

Worth testing: every disguise form above; each innocent word; empty and
whitespace-only bodies; a body that is one long word; the exact report
boundary (threshold-1, threshold, threshold+1); a post with a big score
needing more reports; the first request in a window, the last allowed one,
the first rejected one; retry_after at the very start and very end of a
window; that `device_bucket` is stable for the same salt and different for
a different salt.
"""
import hashlib
import hmac
import re

# Deliberately mild. This is a university board, not a content-policy engine.
BLOCKED_WORDS = frozenset({
    "idiot", "stupid", "moron", "loser", "dumb", "trash",
    "pathetic", "scum", "shutup", "worthless",
})

# Characters people swap in to dodge a filter, and what they really are.
# "1" is ambiguous on purpose - it stands in for both "i" and "l". The
# punctuation symbols carry an empty option too, because "M0R0N!!" ends in
# two exclamation marks that mean nothing - they are not letters at all.
SUBSTITUTIONS = {
    "0": ("o",), "1": ("i", "l"), "3": ("e",), "4": ("a",), "5": ("s",),
    "6": ("g",), "7": ("t",), "8": ("b",), "9": ("g",), "2": ("z",),
    "@": ("a", ""), "$": ("s", ""), "!": ("i", ""), "|": ("i", "l", ""),
    "+": ("t", ""),
}

# Anything that is not one of these splits one token from the next.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9@$!|+]+")

MAX_BODY = 500
REPORT_THRESHOLD = 3        # reports needed on a post nobody voted on
REPORT_SCORE_DIVISOR = 10   # +1 report needed for every 10 net upvotes
MAX_VARIANTS = 256          # stop combinatorial blow-up on silly input

RATE_LIMIT = 5              # posts ...
RATE_WINDOW = 600           # ... per 10 minutes, per device


class ModerationError(ValueError):
    pass


# --------------------------------------------------------------------------
# 1. the word filter
# --------------------------------------------------------------------------

def collapse_runs(word):
    """stuuupid -> stupid. Repeated letters collapse to one."""
    out = []
    for ch in word:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def tokenise(text):
    """Split into candidate words, keeping the characters a dodger would use."""
    return [t for t in _TOKEN_SPLIT.split(str(text).lower()) if t]


def expand(token):
    """Every plain-letter reading of a token that uses leetspeak.

    `stup1d` -> {"stupid", "stupld"}. Unknown symbols are dropped, so
    `s-t-u-p-i-d` as a single token still reads as `stupid`.
    """
    forms = [""]
    for ch in str(token).lower():
        if ch in SUBSTITUTIONS:
            options = SUBSTITUTIONS[ch]
        elif "a" <= ch <= "z":
            options = (ch,)
        else:
            continue
        if len(forms) * len(options) > MAX_VARIANTS:
            options = options[:1]
        forms = [f + o for f in forms for o in options]
    out = set()
    for f in forms:
        if f:
            out.add(f)
            out.add(collapse_runs(f))
    return out


def _glued(tokens, max_part=2, max_window=10):
    """Re-join runs of short tokens: `s.t.u.p.i.d` arrives as six tokens.

    Only pieces of `max_part` characters or fewer are glued. That is a
    deliberate trade: it catches `s.t.u.p.i.d` and `st.up.id`, and it leaves
    `lose rank` and `a los er` alone. Push `max_part` to 3 and you catch
    `los er` as well - and start inventing words nobody wrote. Pick a side
    and put a test on it either way.
    """
    out = []
    n = len(tokens)
    for i in range(n):
        if len(tokens[i]) > max_part:
            continue
        joined = tokens[i]
        for j in range(i + 1, min(n, i + max_window)):
            if len(tokens[j]) > max_part:
                break
            joined += tokens[j]
            out.append(joined)
    return out


def _canonical_index():
    idx = {}
    for w in BLOCKED_WORDS:
        idx[w] = w
        idx[collapse_runs(w)] = w
    return idx


_CANON = _canonical_index()


def find_blocked(text):
    """Which blocked words this text contains, however they were spelled."""
    tokens = tokenise(text)
    hits = set()
    for cand in list(tokens) + _glued(tokens):
        for form in expand(cand):
            if form in _CANON:
                hits.add(_CANON[form])
    return sorted(hits)


def is_clean(text):
    return not find_blocked(text)


def check_body(text):
    """Validate a submission before it is ever written down."""
    body = str(text or "").strip()
    if not body:
        raise ModerationError("a confession needs some words in it")
    if len(body) > MAX_BODY:
        raise ModerationError(f"too long: {len(body)} characters, limit is {MAX_BODY}")
    return body


def moderate(text):
    """The verdict on a brand new post.

    Returns {"status": "published"|"held", "matched": [...], "body": str}.
    Nothing is rejected outright - a human decides that from the queue.
    """
    body = check_body(text)
    matched = find_blocked(body)
    return {"status": "held" if matched else "published",
            "matched": matched,
            "body": body}


# --------------------------------------------------------------------------
# 2. the report rule
# --------------------------------------------------------------------------

def reports_needed(score, base=REPORT_THRESHOLD, divisor=REPORT_SCORE_DIVISOR):
    """How many reports it takes to pull THIS post back into moderation.

    A post under `divisor` upvotes needs `base`. Every further `divisor`
    upvotes buys it one more report of protection, so a small group cannot
    bury something the room liked. Downvoted posts never need less than
    `base` - being unpopular is not a reason to need fewer reports.
    """
    if base < 1:
        raise ModerationError("the base threshold must be at least 1")
    if divisor < 1:
        raise ModerationError("the divisor must be at least 1")
    return base + max(0, int(score)) // int(divisor)


def should_remoderate(reports, score, base=REPORT_THRESHOLD):
    return int(reports) >= reports_needed(score, base=base)


def status_after_report(status, reports, score, base=REPORT_THRESHOLD):
    """A held or rejected post does not change. A published one may be pulled."""
    if status != "published":
        return status
    return "held" if should_remoderate(reports, score, base=base) else "published"


# --------------------------------------------------------------------------
# 3. the fixed-window rate limit
# --------------------------------------------------------------------------

def window_start(now, window=RATE_WINDOW):
    """Epoch second the current fixed window began."""
    if window <= 0:
        raise ModerationError("the window must be positive")
    return (int(now) // int(window)) * int(window)


def check_rate(used, now, limit=RATE_LIMIT, window=RATE_WINDOW):
    """`used` is how many posts this device already made in this window.

    Returns allowed / remaining / retry_after / reset_at. `retry_after` is 0
    while there is headroom, and otherwise the seconds until the window rolls.
    """
    if limit < 1:
        raise ModerationError("the limit must be at least 1")
    used = max(0, int(used))
    start = window_start(now, window)
    reset = start + int(window)
    allowed = used < limit
    return {"allowed": allowed,
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used - (1 if allowed else 0)),
            "reset_at": reset,
            "retry_after": 0 if allowed else max(1, reset - int(now))}


def burst_capacity(limit=RATE_LIMIT):
    """The honest flaw in a fixed window.

    A device that posts `limit` in the last second of one window and `limit`
    in the first second of the next has posted 2*limit in about two seconds
    while never breaking the rule. A sliding window fixes it; this does not.
    """
    return 2 * limit


# --------------------------------------------------------------------------
# anonymity
# --------------------------------------------------------------------------

def device_bucket(device_id, salt):
    """A one-way, salted handle for a device.

    This is what gets stored - never the device id itself. Rotate the salt
    (it is the day, in the app) and yesterday's handles stop matching today's,
    so nothing accumulates into a profile. It is one-way, so a post can never
    be traced back to the phone that wrote it.

    That rotation is not free, and the cost is the interesting part. At
    midnight every "already voted" and "already reported" marker stops
    matching, so one device can report the same post again tomorrow. Reports
    accumulate in Postgres, so three days of one person is three reports.
    Rotate slower and you keep a linkable handle for longer. There is no
    setting that gives you both; say which way you went and why.
    """
    did = str(device_id or "").strip()
    if not did:
        raise ModerationError("a device id is required")
    return hmac.new(str(salt).encode(), did.encode(), hashlib.sha256).hexdigest()[:32]
