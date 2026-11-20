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
