import numpy as np

from vector_store import VectorStore


def doc(key, sha="h"):
    return {"bibtex_key": key, "file_hash": sha}


def test_dense_order_and_persistence(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 3)
    s.add_document("/x/a", doc("A"), np.eye(3)[:2], ["alpha one", "alpha two"], [{"chunk": 0}, {"chunk": 1}])
    s.add_document("/y/c", doc("C"), np.eye(3)[2:], ["gamma"], [{"chunk": 0}])
    assert [i for i, _ in s.dense_top([1, 0.5, 0], 2)[0]] == [0, 1]
    s.save()
    t = VectorStore(tmp_path / "idx", "sig", 3)
    assert t.count() == 3 and t.key_owner("C") == "/y/c" and t.rows[2]["path"] == "/y/c"


def test_scope_filter_applied_before_topk(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    s.add_document("/other/f", doc("O"), np.array([[1, 0]] * 50), ["o"] * 50, [{"chunk": i} for i in range(50)])
    s.add_document("/ws/p", doc("W"), np.array([[0.1, 1]]), ["w"], [{"chunk": 0}])
    assert [i for i, _ in s.dense_top([1, 0], 5, ("/ws",))[0]] == [50]
    assert s.dense_top([1, 0], 5, ("/w",))[0] == []


def test_replace_remove_and_signature_change(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    s.add_document("/a", doc("A"), np.eye(2), ["a1", "a2"], [{"chunk": 0}, {"chunk": 1}])
    s.add_document("/b", doc("B"), np.eye(2)[:1], ["b"], [{"chunk": 0}])
    s.add_document("/a", doc("A", "h2"), np.eye(2)[1:], ["a3"], [{"chunk": 0}])
    assert s.count() == 2 and s.texts == ["b", "a3"]
    assert s.remove_document("/b") and s.key_owner("B") is None and s.count() == 1
    s.save()
    t = VectorStore(tmp_path / "idx", "other", 2)
    assert t.count() == 0 and t.stale_keys == {"/a": "A"}


def test_bm25_prefers_rare_exact_terms(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    texts = ["the model works well", "transformer attention model", "the the the model"]
    s.add_document("/a", doc("A"), np.ones((3, 2)), texts, [{"chunk": i} for i in range(3)])
    scores = s.bm25("attention model", [0, 1, 2])
    assert max(range(3), key=lambda i: scores[i]) == 1


def test_interrupted_save_recovers_previous(tmp_path):
    s = VectorStore(tmp_path / "idx", "sig", 2)
    s.add_document("/a", doc("A"), np.eye(2)[:1], ["a"], [{"chunk": 0}])
    s.save()
    (tmp_path / "idx").rename(tmp_path / "idx.old")
    assert VectorStore(tmp_path / "idx", "sig", 2).count() == 1
