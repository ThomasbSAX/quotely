#!/usr/bin/env python3
"""
Quotely — Évaluation automatique
Lance avec : backend/.venv/bin/python3 eval/run_eval.py
"""
import os, json, re, time, random, datetime, sys
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────
BACKEND_URL   = "http://127.0.0.1:7331"
LM_STUDIO_URL = "http://10.3.160.196:1234/v1/chat/completions"
LM_MODEL      = "mistralai/ministral-3-14b-reasoning"

N_PAPERS      = 100   # None = tous les articles
N_SUGGESTIONS = 15
PASSAGE_TOKENS = 450
TEMPERATURE   = 0.7
TIMEOUT_LLM   = 120
TIMEOUT_BACK  = 30

RESULTS_DIR   = Path(__file__).parent / "results"
RAW_FILE      = RESULTS_DIR / "raw.jsonl"
SUMMARY_FILE  = RESULTS_DIR / "summary.json"
PASSAGES_FILE = RESULTS_DIR / "passages.jsonl"

RESULTS_DIR.mkdir(exist_ok=True)

# ── Imports ───────────────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("❌ 'requests' introuvable. Lance avec : backend/.venv/bin/python3 eval/run_eval.py")
    sys.exit(1)

# ── Test de connectivité ──────────────────────────────────────────────────────
def test_services():
    print("\n── Test des services ─────────────────────────────────────")
    # Quotely
    try:
        h = requests.get(f"{BACKEND_URL}/health", timeout=5).json()
        print(f"✅ Quotely     : {h['indexed_chunks']} chunks indexés")
    except Exception as e:
        print(f"❌ Quotely KO  : {e}")
        sys.exit(1)

    # LM Studio
    try:
        r = requests.post(LM_STUDIO_URL, json={
            "model": LM_MODEL,
            "messages": [{"role": "user", "content": "Réponds juste 'ok'"}],
            "max_tokens": 10,
        }, timeout=20)
        content = r.json()["choices"][0]["message"]["content"]
        print(f"✅ LM Studio   : {LM_MODEL}")
        print(f"   Réponse test: {content[:80].strip()}")
    except Exception as e:
        print(f"❌ LM Studio KO: {e}")
        sys.exit(1)
    print("──────────────────────────────────────────────────────────\n")

# ── Helpers ───────────────────────────────────────────────────────────────────
def strip_reasoning(text: str) -> str:
    """Supprime les balises de raisonnement (Mistral reasoning, DeepSeek…)."""
    text = re.sub(r"\[THINK\].*?\[/THINK\]", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*?</think>",      "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<\|thinking\|>.*?<\|/thinking\|>", "", text, flags=re.DOTALL)
    return text.strip()


def get_chunks(bibtex_key: str, n: int = 4) -> list[str]:
    try:
        r = requests.get(f"{BACKEND_URL}/chunks",
                         params={"key": bibtex_key, "n": n},
                         timeout=TIMEOUT_BACK)
        return r.json().get("chunks", [])
    except Exception:
        return []


SYSTEM = (
    "Tu es un chercheur académique. Écris un passage de ~400 tokens dans le style "
    "d'un article scientifique sur le sujet du document fourni.\n"
    "RÈGLES : ne mentionne pas le titre exact ni les auteurs. "
    "Même langue que le document. Style dense et factuel. "
    "Réponds UNIQUEMENT avec le passage, sans commentaire ni titre."
)


def generate_passage(paper: dict, chunks: list[str]) -> dict:
    context = "\n".join(f"- {c[:300]}" for c in chunks[:3]) or "(aucun extrait disponible)"
    prompt  = (
        f"Titre : {paper['title']}\n"
        f"Auteur(s) : {paper['authors'] or 'Inconnu'}\n"
        f"Année : {paper['year']}\n\n"
        f"Extraits :\n{context}\n\n"
        f"Écris le passage académique."
    )
    t0 = time.perf_counter()
    r = requests.post(LM_STUDIO_URL, json={
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": PASSAGE_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False,
    }, timeout=TIMEOUT_LLM)
    elapsed = round(time.perf_counter() - t0, 2)
    data    = r.json()
    passage = strip_reasoning(data["choices"][0]["message"]["content"])
    tokens  = data.get("usage", {}).get("completion_tokens", 0)
    return {"passage": passage, "tokens": tokens, "latency_s": elapsed}


def query_quotely(passage: str) -> list[dict]:
    r = requests.post(f"{BACKEND_URL}/suggest",
                      json={"text": passage, "n": N_SUGGESTIONS},
                      timeout=TIMEOUT_BACK)
    return r.json().get("citations", [])


def find_rank(key: str, suggestions: list[dict]) -> int | None:
    for i, s in enumerate(suggestions):
        if s["bibtex_key"] == key:
            return i + 1
    return None

# ── Chargement des articles ───────────────────────────────────────────────────
def load_papers() -> list[dict]:
    papers = requests.get(f"{BACKEND_URL}/papers", timeout=10).json()
    papers = [p for p in papers if p["title"] and len(p["title"].strip()) > 10]
    random.seed(42)
    if N_PAPERS and len(papers) > N_PAPERS:
        papers = random.sample(papers, N_PAPERS)
    return papers

# ── Checkpoint ────────────────────────────────────────────────────────────────
def load_done() -> set[str]:
    done = set()
    if RAW_FILE.exists():
        with open(RAW_FILE) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["bibtex_key"])
                except Exception:
                    pass
    return done

