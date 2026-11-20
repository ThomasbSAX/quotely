import numpy as np

from vector_store import VectorStore


def meta(path, key="K", sha="h"):
    return {"file_path": path, "bibtex_key": key, "file_hash": sha}


def test_query_order_and_persistence(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 3)
    s.upsert(["a", "b", "c"], np.eye(3), ["A", "B", "C"], [meta("/x/a", "A"), meta("/x/b", "B"), meta("/y/c", "C")])
    assert [i for i, _ in s.query([1, 0.5, 0], 2)] == [0, 1]
    s.save()
    t = VectorStore(tmp_path / "idx", "sig", 3)
    assert t.count() == 3 and t.key_for_path("/y/c") == "C"


def test_prefix_filter_applied_before_topk(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    vecs = np.array([[1, 0]] * 50 + [[0.1, 1]])
    s.upsert([str(i) for i in range(51)], vecs, ["d"] * 51, [meta("/other/f", "O")] * 50 + [meta("/ws/p", "W")])
    hits = s.query([1, 0], 5, ("/ws",))
    assert [i for i, _ in hits] == [50]
    assert s.query([1, 0], 5, ("/w",)) == []


def test_delete_and_signature_change(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    s.upsert(["a", "b"], np.eye(2), ["A", "B"], [meta("/a", "A"), meta("/b", "B")])
    assert s.delete_where("file_path", "/a") == 1
    assert s.count() == 1 and not s.has_path("/a") and s.key_owner("A") is None
    s.save()
    t = VectorStore(tmp_path / "idx", "other-sig", 2)
    assert t.count() == 0 and t.stale_paths == ["/b"]


def test_interrupted_save_recovers_previous(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    s.upsert(["a"], np.eye(2)[:1], ["A"], [meta("/a", "A")])
    s.save()
    (tmp_path / "idx").rename(tmp_path / "idx.old")
    assert VectorStore(tmp_path / "idx", "sig", 2).count() == 1
