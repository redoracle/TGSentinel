import functools
import hashlib
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class HeuristicResult:
    important: bool
    reasons: list[str]
    content_hash: str
    pre_score: float
    trigger_annotations: dict[str, list[str]]  # New: category -> matched keywords


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# Note: Default keyword lists have been removed.
# Keywords must be explicitly configured via profiles in config/profiles.yml
# or directly on channel/user rules in config/tgsentinel.yml


@functools.lru_cache(maxsize=128)
def _compile_keywords_pattern(keywords_tuple: tuple[str, ...]) -> re.Pattern:
    """Compile and cache regex pattern for keywords tuple.

    Args:
        keywords_tuple: Immutable tuple of keywords to match

    Returns:
        Compiled regex pattern with case-insensitive flag
    """
    pattern = r"|".join(map(re.escape, keywords_tuple))
    return re.compile(pattern, re.I)


def _check_keywords(text: str, keywords: list[str]) -> bool:
    """Check if any keywords match in text (case-insensitive).

    Uses cached compiled patterns for performance.

    Args:
        text: Text to search in
        keywords: List of keywords to search for

    Returns:
        True if any keyword matches, False otherwise
    """
    if not keywords or not text:
        return False

    # Convert to tuple for caching (immutable key)
    keywords_tuple = tuple(keywords)
    pattern = _compile_keywords_pattern(keywords_tuple)
    return bool(pattern.search(text))


def _find_matched_keywords(text: str, keywords: list[str]) -> list[str]:
    """Find which keywords matched in text (for trigger annotations).

    Args:
        text: Text to search in
        keywords: List of keywords to search for

    Returns:
        List of matched keywords (original case from keyword list)
    """
    if not keywords or not text:
        return []

    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _detect_code_patterns(text: str) -> bool:
    """Detect code snippets in message (multi-line code blocks, not single words).

    This looks for actual programming code patterns:
    - Code fence markers (```, ~~~)
    - Consistent indentation (4+ spaces/tabs)
    - Programming language syntax (function, class, def, const, import, etc.)

    Requires at least 2-3 lines to avoid false positives on abbreviations like "EVM", "API".
    """
    if not text:
        return False

    lines = text.split("\n")

    # 1. Code fence markers (markdown code blocks)
    if re.search(r"```|~~~", text):
        return True

    # 2. Consistent indentation pattern (4+ spaces or tabs, at least 3 lines)
    indented_lines = [line for line in lines if re.match(r"^(    |\t)", line)]
    if len(indented_lines) >= 3:
        return True

    # 3. Programming syntax patterns (must have at least 2 lines + syntax keyword)
    if len(lines) >= 2:
        # Common programming keywords across languages
        programming_keywords = [
            r"\bfunction\s+\w+\s*\(",  # function declarations
            r"\bclass\s+\w+",  # class definitions
            r"\bdef\s+\w+\s*\(",  # Python functions
            r"\b(const|let|var)\s+\w+\s*=",  # JS/TS variables
            r"\bimport\s+\w+",  # imports
            r"\bfrom\s+\w+\s+import",  # Python imports
            r"\bpub\s+fn\s+\w+",  # Rust functions
            r"\bfunc\s+\w+\s*\(",  # Go functions
            r"\breturn\s+[^;]+;",  # return statements
            r"=>\s*\{",  # arrow functions
            r"\{[^}]*\}",  # code blocks (single-line and multiline)
        ]

        for pattern in programming_keywords:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL):
                return True

    return False


def _detect_question_patterns(text: str) -> bool:
    """Detect direct questions requiring action."""
    if not text:
        return False
    # Question marks with action verbs
    if "?" in text:
        if re.search(
            r"(can you|could you|will you|would you|do you|are you|when can)",
            text,
            re.I,
        ):
            return True
    return False


