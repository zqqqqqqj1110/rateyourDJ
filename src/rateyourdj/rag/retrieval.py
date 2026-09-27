"""Multi-channel retrieval over the catalog -> RetrievalCandidateV2 records.

Relevance is *hybrid*: the mean of the dense percentile (bge-m3 cosine to the
query vector) and the tag percentile (tag-profile cosine), so neither a title
word collision in vector space nor a coarse tag match alone can dominate.
Seed songs / seed artists named in the request ("像 Time 那样") are resolved to
their vectors and removed from the text before encoding.

Channels (each with a quota that depends on the exploration level):
  semantic  most relevant songs (hybrid)
  tail      closest *tail* songs, only above a minimum relevance percentile
  explore   moderately related mid/tail songs, spread out, one per artist
  rule      tag overlap with the branch tag profile + tags named in the request
            (no vectors needed; also the fallback when there is no index)

Every candidate is a real catalog song and carries evidence that points back to
catalog facts or seed songs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from rateyourdj.contracts import validate_record
from rateyourdj.contracts.v2 import SCHEMA_VERSIONS

from .encoder import Encoder
from .index import VectorIndex

# Chinese request words -> catalog tags (English tags are matched directly).
ZH_TAG_HINTS = {
    "迷幻": ["psychedelic rock", "psychedelic"], "前卫": ["progressive rock"],
    "艺术摇滚": ["art rock"], "太空": ["space rock"], "英伦": ["britpop", "british"],
    "另类": ["alternative rock"], "独立": ["indie rock", "indie"], "朋克": ["punk", "punk rock"],
    "民谣": ["folk", "folk rock"], "氛围": ["ambient"], "电子": ["electronic"],
    "硬摇滚": ["hard rock"], "旋律": ["pop rock", "jangle pop"], "盯鞋": ["shoegaze"],
    "后摇": ["post-rock"], "蓝调": ["blues rock", "blues"], "经典摇滚": ["classic rock"],
    "爵士": ["jazz"], "流行": ["pop"], "金属": ["heavy metal", "metal"],
}
DEFAULTS = {
    "limit": 30,
    "min_tail_relevance": 0.85,      # tail songs must be in the top 15% by relevance
    "explore_band": (0.60, 0.85),    # explore: related but not the obvious picks
    "artist_cap_per_channel": 2,
    "artist_cap_total": 3,
    "text_weight": 0.5,              # query = 0.5 * request text + 0.5 * interest centre
    "reference_weight": 0.35,        # when seeds are named: 0.35 text + 0.35 named seeds + 0.3 centre
    "dense_weight": 0.5,             # hybrid relevance = 0.5 dense percentile + 0.5 tag percentile
    "branch_temperature": 0.05,
    "channel_depth": 60,             # how deep a song may sit in a channel list to count
}


def quotas(limit: int, exploration: float, has_index: bool) -> dict[str, int]:
    e = min(1.0, max(0.0, exploration))
    tail = round(limit * (0.2 + 0.3 * e))
    explore = round(limit * 0.2 * e)
    rule = round(limit * 0.2)
    semantic = max(0, limit - tail - explore - rule)
    if not has_index:
        rule, semantic = rule + semantic, 0
    return {"tail": tail, "explore": explore, "semantic": semantic, "rule": rule}


def _percentiles(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    pct = np.empty(len(values))
    pct[order] = np.arange(len(values)) / max(1, len(values) - 1)
    return pct


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _tag_weights(song: dict[str, Any]) -> dict[str, float]:
    return {t["name"]: float(t.get("weight") or 0.0) for t in song.get("tags", []) if t.get("name")}


def _tag_cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(w * b[t] for t, w in a.items() if t in b)
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class Retriever:
    def __init__(self, songs: list[dict[str, Any]], context: dict[str, Any], *,
                 index: VectorIndex | None = None, encoder: Encoder | None = None,
                 catalog_version: str = "unknown", config: dict[str, Any] | None = None) -> None:
        self.config = {**DEFAULTS, **(config or {})}
        self.context = context
        self.index = index if index is not None and len(index.ids) else None
        self.encoder = encoder
        self.catalog_version = catalog_version
        by_id = {s["song_id"]: s for s in songs}
        self.ids = [i for i in self.index.ids if i in by_id] if self.index else list(by_id)
        self.songs = [by_id[i] for i in self.ids]
        self.row = {sid: r for r, sid in enumerate(self.ids)}
        self.matrix = (self.index.matrix[[self.index.row[i] for i in self.ids]]
                       if self.index else None)
        self.buckets = np.array([s["popularity"]["bucket"] for s in self.songs])
        self.tail_scores = np.array([
            0.5 if s["popularity"].get("global_percentile") is None
            else 1.0 - float(s["popularity"]["global_percentile"]) for s in self.songs])
        self.artist_keys = [
            next((a["mbid"] for a in s.get("artists", []) if a.get("mbid")), None)
            or (s.get("artist_credit") or "").lower() for s in self.songs]
        self.tags = [_tag_weights(s) for s in self.songs]
        self.vocab = {t for tw in self.tags for t in tw if len(t) >= 4}
        self.seed_ids = [sid for b in context["seed_branches"] for sid in b["seed_song_ids"]
                         if sid in self.row]
        self.branch_ids = [b["branch_id"] for b in context["seed_branches"]]
        from rateyourdj.data_pipeline.sources import split_title
        self.references: list[tuple[re.Pattern[str], list[str], str]] = []
        for b in context["seed_branches"]:
            artists: set[str] = set()
            for sid in b["seed_song_ids"]:
                if sid not in self.row:
                    continue
                seed = self.songs[self.row[sid]]
                base = split_title(seed.get("title") or "")[0].strip()
                if len(base) >= 3:
                    self.references.append((self._pattern(base), [sid], b["branch_id"]))
                if seed.get("artist_credit"):
                    artists.add(seed["artist_credit"])
            for artist in artists:
                sids = [s for s in b["seed_song_ids"] if s in self.row]
                self.references.append((self._pattern(artist), sids, b["branch_id"]))
        self.branch_tags: dict[str, dict[str, float]] = {}
        self.centres: dict[str, np.ndarray] = {}
        for b in context["seed_branches"]:
            seeds = [sid for sid in b["seed_song_ids"] if sid in self.row]
            profile: dict[str, float] = defaultdict(float)
            for sid in seeds:
                for t, w in self.tags[self.row[sid]].items():
                    profile[t] += w / max(1, len(seeds))
            self.branch_tags[b["branch_id"]] = dict(profile)
            if self.matrix is not None and seeds:
                self.centres[b["branch_id"]] = _unit(
                    self.matrix[[self.row[s] for s in seeds]].mean(axis=0))

    # ------------------------------------------------------------ query side
    @staticmethod
    def _pattern(name: str) -> re.Pattern[str]:
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I)

    def _resolve_references(self, query: str) -> tuple[str, list[str], dict[str, float]]:
        """Find seed titles / seed artists in the request; return (stripped text, seed ids, branches)."""
        text, seeds, branches = query, [], defaultdict(float)
        for pattern, sids, branch in sorted(self.references, key=lambda r: -len(r[0].pattern)):
            if pattern.search(text):
                text = pattern.sub(" ", text)
                seeds.extend(s for s in sids if s not in seeds)
                branches[branch] += 1.0
        return re.sub(r"\s+", " ", text).strip(), seeds, dict(branches)

    def _request_tags(self, query: str) -> dict[str, float]:
        text = (query or "").lower()
        found: dict[str, float] = {}
        for word, tags in ZH_TAG_HINTS.items():
            if word in text:
                for t in tags:
                    found[t] = 1.0
        for t in self.vocab:
            if t in text:
                found[t] = 1.0
        return found

    def _branch_weights(self, text_vec: np.ndarray | None, query: str,
                        branch_hint: str | None) -> dict[str, float]:
        if branch_hint:
            if branch_hint not in self.branch_ids:
                raise ValueError(f"unknown branch {branch_hint!r}; known: {self.branch_ids}")
            return {b: float(b == branch_hint) for b in self.branch_ids}
        if text_vec is not None and self.centres:
            sims = np.array([float(self.centres[b] @ text_vec) for b in self.branch_ids])
            z = np.exp((sims - sims.max()) / self.config["branch_temperature"])
            return {b: float(w) for b, w in zip(self.branch_ids, z / z.sum())}
        req = self._request_tags(query)
        if req:
            scores = np.array([_tag_cosine(self.branch_tags[b], req) for b in self.branch_ids])
            if scores.sum() > 0:
                return {b: float(s) for b, s in zip(self.branch_ids, scores / scores.sum())}
        return {b: 1.0 / len(self.branch_ids) for b in self.branch_ids}

    # ------------------------------------------------------------ retrieval
    def retrieve(self, query: str = "", *, branch_hint: str | None = None,
                 exploration_level: float | None = None, limit: int | None = None,
                 exclude_song_ids: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
        cfg = self.config
        limit = limit or cfg["limit"]
        e = self.context.get("exploration_level", 0.5) if exploration_level is None else exploration_level
        has_index = self.matrix is not None and self.encoder is not None
        quota = quotas(limit, e, has_index)

        text, ref_seeds, ref_branches = self._resolve_references(query)
        text_vec = None
        if has_index and re.search(r"\w", text):
            text_vec = _unit(self.encoder.encode([text])[0])
        if ref_branches and not branch_hint:
            total = sum(ref_branches.values())
            weights = {b: ref_branches.get(b, 0.0) / total for b in self.branch_ids}
        else:
            weights = self._branch_weights(text_vec, text, branch_hint)
        req_tags = self._request_tags(query)
        query_tags: dict[str, float] = defaultdict(float)
        for b, w in weights.items():
            for t, tw in self.branch_tags[b].items():
                query_tags[t] += w * tw
        for t in req_tags:
            query_tags[t] += 1.0

        rule_scores = np.array([_tag_cosine(tw, query_tags) for tw in self.tags])
        tag_pct = _percentiles(rule_scores)
        if has_index:
            interest = _unit(sum(w * self.centres[b] for b, w in weights.items() if b in self.centres))
            parts = [(1.0, interest)]
            if ref_seeds:
                ref_vec = _unit(self.matrix[[self.row[s] for s in ref_seeds]].mean(axis=0))
                rw = cfg["reference_weight"]
                parts = [(1 - 2 * rw, interest), (rw, ref_vec)] + ([(rw, text_vec)] if text_vec is not None else [])
            elif text_vec is not None:
                parts = [(1 - cfg["text_weight"], interest), (cfg["text_weight"], text_vec)]
            qvec = _unit(sum(w * v for w, v in parts))
            dense = self.matrix @ qvec
            dw = cfg["dense_weight"]
            base = dw * _percentiles(dense) + (1 - dw) * tag_pct
        else:
            base = rule_scores
        rel_pct = _percentiles(base)

        excluded = set(self.seed_ids) | set(exclude_song_ids)
        excluded |= set(self.context.get("heard_song_ids", []))
        excluded |= set(self.context.get("recommended_song_ids", []))
        excluded |= set(self.context.get("exclusions", {}).get("song_ids", []))
        banned_artists = set(self.context.get("exclusions", {}).get("artists_mbid", []))
        ok = np.array([sid not in excluded and self.artist_keys[r] not in banned_artists
                       for r, sid in enumerate(self.ids)])

        by_base = [int(r) for r in np.argsort(-base, kind="stable") if ok[r]]
        lo, hi = cfg["explore_band"]
        explore_pool = [r for r in by_base if self.buckets[r] in ("mid", "tail") and lo <= rel_pct[r] < hi]
        if quota["explore"] and explore_pool:
            stride = max(1, len(explore_pool) // (quota["explore"] * 4))
            explore_pool = explore_pool[::stride]
        channels = {
            "tail": [r for r in by_base if self.buckets[r] == "tail"
                     and rel_pct[r] >= cfg["min_tail_relevance"]],
            "explore": explore_pool,
            "semantic": by_base if has_index else [],
            "rule": [int(r) for r in np.argsort(-rule_scores, kind="stable") if ok[r] and rule_scores[r] > 0],
        }

        chosen: list[int] = []
        picked_by: dict[int, str] = {}
        per_artist: dict[str, int] = defaultdict(int)
        for name in ("tail", "explore", "semantic", "rule"):
            cap = 1 if name == "explore" else cfg["artist_cap_per_channel"]
            in_channel: dict[str, int] = defaultdict(int)
            for r in channels[name]:
                if sum(1 for x in chosen if picked_by[x] == name) >= quota[name]:
                    break
                a = self.artist_keys[r]
                if r in picked_by or in_channel[a] >= cap or per_artist[a] >= cfg["artist_cap_total"]:
                    continue
                chosen.append(r)
                picked_by[r] = name
                in_channel[a] += 1
                per_artist[a] += 1
        backfill = channels["semantic"] or channels["rule"]
        for r in backfill:  # channels ran dry (e.g. few tail songs): top up by relevance
            if len(chosen) >= limit:
                break
            a = self.artist_keys[r]
            if r not in picked_by and per_artist[a] < cfg["artist_cap_total"]:
                chosen.append(r)
                picked_by[r] = "semantic" if has_index else "rule"
                per_artist[a] += 1

        depth = cfg["channel_depth"]
        ranks = {name: {r: i for i, r in enumerate(lst[: max(depth, quota[name] * 3)])}
                 for name, lst in channels.items()}
        raw = {"semantic": base, "tail": base, "explore": base, "rule": rule_scores}
        candidate_set_id = "cs_" + hashlib.sha1(json.dumps({
            "q": query, "b": branch_hint, "e": round(float(e), 3), "l": limit,
            "x": sorted(exclude_song_ids), "i": self.index.version if self.index else None,
            "c": self.catalog_version, "cfg": {k: v for k, v in cfg.items()}}, sort_keys=True,
            default=str).encode()).hexdigest()[:12]
        request_id = "req_" + candidate_set_id[3:]

        candidates = []
        for r in chosen:
            song = self.songs[r]
            members = [{"channel": picked_by[r], "rank": ranks[picked_by[r]].get(r, -1),
                        "raw_score": round(float(raw[picked_by[r]][r]), 4)}]
            for name in ("semantic", "tail", "explore", "rule"):
                if name != picked_by[r] and r in ranks[name]:
                    members.append({"channel": name, "rank": ranks[name][r],
                                    "raw_score": round(float(raw[name][r]), 4)})
            calibrated = [1 - m["rank"] / max(1, len(ranks[m["channel"]]))
                          for m in members if m["rank"] >= 0] or [0.0]
            record = {
                "schema_version": SCHEMA_VERSIONS["retrieval_candidate"],
                "request_id": request_id,
                "candidate_set_id": candidate_set_id,
                "song_id": song["song_id"],
                "title": song.get("title"),
                "artist_credit": song.get("artist_credit"),
                "bucket": str(self.buckets[r]),
                "channels": members,
                "fused_score": round(min(1.0, max(calibrated) + 0.05 * (len(members) - 1)), 4),
                "relevance": round(float(rel_pct[r]), 4),
                "tail_score": round(float(self.tail_scores[r]), 4),
                "evidence": self._evidence(r, query_tags, req_tags),
                "index_version": self.index.version if self.index else None,
                "branch_affinity": self._affinity(r),
            }
            validate_record("retrieval_candidate", record)
            candidates.append(record)
        counts: dict[str, int] = defaultdict(int)
        for r in chosen:
            counts[picked_by[r]] += 1
        return {
            "candidate_set_id": candidate_set_id,
            "request_id": request_id,
            "query": {"text": query, "encoded_text": text, "referenced_seeds": ref_seeds,
                      "branch_hint": branch_hint, "exploration_level": e,
                      "limit": limit, "branch_weights": {k: round(v, 4) for k, v in weights.items()},
                      "request_tags": sorted(req_tags)},
            "index_version": self.index.version if self.index else None,
            "catalog_version": self.catalog_version,
            "quota": quota,
            "channel_counts": dict(counts),
            "fallback": None if has_index else "rule_only",
            "candidates": candidates,
        }

    # ------------------------------------------------------------ evidence
    def _affinity(self, r: int) -> dict[str, float]:
        if self.matrix is not None and self.centres:
            return {b: round(float(self.matrix[r] @ c), 4) for b, c in self.centres.items()}
        return {b: round(_tag_cosine(self.tags[r], p), 4) for b, p in self.branch_tags.items()}

    def _evidence(self, r: int, query_tags: dict[str, float], req_tags: dict[str, float]
                  ) -> list[dict[str, str]]:
        song = self.songs[r]
        evidence: list[dict[str, str]] = []
        if self.matrix is not None and self.seed_ids:
            sims = [(float(self.matrix[r] @ self.matrix[self.row[s]]), s) for s in self.seed_ids]
            best, seed = max(sims)
            evidence.append({"type": "seed_similarity",
                             "detail": f"与种子《{self.songs[self.row[seed]]['title']}》的向量相似度 {best:.2f}",
                             "ref": seed})
        shared = sorted((t for t in self.tags[r] if t in query_tags),
                        key=lambda t: -(self.tags[r][t] * query_tags[t]))[:3]
        if shared:
            evidence.append({"type": "shared_tag", "detail": "风格标签：" + "、".join(shared),
                             "ref": "tags:" + song["song_id"]})
        matched = [t for t in self.tags[r] if t in req_tags][:3]
        if matched:
            evidence.append({"type": "request_match", "detail": "符合请求中的风格：" + "、".join(matched),
                             "ref": "request"})
        pop = song["popularity"]
        pct = pop.get("global_percentile")
        evidence.append({"type": "popularity",
                         "detail": (f"ListenBrainz 听众 {pop.get('listener_count')}，"
                                    f"全网热度百分位 {pct:.2f}（{pop['bucket']}）" if pct is not None
                                    else f"ListenBrainz 听众 {pop.get('listener_count')}（{pop['bucket']}）"),
                         "ref": "listenbrainz:" + song["song_id"]})
        return evidence


def save_candidate_set(result: dict[str, Any], root: str | Path = "data/candidates") -> Path:
    path = Path(root) / f"{result['candidate_set_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return path
