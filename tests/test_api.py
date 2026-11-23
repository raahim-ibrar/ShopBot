import pytest
from fastapi.testclient import TestClient

from shopbot.api import create_app


client = TestClient(create_app())
chat_body = {"user_id": "shopper-a", "thread_id": "thread-1", "message": "Find a charger."}


def test_health_is_plain_text():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.content == b"ok"


def test_public_chat_returns_exact_contract():
    response = client.post("/chat", json=chat_body)

    assert response.status_code == 200
    assert response.json() == {
        "reply": "The PortPair API is running. Shopping features are being set up.",
        "tool_calls": [],
        "tokens": {"in": 0, "out": 0},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "shopper-a", "message": "Find a charger."},
        {**chat_body, "message": "  "},
        {**chat_body, "message": "x" * 4001},
        {**chat_body, "user_id": 42},
        {**chat_body, "user_id": "  "},
        {**chat_body, "thread_id": "x" * 129},
        {**chat_body, "extra": "value"},
    ],
)
def test_rejects_invalid_chat_body(body):
    assert client.post("/chat", json=body).status_code == 422


def test_rejects_malformed_json():
    response = client.post("/chat", content="{", headers={"content-type": "application/json"})

    assert response.status_code == 422
