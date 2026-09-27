"""rateyourdj-rag: stage 2 retrieval commands.

  build-index   encode every catalog song with bge-m3 -> data/index/<version>/
  retrieve      run one retrieval and print the candidates
  eval          rule-only vs RAG on eval/retrieval_queries_v1.jsonl -> runs/retrieval-v1/
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

from .encoder import DEFAULT_MODEL
from .evaluate import Judges, render_markdown, run_evaluation
from .index import build_index, load_index
from .retrieval import Retriever, save_candidate_set


def _catalog_version(catalog_root: Path) -> str:
    manifest = catalog_root / "manifest.json"
    return json.loads(manifest.read_text("utf-8"))["catalog_version"] if manifest.is_file() else "unknown"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="rateyourdj-rag", description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", default="participant_001")
    parser.add_argument("--catalog-root", default="data/catalog")
    parser.add_argument("--users-root", default="data/users")
    parser.add_argument("--index-root", default="data/index")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", help="mps / cuda / cpu (default: auto)")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-index")
    build.add_argument("--batch-size", type=int, default=32)
    ret = sub.add_parser("retrieve")
    ret.add_argument("text", nargs="?", default="")
    ret.add_argument("--branch")
    ret.add_argument("--exploration", type=float)
    ret.add_argument("--limit", type=int, default=30)
    ret.add_argument("--no-index", action="store_true", help="rule-only fallback")
    ret.add_argument("--save", action="store_true", help="save the candidate set under data/candidates/")
    ev = sub.add_parser("eval")
    ev.add_argument("--queries", default="eval/retrieval_queries_v1.jsonl")
    ev.add_argument("--run-dir", default="runs/retrieval-v1")
    args = parser.parse_args(argv)

    catalog_root = Path(args.catalog_root)
    songs = read_jsonl(catalog_root / "processed" / "songs.jsonl")
    catalog_version = _catalog_version(catalog_root)

    def encoder():
        from .encoder import SentenceTransformerEncoder
        enc = SentenceTransformerEncoder(args.model, device=args.device)
        print(f"[encoder] {enc.name} on {enc.device}, dim {enc.dim}", file=sys.stderr)
        return enc

    if args.command == "build-index":
        index = build_index(songs, encoder(), catalog_version=catalog_version,
                            root=args.index_root, batch_size=args.batch_size)
        print(json.dumps(index.manifest, indent=2))
        return 0

    context = load_user_context(args.user_id, args.users_root, legacy_root=None)
    index = None if getattr(args, "no_index", False) else load_index(args.index_root)
    if index is None and not getattr(args, "no_index", False):
        print("[retrieve] no index found: falling back to rule-only (run build-index first)", file=sys.stderr)

    if args.command == "retrieve":
        retriever = Retriever(songs, context, index=index, encoder=encoder() if index else None,
                              catalog_version=catalog_version)
        result = retriever.retrieve(args.text, branch_hint=args.branch,
                                    exploration_level=args.exploration, limit=args.limit)
        print(json.dumps({k: v for k, v in result.items() if k != "candidates"}, ensure_ascii=False, indent=1))
        for i, c in enumerate(result["candidates"], 1):
            print(f"{i:>2}. [{c['channels'][0]['channel']:<8} {c['bucket']:<4}] {c['artist_credit']} - {c['title']}"
                  f"  rel={c['relevance']:.2f} tail={c['tail_score']:.2f}")
            for ev in c["evidence"]:
                print(f"      · {ev['detail']}")
        if args.save:
            print(f"saved {save_candidate_set(result)}")
        return 0

    if args.command == "eval":
        if index is None:
            print("eval needs the vector index; run build-index first", file=sys.stderr)
            return 2
        enc = encoder()
        rag = Retriever(songs, context, index=index, encoder=enc, catalog_version=catalog_version)
        rule = Retriever(songs, context, index=None, catalog_version=catalog_version)
        queries = [json.loads(line) for line in open(args.queries, encoding="utf-8") if line.strip()]
        seed_ids = {sid for b in context["seed_branches"] for sid in b["seed_song_ids"]}
        report = run_evaluation(queries, {"rule_only": rule, "rag": rag}, Judges(rag),
                                {s["song_id"]: s for s in songs}, seed_ids)
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")
        info = {"index_version": index.version, "model": index.manifest["model"],
                "queries": args.queries, "query_count": len(queries)}
        (run_dir / "report.md").write_text(render_markdown(report, info), "utf-8")
        write_run_manifest(run_dir, build_run_manifest(
            run_dir.name, kind="retrieval_eval", config={**info, "catalog_version": catalog_version},
            data_files=[args.queries], model={"embedding": index.manifest["model"]},
            metrics={s: v["summary"] for s, v in report["systems"].items()}))
        for name, system in report["systems"].items():
            print(name, json.dumps(system["summary"], ensure_ascii=False))
        print(f"wrote {run_dir / 'report.md'}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
