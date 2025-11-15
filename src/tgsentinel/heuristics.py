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


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# Default keyword patterns for comprehensive detection
DEFAULT_ACTION_KEYWORDS = [
    "can you",
    "could you",
    "please",
    "need you to",
    "would you",
    "I need",
    "we need",
    "urgent request",
    "asap",
    "immediately",
    "confirm",
    "appointment",
    "meeting",
    "schedule",
    "deadline",
]

DEFAULT_DECISION_KEYWORDS = [
    "poll",
    "vote",
    "voting",
    "proposal",
    "approved",
    "rejected",
    "decision",
    "consensus",
    "agreement",
    "resolution",
    "policy change",
    "new rule",
    "updated procedure",
    "governance",
]

DEFAULT_URGENCY_KEYWORDS = [
    "urgent",
    "emergency",
    "critical",
    "immediate",
    "asap",
    "now",
    "breaking",
    "alert",
    "warning",
    "attention required",
]

DEFAULT_IMPORTANCE_KEYWORDS = [
    "important",
    "crucial",
    "essential",
    "significant",
    "must read",
    "heads up",
    "fyi",
    "please note",
    "be aware",
    "you should know",
    "update:",
    "notice:",
    "announcement:",
]

DEFAULT_RELEASE_KEYWORDS = [
    "release",
    "version",
    "update available",
    "new release",
    "changelog",
    "released",
    "launch",
    "deployment",
    "rollout",
    "new version",
    "version released",
]

DEFAULT_SECURITY_KEYWORDS = [
    "cve",
    "vulnerability",
    "exploit",
    "security",
    "breach",
    "hack",
    "compromised",
    "alert",
    "patch",
    "fix",
    "advisory",
    "zero-day",
    "malware",
    "phishing",
    "scam",
]

DEFAULT_RISK_KEYWORDS = [
    "danger",
    "risk",
    "problem",
    "issue",
    "bug",
    "error",
    "failure",
    "down",
    "outage",
    "incident",
    "complaint",
    "escalation",
    "legal",
    "lawsuit",
    "violation",
    "banned",
    "suspended",
]

DEFAULT_OPPORTUNITY_KEYWORDS = [
    "invitation",
    "invite",
    "opportunity",
    "opening",
    "position",
    "hiring",
    "job",
    "beta",
    "early access",
    "exclusive",
    "limited",
    "offer",
    "discount",
    "free",
    "giveaway",
    "contest",
]


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


def _detect_code_patterns(text: str) -> bool:
    """Detect OTP codes, passwords, tokens in message."""
    if not text:
        return False
    # OTP patterns: 6-digit codes, verification codes
    if re.search(r"\b\d{6}\b", text):
        return True
    # Common OTP/verification phrases
    if re.search(r"(verification code|otp|one[-\s]time|passcode|token)", text, re.I):
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
    # Detection flags
    detect_codes: bool = True,
    detect_documents: bool = True,
    prioritize_pinned: bool = True,
    prioritize_admin: bool = True,
    detect_polls: bool = True,
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
    # Private chats have higher priority for action requests
    if is_private:
        reasons.append("private-chat")
        score += 0.5  # Base boost for private messages

        # Direct questions in private chats
        if _detect_question_patterns(text):
            reasons.append("direct-question")
            score += 1.2

    # Action keywords detection
    action_kw = action_keywords if action_keywords else DEFAULT_ACTION_KEYWORDS
    if _check_keywords(text, action_kw):
        reasons.append("action-required")
        score += 1.0 if is_private else 0.8

    # === CATEGORY 2: Decisions, Voting, and Direction Changes ===
    decision_kw = decision_keywords if decision_keywords else DEFAULT_DECISION_KEYWORDS
    if _check_keywords(text, decision_kw):
        reasons.append("decision")
        score += 1.1

    # === CATEGORY 4: Urgency & Importance Indicators ===
    urgency_kw = urgency_keywords if urgency_keywords else DEFAULT_URGENCY_KEYWORDS
    if _check_keywords(text, urgency_kw):
        reasons.append("urgent")
        score += 1.5  # High priority

    importance_kw = (
        importance_keywords if importance_keywords else DEFAULT_IMPORTANCE_KEYWORDS
    )
    if _check_keywords(text, importance_kw):
        reasons.append("important")
        score += 0.9

    # === CATEGORY 5: Project & Interest Updates ===
    release_kw = release_keywords if release_keywords else DEFAULT_RELEASE_KEYWORDS
    if _check_keywords(text, release_kw):
        reasons.append("release")
        score += 0.8

    security_kw = security_keywords if security_keywords else DEFAULT_SECURITY_KEYWORDS
    if _check_keywords(text, security_kw):
        reasons.append("security")
        score += 1.2  # Security is high priority

    # === CATEGORY 6: Structured or Sensitive Data ===
    if detect_codes and _detect_code_patterns(text):
        reasons.append("code-detected")
        score += 1.3  # OTP/codes are very important

    if detect_documents and has_media and media_type:
        # Treat any media type as potentially important
        # Common types: document, photo, voice, video_note, video, audio, sticker, animation, video_message
        reasons.append(f"media-{media_type}")
        score += 0.7

    # === CATEGORY 8: Risk or Incident Messages ===
    risk_kw = risk_keywords if risk_keywords else DEFAULT_RISK_KEYWORDS
    if _check_keywords(text, risk_kw):
        reasons.append("risk")
        score += 1.0

    # === CATEGORY 9: Opportunity Messages ===
    opportunity_kw = (
        opportunity_keywords if opportunity_keywords else DEFAULT_OPPORTUNITY_KEYWORDS
    )
    if _check_keywords(text, opportunity_kw):
        reasons.append("opportunity")
        score += 0.6

    # === LEGACY: VIP Senders ===
    if sender_id in vip:
        reasons.append("vip")
        score += 1.0  # Increased from 0.8

    # === LEGACY: Engagement Thresholds ===
    if reactions >= max(react_thr, 0) > 0:
        reasons.append("reactions")
        score += 0.5  # Increased from 0.4

    if replies >= max(reply_thr, 0) > 0:
        reasons.append("replies")
        score += 0.5  # Increased from 0.4

    # === LEGACY: Custom Keywords ===
    if keywords and _check_keywords(text, keywords):
        reasons.append("keywords")
        score += 0.8  # Increased from 0.6

    # === CATEGORY 7: Personal Context (Rare Senders) ===
    # This would require tracking message frequency per sender
    # Could be implemented in worker.py by checking last message timestamp

    return HeuristicResult(
        important=bool(reasons),
        reasons=reasons,
        content_hash=content_hash(text or ""),
        pre_score=score,
    )
