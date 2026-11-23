from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from shopbot import storefront
from shopbot.api import create_app


@pytest.fixture
def client(monkeypatch):
    settings = {"STOREFRONT_API_SECRET": "customer-key", "ADMIN_API_SECRET": "seller-key", "ADMIN_PASSWORD": "test-password"}
    monkeypatch.setattr(storefront, "get_setting", settings.get)
    with TestClient(create_app()) as app_client:
        yield app_client


def test_storefront_key_cannot_use_seller_routes(client):
    assert client.get("/internal/catalog").status_code == 403
    assert client.get("/internal/seller/dashboard", headers={"X-PortPair-Key": "customer-key"}).status_code == 403
    assert client.get("/internal/catalog", headers={"X-PortPair-Key": "seller-key"}).status_code == 403


def test_seller_needs_password_and_server_key(client):
    login = "/internal/seller/login"
    assert client.post(login, json={"password": "test-password"}).status_code == 403
    assert client.post(login, headers={"X-PortPair-Key": "seller-key"}, json={"password": "wrong"}).status_code == 401
    assert client.post(login, headers={"X-PortPair-Key": "seller-key"}, json={"password": "test-password"}).json() == {"ok": True}


def test_website_chat_uses_separate_shopper_identity(monkeypatch):
    monkeypatch.setattr(storefront, "get_setting", lambda _: "customer-key")
    agent = Mock()
    agent.respond.return_value = {"reply": "Here to help", "tool_calls": [], "tokens_in": 0, "tokens_out": 0}
    session = str(uuid4())
    with TestClient(create_app(agent=agent)) as client:
        website = client.post("/internal/chat", headers={"X-PortPair-Key": "customer-key"}, json={"session_id": session, "message": "Hello"})
        grading = client.post("/chat", json={"user_id": session, "thread_id": "storefront", "message": "Hello"})

    assert website.json() == grading.json() == {"reply": "Here to help", "tool_calls": [], "tokens": {"in": 0, "out": 0}}
    website_shopper = agent.respond.call_args_list[0].args[0]
    grading_shopper = agent.respond.call_args_list[1].args[0]
    assert website_shopper.thread_key != grading_shopper.thread_key
