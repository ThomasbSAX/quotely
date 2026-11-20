from fastapi.testclient import TestClient

import main


def test_requests_without_client_header_are_rejected(home):
    c = TestClient(main.app)
    assert c.get("/papers").status_code == 403
    assert c.get("/papers", headers={"X-Quotely-Client": "vscode"}).status_code == 200


def test_upload_filename_is_sanitised(home):
    import config
    c = TestClient(main.app)
    r = c.post("/ingest", headers={"X-Quotely-Client": "t"},
               files={"file": ("../../evil.md", b"# Evil paper title 2020\n" + b"word " * 40)})
    assert r.status_code == 200
    assert (config.PAPERS_PATH / "evil.md").exists()


def test_assist_and_paper_endpoints(home):
    import config
    from conftest import write
    import indexer
    write(config.PAPERS_PATH / "c.md", "# Coral reefs\n\nCoral reef bleaching increased sharply with ocean warming events.\n")
    indexer.startup()
    c = TestClient(main.app)
    h = {"X-Quotely-Client": "t"}
    r = c.post("/assist", headers=h, json={"text": "Coral reef bleaching increased sharply with ocean warming events.", "threshold": 0.5})
    item = r.json()["items"][0]
    key = item["suggestions"][0]["bibtex_key"]
    assert item["suggestions"][0]["passage"]["text"].startswith("Coral reef")
    assert c.get("/paper", params={"key": key}, headers=h).json()["title"] == "Coral reefs"
    assert c.get("/health", headers=h).json()["indexed_documents"] == 1
