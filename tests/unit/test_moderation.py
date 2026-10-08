from app.moderation import (
    RATE_LIMIT,
    RATE_WINDOW,
    burst_capacity,
    check_body,
    check_rate,
    device_bucket,
    find_blocked,
    moderate,
    reports_needed,
    status_after_report,
    window_start,
)


def test_blocked_word_disguises_are_caught():
    for text in ["stupid", "STUPID", "stup1d", "$tupid", "stuuupid", "s.t.u.p.i.d"]:
        assert find_blocked(text) == ["stupid"]


def test_innocent_words_do_not_trigger_filter():
    for text in ["classmate", "assignment", "analysis", "dumbbell"]:
        assert find_blocked(text) == []


def test_empty_and_whitespace_bodies_are_rejected():
    for text in ["", "   ", "\n\t"]:
        try:
            check_body(text)
        except ValueError:
            pass
        else:
            raise AssertionError("empty body should be rejected")


def test_body_is_trimmed_and_moderation_holds_blocked_text():
    assert check_body("  hello  ") == "hello"
    assert moderate("hello")["status"] == "published"
    result = moderate("you are stup1d")
    assert result["status"] == "held"
    assert result["matched"] == ["stupid"]


def test_body_length_limit():
    assert len(check_body("x" * 500)) == 500
    try:
        check_body("x" * 501)
    except ValueError:
        pass
    else:
        raise AssertionError("oversized body should be rejected")


def test_reports_needed_around_every_multiple():
    assert reports_needed(9) == 3
    assert reports_needed(10) == 4
    assert reports_needed(11) == 4
    assert reports_needed(19) == 4
    assert reports_needed(20) == 5
    assert reports_needed(21) == 5
    assert reports_needed(-100) == 3


def test_report_status_changes_at_exact_threshold():
    assert status_after_report("published", 2, 0) == "published"
    assert status_after_report("published", 3, 0) == "held"
    assert status_after_report("held", 100, 0) == "held"
    assert status_after_report("rejected", 100, 0) == "rejected"


def test_rate_limit_first_last_allowed_and_first_rejected():
    now = 12345
    assert check_rate(0, now)["allowed"] is True
    assert check_rate(RATE_LIMIT - 1, now)["allowed"] is True
    assert check_rate(RATE_LIMIT, now)["allowed"] is False
    assert check_rate(RATE_LIMIT, now)["retry_after"] > 0


def test_rate_limit_retry_at_window_edges():
    start = window_start(1200, RATE_WINDOW)
    assert start == 1200
    assert check_rate(RATE_LIMIT, start)["retry_after"] == RATE_WINDOW
    assert check_rate(RATE_LIMIT, start + RATE_WINDOW - 1)["retry_after"] == 1


def test_burst_capacity_documents_fixed_window_boundary():
    assert burst_capacity() == 2 * RATE_LIMIT


def test_device_bucket_is_salted_and_stable():
    first = device_bucket("device-123", "salt-a")
    assert first == device_bucket("device-123", "salt-a")
    assert first != device_bucket("device-123", "salt-b")
    assert first != device_bucket("device-456", "salt-a")
    assert len(first) == 32


def test_invalid_rate_parameters_are_rejected():
    for args in [(0, 1), (1, 0)]:
        try:
            check_rate(0, 100, limit=args[0], window=args[1])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid rate parameters should fail")


def test_invalid_device_is_rejected():
    try:
        device_bucket("", "salt")
    except ValueError:
        pass
    else:
        raise AssertionError("missing device id should fail")
