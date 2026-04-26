import re


def split_text_into_chunks(text, chunk_size_words=350, overlap_words=80):
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    words = normalized.split(" ")
    if len(words) <= chunk_size_words:
        return [normalized]

    chunks = []
    start = 0
    step = max(1, chunk_size_words - overlap_words)

    while start < len(words):
        end = start + chunk_size_words
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
        start += step

    return chunks
