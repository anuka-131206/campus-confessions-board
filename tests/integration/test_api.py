import os
import uuid

import pytest
from fastapi.testclient import TestClient

# These tests intentionally use the real services configured by CI/Compose.
# No mocks are used for Postgres or Redis.
os.environ.setdefault("DATABASE_URL", "postgresql://confessions:confessions@localhost:5432/confessions")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.main import app  # noqa: E402
from app import cache  # noqa: E402


@pytest.fixture(scope="module")
def client():
    # Fail clearly if integration services were not started.
    cache.client().ping()
    from app import db

    db.query("SELECT 1")
    return TestClient(app)


def test_health_reports_both_real_services(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["postgres"] is True
    assert response.json()["redis"] is True


def test_seeded_board_and_post_detail(client):
    response = client.get("/posts")
    assert response.status_code == 200
    assert len(response.json()["posts"]) >= 1

    response = client.get("/posts/1")
    assert response.status_code == 200
    assert "comments" in response.json()


def test_create_vote_and_duplicate_vote_are_enforced(client):
    device = "integration-vote-" + uuid.uuid4().hex
    response = client.post(
        "/posts",
        headers={"X-Device-Id": device},
        json={"body": "integration test confession " + uuid.uuid4().hex},
    )
    assert response.status_code == 201
    post_id = response.json()["id"]

    response = client.post(
        f"/posts/{post_id}/vote",
        headers={"X-Device-Id": device},
        json={"direction": "up"},
    )
    assert response.status_code == 200

    response = client.post(
        f"/posts/{post_id}/vote",
        headers={"X-Device-Id": device},
        json={"direction": "up"},
    )
    assert response.status_code == 409


def test_blocked_post_enters_moderation_queue(client):
    device = "integration-moderation-" + uuid.uuid4().hex
    response = client.post(
        "/posts",
        headers={"X-Device-Id": device},
        json={"body": "this is stup1d " + uuid.uuid4().hex},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "held"
    post_id = response.json()["id"]

    response = client.get("/posts/" + str(post_id))
    assert response.status_code == 404

    queue = client.get("/moderation/queue")
    assert queue.status_code == 200
    assert any(item["id"] == post_id for item in queue.json()["held"])

    response = client.post(
        f"/moderation/{post_id}",
        json={"action": "approve"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"

    response = client.get("/posts/" + str(post_id))
    assert response.status_code == 200


def test_three_distinct_reports_pull_unpopular_post_back_to_queue(client):
    creator = "integration-report-creator-" + uuid.uuid4().hex
    response = client.post(
        "/posts",
        headers={"X-Device-Id": creator},
        json={"body": "report threshold integration " + uuid.uuid4().hex},
    )
    assert response.status_code == 201
    post_id = response.json()["id"]

    for index in range(3):
        response = client.post(
            f"/posts/{post_id}/report",
            headers={"X-Device-Id": f"integration-reporter-{uuid.uuid4().hex}-{index}"},
        )
        assert response.status_code == 200

    assert response.json()["status"] == "held"
    assert response.json()["back_in_queue"] is True
