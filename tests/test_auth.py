def test_open_when_no_password_set(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert client.get("/exercises").status_code == 200


def test_password_gate(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "correct horse")

    # The page itself and the health check stay reachable...
    assert client.get("/health").status_code == 200
    # ...but the API is locked.
    assert client.get("/exercises").status_code == 401

    assert client.post("/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/exercises").status_code == 401

    assert client.post("/login", json={"password": "correct horse"}).status_code == 200
    assert client.get("/exercises").status_code == 200   # the cookie now lets us in

    client.post("/logout")
    assert client.get("/exercises").status_code == 401


def test_changing_password_logs_everyone_out(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "old")
    client.post("/login", json={"password": "old"})
    assert client.get("/exercises").status_code == 200

    monkeypatch.setenv("APP_PASSWORD", "new")
    assert client.get("/exercises").status_code == 401
