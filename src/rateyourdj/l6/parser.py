from __future__ import annotations

import re

from .models import AgentRequest


GENRE_ALIASES = {
    "英伦独立摇滚": "british indie rock",
    "英伦摇滚": "british",
    "独立摇滚": "indie rock",
    "british indie rock": "british indie rock",
    "uk indie rock": "british indie rock",
    "british indie": "british indie",
    "uk indie": "british indie",
    "indie rock": "indie rock",
    "indie": "indie",
    "摇滚": "rock",
    "rock": "rock",
    "爵士": "jazz",
    "jazz": "jazz",
    "流行": "pop",
    "pop": "pop",
    "灵魂": "soul",
    "soul": "soul",
    "民谣": "folk",
    "folk": "folk",
    "电子": "electronic",
    "electronic": "electronic",
    "朋克": "punk",
    "punk": "punk",
    "金属": "metal",
    "metal": "metal",
    "放克": "funk",
    "funk": "funk",
    "乡村": "country",
    "country": "country",
    "蓝调": "blues",
    "blues": "blues",
    "古典": "classical",
    "classical": "classical",
    "氛围": "ambient",
    "ambient": "ambient",
}

_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}


def parse_agent_request(query: str, *, default_top_k: int = 10) -> AgentRequest:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    normalized_query = " ".join(query.strip().split())
    lowered = normalized_query.casefold()
    intent = (
        "more"
        if any(
            marker in lowered
            for marker in (
                "换一批",
                "再来",
                "more",
                "another batch",
                "还是不够像",
                "更像",
                "类似",
                "差不多像",
            )
        )
        else "recommend"
    )
    top_k = _parse_count(normalized_query, default_top_k)
    max_per_artist = (
        1
        if any(
            marker in normalized_query.casefold()
            for marker in (
                "多样",
                "不同歌手",
                "不要重复歌手",
                "每位歌手最多一首",
                "每个歌手最多一首",
                "diverse",
                "one per artist",
            )
        )
        else 2
    )
    min_score = (
        0.3
        if "高度相似" in lowered
        else 0.1
        if any(marker in lowered for marker in ("相似", "差不多", "类似"))
        else 0.0
    )
    preferences: list[str] = []
    exclusions = _parse_exclusions(normalized_query)
    referenced_exclusion = _referenced_artist_exclusion(normalized_query)
    if referenced_exclusion and referenced_exclusion not in exclusions:
        exclusions.append(referenced_exclusion)
    exclusions = [
        term
        for term in exclusions
        if term not in {"这个乐队", "这个歌手", "该乐队", "该歌手"}
        and not _is_seen_song_reference(term)
    ]

    for alias, term in GENRE_ALIASES.items():
        start = lowered.find(alias.casefold())
        if start < 0:
            continue
        prefix = lowered[max(0, start - 5):start]
        destination = (
            exclusions
            if any(marker in prefix for marker in ("不要", "排除", "避开", "not "))
            else preferences
        )
        if term not in destination:
            destination.append(term)

    reference_artists = _parse_reference_artists(normalized_query)
    avoid_artists = _parse_avoid_artists(normalized_query)

    return AgentRequest(
        query=normalized_query,
        top_k=top_k,
        max_per_artist=max_per_artist,
        min_retrieval_score=min_score,
        preference_terms=preferences,
        exclude_terms=exclusions,
        reference_artists=reference_artists,
        avoid_artists=avoid_artists,
        refinement_notes=[],
        intent=intent,
        exclude_seen=intent == "more",
    )


def _parse_count(query: str, default: int) -> int:
    digit_match = re.search(r"(\d{1,2})\s*(?:首|首歌|songs?)", query, re.I)
    if digit_match:
        return max(1, min(int(digit_match.group(1)), 50))

    chinese_match = re.search(
        r"(二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*首",
        query,
    )
    if chinese_match:
        return _CHINESE_NUMBERS[chinese_match.group(1)]
    return default


