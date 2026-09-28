"""rateyourdj-rank: stage 3 ranking evaluation.

  build-positives   ListenBrainz similar-recordings of the seeds -> eval/weak_positives_v1.json
  build-hidden-positives  two-hop co-listens (stage 5, eval only) -> eval/hidden_positives_v1.json
  eval              rel-only vs tail mix (--with-agent adds the DeepSeek ReAct agent) -> runs/ranking-v1/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rateyourdj.config import load_dotenv
from rateyourdj.data_pipeline.catalog import read_jsonl
from rateyourdj.data_pipeline.user_context import load_user_context
from rateyourdj.experiment import build_run_manifest, write_run_manifest


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="rateyourdj-rank")
    parser.add_argument("--user-id", default="participant_001")
    parser.add_argument("--catalog-root", default="data/catalog")
    parser.add_argument("--users-root", default="data/users")
    parser.add_argument("--positives", default="eval/weak_positives_v1.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-positives")
    hp = sub.add_parser("build-hidden-positives")
    hp.add_argument("--out", default="eval/hidden_positives_v1.json")
    hp.add_argument("--hop1-expand", type=int, default=40)
    ev = sub.add_parser("eval")
    ev.add_argument("--queries", default="eval/retrieval_queries_v1.jsonl")
    ev.add_argument("--run-dir", default="runs/ranking-v1")
    ev.add_argument("--with-agent", action="store_true", help="also run the DeepSeek ReAct agent (API cost)")
    args = parser.parse_args(argv)

    songs = read_jsonl(Path(args.catalog_root) / "processed" / "songs.jsonl")
    context = load_user_context(args.user_id, args.users_root, legacy_root=None)

    if args.command == "build-positives":
        from rateyourdj.data_pipeline.sources import ListenBrainzClient

        from .positives import build_weak_positives
        out = build_weak_positives(context, songs, ListenBrainzClient(Path(args.catalog_root) / "raw"))
        Path(args.positives).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", "utf-8")
        for bid, b in out["branches"].items():
            print(f"{bid}: returned {b['returned']}, in catalog {b['in_catalog']}, buckets {b['buckets']}")
        return 0

    if args.command == "build-hidden-positives":
        from rateyourdj.data_pipeline.sources import ListenBrainzClient

        from .positives import build_two_hop_positives
        out = build_two_hop_positives(context, songs, ListenBrainzClient(Path(args.catalog_root) / "raw"),
                                      hop1_expand=args.hop1_expand)
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(f"api calls: {out['api_calls']}")
        for bid, b in out["branches"].items():
            hop = {h: sum(1 for v in b["hop"].values() if v == h) for h in (1, 2)}
            print(f"{bid}: in catalog {b['in_catalog']} (hop1 {hop[1]}, hop2 {hop[2]}), buckets {b['buckets']}")
        print(f"wrote {args.out}")
        return 0

    from rateyourdj.agent.factory import build_recommender, vector_similarity
    from rateyourdj.agent.llm import OpenAICompatibleChat

    from .evaluate import evaluate, frozen_context, write_outputs

    positives = json.loads(Path(args.positives).read_text("utf-8"))
    ctx = frozen_context(context)
    service = build_recommender(catalog_root=args.catalog_root, users_root=args.users_root, use_llm=False)
    retriever = service.retriever_factory(songs, ctx)
    if retriever.matrix is None:
        print("eval needs the vector index (rateyourdj-rag build-index)", file=sys.stderr)
        return 2
    llm = OpenAICompatibleChat.from_env() if args.with_agent else None
    if args.with_agent and llm is None:
        print("--with-agent needs DEEPSEEK_API_KEY (or AGENT_LLM_*)", file=sys.stderr)
        return 2
    by_id = {s["song_id"]: s for s in songs}
    seed_artists = {by_id[s].get("artist_credit") or "" for b in ctx["seed_branches"] for s in b["seed_song_ids"]}
    queries = [json.loads(line) for line in open(args.queries, encoding="utf-8") if line.strip()]
    report = evaluate(queries, songs=songs, context=ctx, retriever=retriever, positives=positives,
                      similarity=vector_similarity(retriever), seed_artists=seed_artists, llm=llm)
    info = {"index_version": retriever.index.version, "query_count": len(queries), "queries": args.queries,
            "with_agent": bool(llm), "llm": llm.name if llm else None}
    run_dir = Path(args.run_dir)
    write_outputs(report, run_dir, info)
    write_run_manifest(run_dir, build_run_manifest(
        run_dir.name, kind="ranking_eval", config=info, data_files=[args.queries, args.positives],
        model={"embedding": retriever.index.manifest["model"], "llm": info["llm"]},
        metrics={"summary": report["summary"], "acceptance": report["acceptance"]}))
    for system, s in report["summary"].items():
        print(system, json.dumps({k: s[k] for k in ("recall@10", "ndcg@10", "tail_exposure", "mean_relevance",
                                                   "serendipity", "hallucination_rate", "constraint_pass")},
                                 ensure_ascii=False))
    print("acceptance", json.dumps(report["acceptance"], ensure_ascii=False))
    print(f"wrote {run_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
