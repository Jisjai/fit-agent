"""The files that make the app installable on a phone."""


def test_manifest_and_service_worker_are_served(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200
    assert m.json()["display"] == "standalone"

    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in sw.headers["content-type"]


def test_every_icon_in_the_manifest_exists(client):
    for icon in client.get("/manifest.webmanifest").json()["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]
    assert client.get("/icons/apple-touch-icon.png").status_code == 200


def test_install_files_work_without_signing_in(client):
    # Phones fetch these without the session cookie, so the sign-in check must let them through.
    client.cookies.clear()
    for path in ["/manifest.webmanifest", "/sw.js", "/icons/icon-192.png", "/favicon.ico"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/exercises").status_code == 401   # ...but the data stays locked


def test_icons_route_cannot_read_other_files(client):
    assert client.get("/icons/..%2Fsw.js").status_code == 404
    assert client.get("/icons/missing.png").status_code == 404
