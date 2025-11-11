import re, hashlib
from dataclasses import dataclass


@dataclass
class HeuristicResult:
    important: bool
    reasons: list[str]
    content_hash: str
    pre_score: float


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def run_heuristics(
    text: str,
    sender_id: int,
    mentioned: bool,
    reactions: int,
    replies: int,
    vip: set[int],
    keywords: list[str],
    react_thr: int,
    reply_thr: int,
) -> HeuristicResult:
    reasons, score = [], 0.0
    if mentioned:
        reasons.append("mention")
        score += 1.0
    if sender_id in vip:
        reasons.append("vip")
        score += 0.8
    if reactions >= max(react_thr, 0) > 0:
        reasons.append("reactions")
        score += 0.4
    if replies >= max(reply_thr, 0) > 0:
        reasons.append("replies")
        score += 0.4
    kw_hit = False
    if keywords and text:
        rx = re.compile(r"|".join(map(re.escape, keywords)), re.I)
        if rx.search(text):
            reasons.append("keywords")
            score += 0.6
            kw_hit = True
    return HeuristicResult(
        important=bool(reasons),
        reasons=reasons,
        content_hash=content_hash(text or ""),
        pre_score=score,
    )