def _detect_url_patterns(text: str) -> bool:
    """Detect URLs in message text.

    Matches common URL patterns including:
    - http:// and https:// URLs
    - www. prefixed domains
    - Common TLDs without protocol (e.g., example.com)
    - Telegram t.me links
    """
    if not text:
        return False

    # Common URL patterns
    url_patterns = [
        r"https?://[^\s]+",  # http:// or https:// URLs
        r"www\.[^\s]+",  # www. prefixed
        r"t\.me/[^\s]+",  # Telegram links
        r"\b[a-zA-Z0-9-]+\.(com|org|net|io|dev|co|me|xyz|app|tech)[^\s]*",  # Common TLDs
    ]

    for pattern in url_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


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
    # New parameters for enhanced detection
    is_private: bool = False,
    is_reply_to_user: bool = False,
    has_media: bool = False,
    media_type: Optional[str] = None,
    is_pinned: bool = False,
    is_poll: bool = False,
    sender_is_admin: bool = False,
    has_forward: bool = False,
    # Category-specific keywords
    action_keywords: Optional[list[str]] = None,
    decision_keywords: Optional[list[str]] = None,
    urgency_keywords: Optional[list[str]] = None,
    importance_keywords: Optional[list[str]] = None,
    release_keywords: Optional[list[str]] = None,
    security_keywords: Optional[list[str]] = None,
    risk_keywords: Optional[list[str]] = None,
    opportunity_keywords: Optional[list[str]] = None,
    # Detection flags (opt-in model: must be explicitly enabled in profiles)
    detect_codes: bool = False,
    detect_documents: bool = False,
    detect_links: bool = False,
    require_forwarded: bool = False,
    prioritize_pinned: bool = False,
    prioritize_admin: bool = False,
    prioritize_private: bool = False,
    detect_polls: bool = False,
) -> HeuristicResult:
    """
    Comprehensive heuristic analysis based on 10 categories of important messages:

    1. Messages Requiring Direct Action
    2. Decisions, Voting, and Direction Changes
    3. Direct Mentions and Replies
    4. Messages With Key Importance Indicators
    5. Updates Related to Interests or Projects
    6. Messages Containing Structured or Sensitive Data
    7. Personal Context Changes (private chats)
    8. Risk or Incident-Related Messages
    9. Opportunity-Driven Messages
    10. Meta-Important Messages (Based on MTProto Metadata)
    """
    reasons, score = [], 0.0
    trigger_annotations: dict[str, list[str]] = {}  # Track which keywords triggered

    # === FILTER: Required Forward Check ===
    # If require_forwarded is True and message is not forwarded, short-circuit to zero score
    if require_forwarded and not has_forward:
        return HeuristicResult(
            important=False,
            reasons=["filtered-no-forward"],
            content_hash=content_hash(text or ""),
            pre_score=0.0,
            trigger_annotations={},
        )

    # === CATEGORY 3: Direct Mentions and Replies (HIGHEST PRIORITY) ===
    if mentioned:
        reasons.append("mention")
        score += 2.0  # Increased from 1.0 - highest priority

    if is_reply_to_user:
        reasons.append("reply-to-you")
        score += 1.5

    # === CATEGORY 10: Meta-Important Messages ===
    if is_pinned and prioritize_pinned:
        reasons.append("pinned")
        score += 1.2

    if sender_is_admin and prioritize_admin:
        reasons.append("admin")
        score += 0.9

    if is_poll and detect_polls:
        reasons.append("poll")
        score += 1.0

    # === CATEGORY 1: Messages Requiring Direct Action ===
    # Private chats have higher priority for action requests (opt-in)
    if is_private and prioritize_private:
        reasons.append("private-chat")
        score += 0.5  # Base boost for private messages

        # Direct questions in private chats
        if _detect_question_patterns(text):
            reasons.append("direct-question")
            score += 1.2

    # Action keywords detection (only if keywords are configured)
    action_kw = action_keywords if action_keywords else []
    matched = _find_matched_keywords(text, action_kw)
    if matched:
        reasons.append("action-required")
        score += 1.0 if is_private else 0.8
        trigger_annotations["action"] = matched

    # === CATEGORY 2: Decisions, Voting, and Direction Changes ===
    decision_kw = decision_keywords if decision_keywords else []
    matched = _find_matched_keywords(text, decision_kw)
    if matched:
        reasons.append("decision")
        score += 1.1
        trigger_annotations["decision"] = matched

    # === CATEGORY 4: Urgency & Importance Indicators ===
    urgency_kw = urgency_keywords if urgency_keywords else []
    matched = _find_matched_keywords(text, urgency_kw)
    if matched:
        reasons.append("urgent")
        score += 1.5  # High priority
        trigger_annotations["urgency"] = matched

    importance_kw = importance_keywords if importance_keywords else []
    matched = _find_matched_keywords(text, importance_kw)
    if matched:
        reasons.append("important")
        score += 0.9
        trigger_annotations["importance"] = matched

    # === CATEGORY 5: Project & Interest Updates ===
    release_kw = release_keywords if release_keywords else []
    matched = _find_matched_keywords(text, release_kw)
    if matched:
        reasons.append("release")
        score += 0.8
        trigger_annotations["release"] = matched

    security_kw = security_keywords if security_keywords else []
    matched = _find_matched_keywords(text, security_kw)
    if matched:
        reasons.append("security")
        score += 1.2  # Security is high priority
        trigger_annotations["security"] = matched

    # === CATEGORY 6: Structured or Sensitive Data ===
    if detect_codes and _detect_code_patterns(text):
        reasons.append("code-detected")
        score += 1.3  # OTP/codes are very important

    if detect_documents and has_media and media_type:
        # Treat any media type as potentially important
        # Common types: document, photo, voice, video_note, video, audio, sticker, animation, video_message
        reasons.append(f"media-{media_type}")
        score += 0.7

    # === URL/Link Detection ===
    if detect_links and _detect_url_patterns(text):
        reasons.append("contains-link")
        score += 0.5

    # === CATEGORY 8: Risk or Incident Messages ===
    risk_kw = risk_keywords if risk_keywords else []
    matched = _find_matched_keywords(text, risk_kw)
    if matched:
        reasons.append("risk")
        score += 1.0
        trigger_annotations["risk"] = matched

    # === CATEGORY 9: Opportunity Messages ===
    opportunity_kw = opportunity_keywords if opportunity_keywords else []
    matched = _find_matched_keywords(text, opportunity_kw)
    if matched:
        reasons.append("opportunity")
        score += 0.6
        trigger_annotations["opportunity"] = matched

    # === VIP Senders ===
    if sender_id in vip:
        reasons.append("vip")
        score += 1.0

    # === Engagement Thresholds ===
    if reactions >= max(react_thr, 0) > 0:
        reasons.append("reactions")
        score += 0.5

    if replies >= max(reply_thr, 0) > 0:
        reasons.append("replies")
        score += 0.5

    # === Custom Keywords ===
    matched = _find_matched_keywords(text, keywords) if keywords else []
    if matched:
        reasons.append("keywords")
        score += 0.8
        trigger_annotations["keywords"] = matched

    # === Personal Context (Rare Senders) ===
    # This would require tracking message frequency per sender
    # Could be implemented in worker.py by checking last message timestamp

    return HeuristicResult(
        important=bool(reasons),
        reasons=reasons,
        content_hash=content_hash(text or ""),
        pre_score=score,
        trigger_annotations=trigger_annotations,
    )
