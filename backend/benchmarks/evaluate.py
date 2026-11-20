"""
Evaluation of the writing assistant against a running backend.

Each sentence of sentences.json is sent alone to /assist at every threshold (0.75 → 0.90, step
0.01) and once to /suggest for the ranking metrics. Responses are appended to
results/<label>.jsonl as they arrive, so an interrupted run resumes where it stopped.

  support        reformulates a claim of `doc`: a hit on `doc` is a true positive, a hit on
                 another document a false positive, no hit a miss
  offtopic       must trigger nothing; any hit is a false positive
  contradiction  contradicts `doc`: reported apart (trigger rate on `doc`, share flagged
                 "à vérifier")
  parasites      wrong extra suggestions shown per triggered support sentence

S sentences, T thresholds: S·(T+1) HTTP calls; the backend memoises query embeddings, so each
call after the first for a sentence costs one O(N·d) matmul.

    python benchmarks/evaluate.py --label base                       # tuning set
    python benchmarks/evaluate.py --label base-val --set validation  # held out, never tuned on
    QUOTELY_PRECISION=1 python main.py & python benchmarks/evaluate.py --label precision --precise
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
THRESHOLDS = [round(0.75 + 0.01 * i, 2) for i in range(16)]


def load_done(path: Path) -> dict[tuple, dict]:
    done = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[(r["t"], r["id"])] = r
    return done


def run(client: httpx.Client, sentences: list[dict], out: Path, precise: bool) -> dict[tuple, dict]:
    done = load_done(out)
    with out.open("a") as f:
        for s in sentences:
            if ("rank", s["id"]) not in done:
                cites = client.post("/suggest", json={"text": s["text"], "n": 5, "precise": precise}).json()["citations"]
                done[("rank", s["id"])] = r = {"t": "rank", "id": s["id"], "keys": [c["bibtex_key"] for c in cites]}
                f.write(json.dumps(r) + "\n")
            for t in THRESHOLDS:
                if (t, s["id"]) in done:
                    continue
                items = client.post("/assist", json={"text": s["text"], "threshold": t}).json()["items"]
                sugg = items[0]["suggestions"] if items else []
                done[(t, s["id"])] = r = {
                    "t": t, "id": s["id"], "keys": [x["bibtex_key"] for x in sugg],
                    "flagged": [x["bibtex_key"] for x in sugg if x.get("contradiction")],
                    "confidence": [x["confidence"] for x in sugg],
                }
                f.write(json.dumps(r) + "\n")
            f.flush()
    return done


def metrics(sentences: list[dict], done: dict[tuple, dict], keys: dict[str, str]) -> tuple[dict, list[dict]]:
    support = [s for s in sentences if s["kind"] == "support"]
    off = [s for s in sentences if s["kind"] == "offtopic"]
    contra = [s for s in sentences if s["kind"] == "contradiction"]
    exp = lambda s: keys[s["doc"]]
    rank = {
        "R@1": sum(exp(s) in done[("rank", s["id"])]["keys"][:1] for s in support) / len(support),
        "R@3": sum(exp(s) in done[("rank", s["id"])]["keys"][:3] for s in support) / len(support),
    }
    rows = []
    for t in THRESHOLDS:
        hit = lambda s: done[(t, s["id"])]["keys"]
        flagged = lambda s: exp(s) in done[(t, s["id"])]["flagged"]
        tp = sum(exp(s) in hit(s) and not flagged(s) for s in support)
        wrong = sum(bool(hit(s)) and exp(s) not in hit(s) for s in support)
        fp_off = sum(bool(hit(s)) for s in off)
        c_trig = sum(exp(s) in hit(s) and not flagged(s) for s in contra)
        c_flag = sum(flagged(s) for s in contra)
        trig = [s for s in support if hit(s)]
        noise = sum(sum(k != exp(s) for k in hit(s)) for s in trig) / len(trig) if trig else 0.0
        p = tp / (tp + wrong + fp_off) if tp + wrong + fp_off else 1.0
        r = tp / len(support)
        rows.append({
            "threshold": t, "P": p, "R": r, "F1": 2 * p * r / (p + r) if p + r else 0.0,
            "P_contra": tp / (tp + wrong + fp_off + c_trig) if tp + wrong + fp_off + c_trig else 1.0,
            "FP_off": fp_off / len(off), "contra_trig": c_trig / len(contra), "contra_flag": c_flag / len(contra),
            "support_flagged": sum(flagged(s) for s in support) / len(support), "noise": noise,
        })
    return rank, rows


def choose(rows: list[dict], min_precision: float = 0.85) -> dict:
    ok = [r for r in rows if r["P"] >= min_precision]
    return max(ok or rows, key=lambda r: (r["F1"], r["P"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7331")
    ap.add_argument("--label", default="base")
    ap.add_argument("--precise", action="store_true")
    ap.add_argument("--set", default="sentences", help="sentences (tuning) or validation (held out)")
    args = ap.parse_args()

    sentences = json.loads((HERE / f"{args.set}.json").read_text())
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    with httpx.Client(base_url=args.url, headers={"X-Quotely-Client": "benchmark"}, timeout=120) as client:
        papers = client.get("/papers").json()
        keys = {Path(p["file_path"]).stem: p["bibtex_key"] for p in papers}
        missing = sorted({s["doc"] for s in sentences if "doc" in s} - set(keys))
        if missing:
            sys.exit(f"Documents not indexed: {', '.join(missing)}")
        done = run(client, sentences, results / f"{args.label}.jsonl", args.precise)

    rank, rows = metrics(sentences, done, keys)
    best = choose(rows)
    print(f"\n{args.label}: R@1 {rank['R@1']:.3f} · R@3 {rank['R@3']:.3f}  ({len(sentences)} phrases)\n")
    print("seuil   P      R      F1     P+contra  FP hors-sujet  contra déclench.  contra signalées  appuis signalés  parasites")
    for r in rows:
        mark = " ←" if r is best else ""
        print(f"{r['threshold']:.2f}   {r['P']:.3f}  {r['R']:.3f}  {r['F1']:.3f}  {r['P_contra']:.3f}     "
              f"{r['FP_off']:.2f}           {r['contra_trig']:.2f}              {r['contra_flag']:.2f}              "
              f"{r['support_flagged']:.2f}             {r['noise']:.2f}{mark}")
    summary = {"label": args.label, **rank, "best": best, "rows": rows}
    (results / f"{args.label}_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSeuil retenu (P ≥ 0.85, F1 max) : {best['threshold']:.2f}")


if __name__ == "__main__":
    main()
