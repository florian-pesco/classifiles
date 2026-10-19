import time

from fastapi.testclient import TestClient

from classifiles_api.app import create_app


def test_api_flow(service, tree, tmp_path):
    client = TestClient(create_app(service, config_path=str(tmp_path / "cfg.yaml")))
    cats = client.get("/api/categories").json()["categories"]
    assert [c["name"] for c in cats] == ["Finance", "Photos", "Software", "Work", "Delete"]
    assert client.get("/api/does-not-exist").status_code == 404

    scan = client.post("/api/scans", json={}).json()
    for _ in range(100):
        scan = client.get(f"/api/scans/{scan['id']}").json()
        if scan["status"] != "running":
            break
        time.sleep(0.05)
    assert scan["status"] == "done"

    rows = client.get("/api/proposals", params={"scan_id": scan["id"]}).json()
    assert len(rows) == 5
    pid = next(r["id"] for r in rows if r["name"] == "invoice_march.txt")
    r = client.patch(f"/api/proposals/{pid}", json={"action": "accept", "folder": "Finance"})
    assert r.json()["status"] == "accepted"
    assert client.patch(f"/api/proposals/{pid}", json={"action": "accept", "folder": "Nope"}).status_code == 400
    assert client.patch("/api/proposals/99999", json={"action": "reject"}).status_code == 404

    plan = client.post("/api/apply/plan", json={"scan_id": scan["id"]}).json()
    assert len(plan) == 1
    res = client.post("/api/apply", json={"scan_id": scan["id"]}).json()
    assert len(res["moved"]) == 1 and (tree["root"] / "Finance" / "invoice_march.txt").exists()
    assert len(client.post("/api/undo").json()["restored"]) == 1

    s = client.patch("/api/settings", json={"threshold": 0.9}).json()
    assert s["threshold"] == 0.9 and (tmp_path / "cfg.yaml").exists()


def test_categories_rules_fs_and_file_endpoints(service, tree, tmp_path):
    client = TestClient(create_app(service, config_path=str(tmp_path / "cfg.yaml")))
    cats = client.get("/api/categories").json()
    names = [c["name"] for c in cats["categories"]]
    assert names == ["Finance", "Photos", "Software", "Work", "Delete"]

    entries = [{"path": c["path"], "description": c["description"]} for c in cats["categories"] if c["path"]]
    entries[1]["enabled"] = False
    r = client.put("/api/categories", json={"roots": cats["roots"], "categories": entries, "delete": cats["delete"]})
    assert r.status_code == 200
    assert next(c for c in r.json()["categories"] if c["name"] == "Photos")["usable"] is False

    assert client.put("/api/rules", json={"rules": [{"ext": ".exe", "folder": "Nope"}]}).status_code == 400
    ok = client.put("/api/rules", json={"rules": [{"ext": ".exe", "folder": "Delete"}]})
    assert ok.status_code == 200 and "installer" in ok.json()["kinds"]

    fs = client.get("/api/fs", params={"path": str(tree["root"])}).json()
    assert "Finance" in fs["dirs"] and fs["parent"] == str(tree["root"].parent)
    assert client.get("/api/fs", params={"path": str(tree["root"] / "nope")}).status_code == 400

    _, rows = service.scan(tree["inbox"])
    pid = next(r["id"] for r in rows if r["name"] == "invoice_march.txt")
    f = client.get(f"/api/proposals/{pid}/file")
    assert f.status_code == 200 and b"Invoice" in f.content and "inline" in f.headers["content-disposition"]

    scans = client.get("/api/scans").json()
    assert scans[0]["counts"]
