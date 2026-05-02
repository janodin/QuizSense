"""
Shared topic resolution for QuizSense.
Maps AI-generated topic names to existing seeded Topics for a chapter.
"""
import re


def _normalize_topic(title):
    """Strip punctuation, lowercase, and whitespace-collapse a topic title."""
    return re.sub(r'[^a-z0-9\s]', '', (title or '').lower()).strip()


def find_topic_for_chapter(chapter, ai_topic_name, create=True):
    """
    Map an AI-generated topic name to an existing seeded Topic for this chapter.
    Falls back to creating a new topic only when no reasonable match exists.

    Args:
        chapter: Chapter model instance
        ai_topic_name: Topic name returned by the AI
        create: If True, create a new Topic when no match is found (default).
                If False, return None instead.

    Returns:
        Topic instance or None
    """
    from ..models import Topic

    if not ai_topic_name:
        return None

    normalized_ai = _normalize_topic(ai_topic_name)
    if not normalized_ai:
        return None

    existing = list(Topic.objects.filter(chapter=chapter))

    # 1. Exact match (case-insensitive)
    for t in existing:
        if _normalize_topic(t.title) == normalized_ai:
            return t

    # 2. Substring match — AI topic contains existing title or vice versa
    for t in existing:
        norm_title = _normalize_topic(t.title)
        if norm_title and (norm_title in normalized_ai or normalized_ai in norm_title):
            return t

    # 3. No match found — create a new topic (last resort)
    if create:
        return Topic.objects.create(chapter=chapter, title=ai_topic_name)
    return None