def _parse_exclusions(query: str) -> list[str]:
    terms: list[str] = []
    quoted_pattern = r"(?:不要|排除|避开)\s*[\"“']([^\"”']+)[\"”']"
    for match in re.finditer(quoted_pattern, query, re.I):
        term = " ".join(match.group(1).strip().casefold().split())
        if term and term not in terms:
            terms.append(term)

    unquoted_pattern = (
        r"(?:不要|排除|避开)\s*"
        r"(?![\"“'])"
        r"([^，。！？,;；]+)"
    )
    for match in re.finditer(unquoted_pattern, query, re.I):
        term = " ".join(match.group(1).strip().casefold().split())
        if (
            term
            and term not in {"重复歌手", "相同歌手"}
            and term not in terms
        ):
            terms.append(term)

    negative_artist_patterns = (
        r"(?:不是|非)\s*"
        r"([A-Za-z0-9][A-Za-z0-9 .&'_-]*?)"
        r"\s*的(?:歌|歌曲|音乐)",
        r"(?:别放|别要|不想听)\s*"
        r"([A-Za-z0-9][A-Za-z0-9 .&'_-]*?)"
        r"(?=[，。！？,;；]|$)",
        r"(?:不想要|不要)\s*"
        r"([A-Za-z0-9][A-Za-z0-9 .&'_-]*?)"
        r"\s*(?:这种|这样的|这类)",
    )
    for pattern in negative_artist_patterns:
        for match in re.finditer(pattern, query, re.I):
            term = " ".join(match.group(1).strip().casefold().split())
            if term and term not in terms:
                terms.append(term)
    return terms


def _referenced_artist_exclusion(query: str) -> str | None:
    patterns = (
        r"(?:和|跟|像)\s*([A-Za-z0-9][A-Za-z0-9 .&'_-]*?)"
        r"\s*(?:差不多|相似|类似).*?"
        r"(?:不要|排除|避开)\s*(?:这个|该)(?:乐队|歌手)",
        r"(?:similar to|like)\s+([A-Za-z0-9][A-Za-z0-9 .&'_-]*?)"
        r"\s*(?:but|,).*?(?:not|exclude|avoid)\s+"
        r"(?:them|that artist|that band)",
    )
    for pattern in patterns:
        match = re.search(pattern, query, re.I)
        if not match:
            continue
        term = " ".join(match.group(1).strip(" ,，。").casefold().split())
        if term:
            return term
    return None


def _parse_reference_artists(query: str) -> list[str]:
    artists: list[str] = []
    patterns = (
        r"(?:像|更像|类似|差不多像)\s*([A-Za-z0-9][A-Za-z0-9 /&'._-]*?)(?=$|[，。！？,;；]|\s*(?:这样|这样的|这种|这类|一样|的))",
        r"(?:like|similar to|more like)\s+([A-Za-z0-9][A-Za-z0-9 /&'._-]*?)(?=$|[，。！？,;；])",
        r"(?:最好是|最好来点)\s*([A-Za-z0-9][A-Za-z0-9 /&'._-]*?)\s*(?:这样|这样的|这种|这类)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, query, re.I):
            artists.extend(_split_artist_list(match.group(1)))
    return _unique_normalized_artists(artists)


def _parse_avoid_artists(query: str) -> list[str]:
    artists: list[str] = []
    patterns = (
        r"(?:不要|别要|不想要)\s*([A-Za-z0-9][A-Za-z0-9 /&'._-]*?)\s*(?:这种|这样的|这类)",
        r"(?:not|avoid|exclude)\s+([A-Za-z0-9][A-Za-z0-9 /&'._-]*?)(?=$|[，。！？,;；])",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, query, re.I):
            artists.extend(_split_artist_list(match.group(1)))
    return _unique_normalized_artists(artists)


def _split_artist_list(value: str) -> list[str]:
    parts = re.split(r"\s*(?:/|,|，|、| and |&)\s*", value, flags=re.I)
    result: list[str] = []
    for part in parts:
        artist = " ".join(part.strip().casefold().split())
        if artist and artist not in {"like", "similar to", "more like"}:
            result.append(artist)
    return result


def _unique_normalized_artists(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        artist = " ".join(str(value).strip().casefold().split())
        if artist and artist not in result:
            result.append(artist)
    return result


def _is_seen_song_reference(term: str) -> bool:
    normalized = " ".join(term.casefold().split())
    markers = (
        "刚才推荐过",
        "刚刚推荐过",
        "之前推荐过",
        "上次推荐过",
        "刚才那些",
        "刚刚那些",
        "之前那些",
        "already recommended",
        "shown before",
    )
    return any(marker in normalized for marker in markers)