# ── Boucle principale ─────────────────────────────────────────────────────────
def run():
    test_services()

    papers   = load_papers()
    done     = load_done()
    todo     = [p for p in papers if p["bibtex_key"] not in done]

    print(f"{len(papers)} articles sélectionnés, {len(done)} déjà faits, {len(todo)} restants\n")

    for i, paper in enumerate(todo):
        key = paper["bibtex_key"]
        print(f"[{i+1:3d}/{len(todo)}] {key[:35]:35s}  {paper['title'][:45]}")

        row = {
            "bibtex_key": key, "title": paper["title"],
            "authors": paper["authors"], "year": paper["year"],
            "file_path": paper.get("file_path", ""),
            "timestamp": datetime.datetime.now().isoformat(),
            "passage": None, "passage_tokens": None, "llm_latency_s": None,
            "suggestions": [], "rank": None, "score_at_rank": None,
            "hit_at_1": False, "hit_at_3": False, "hit_at_5": False, "hit_at_10": False,
            "reciprocal_rank": 0.0, "error": None, "chunks_used": [],
        }

        try:
            chunks = get_chunks(key)
            row["chunks_used"] = chunks

            gen = generate_passage(paper, chunks)
            row["passage"]        = gen["passage"]
            row["passage_tokens"] = gen["tokens"]
            row["llm_latency_s"]  = gen["latency_s"]

            sugg = query_quotely(gen["passage"])
            row["suggestions"] = [
                {"bibtex_key": s["bibtex_key"], "title": s["title"], "score": s["score"]}
                for s in sugg
            ]

            rank = find_rank(key, sugg)
            row["rank"] = rank
            if rank is not None:
                row["score_at_rank"]   = sugg[rank - 1]["score"]
                row["hit_at_1"]        = rank <= 1
                row["hit_at_3"]        = rank <= 3
                row["hit_at_5"]        = rank <= 5
                row["hit_at_10"]       = rank <= 10
                row["reciprocal_rank"] = round(1 / rank, 4)
                print(f"          → ✅ rang {rank}  score={row['score_at_rank']:.3f}  ({gen['latency_s']}s)")
            else:
                print(f"          → ❌ non trouvé dans top {N_SUGGESTIONS}  ({gen['latency_s']}s)")

        except Exception as e:
            row["error"] = str(e)
            print(f"          → ⚠️  ERREUR: {e}")

        # Checkpoint immédiat
        with open(RAW_FILE, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if row["passage"]:
            with open(PASSAGES_FILE, "a") as f:
                f.write(json.dumps({"bibtex_key": key, "passage": row["passage"]},
                                   ensure_ascii=False) + "\n")

    print("\n── Calcul des métriques ──────────────────────────────────")
    compute_metrics()


def compute_metrics():
    records = []
    with open(RAW_FILE) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except Exception:
                pass

    valid = [r for r in records if not r.get("error")]
    n     = len(valid)
    if n == 0:
        print("Aucun résultat valide.")
        return

    hits = lambda k: sum(1 for r in valid if r.get(f"hit_at_{k}")) / n
    found = [r for r in valid if r["rank"] is not None]

    m = {
        "n_evaluated":            n,
        "n_errors":               len(records) - n,
        "hit_at_1":               round(hits(1),  4),
        "hit_at_3":               round(hits(3),  4),
        "hit_at_5":               round(hits(5),  4),
        "hit_at_10":              round(hits(10), 4),
        "mrr":                    round(sum(r["reciprocal_rank"] for r in valid) / n, 4),
        "not_found_rate":         round(1 - len(found) / n, 4),
        "mean_rank_when_found":   round(sum(r["rank"] for r in found) / len(found), 2) if found else None,
        "mean_score_when_found":  round(sum(r["score_at_rank"] for r in found) / len(found), 4) if found else None,
        "mean_llm_latency_s":     round(sum(r["llm_latency_s"] for r in valid if r["llm_latency_s"]) / n, 2),
        "timestamp":              datetime.datetime.now().isoformat(),
    }

    with open(SUMMARY_FILE, "w") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)

    print(f"\n{'═'*48}")
    print(f"  MÉTRIQUES FINALES  ({n} articles)")
    print(f"{'═'*48}")
    print(f"  Hit@1   : {m['hit_at_1']*100:5.1f}%")
    print(f"  Hit@3   : {m['hit_at_3']*100:5.1f}%")
    print(f"  Hit@5   : {m['hit_at_5']*100:5.1f}%")
    print(f"  Hit@10  : {m['hit_at_10']*100:5.1f}%")
    print(f"  MRR     : {m['mrr']:.4f}")
    print(f"  Non trouvé : {m['not_found_rate']*100:.1f}%")
    print(f"{'═'*48}")
    print(f"\nRésultats sauvegardés dans {RESULTS_DIR}/")


if __name__ == "__main__":
    run()
