import hashlib
import math
import re

STOPWORDS = set("a an and are as at be by can do does for from how i in is it of on or please tell that the this to was what when where which who why with you your me about".split())
SUSPICIOUS = re.compile(r"ignore (?:all |the |any )?(?:previous|prior|above) instructions|reveal (?:your |the )?system prompt|(?:system|developer)\s*:\s*you (?:must|are)", re.I)


def tokens(value: str) -> list[str]:
    words = [t for t in re.findall(r"[\w]+", value.lower(), flags=re.UNICODE) if t not in STOPWORDS and len(t) > 1]
    # Small English plural normalizer for offline demo ranking, not a stemmer
    # intended to replace real multilingual embedding models.
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")) else t for t in words]


def normalize(value: str) -> str:
    return " ".join(value.lower().split())


def chunks(text: str, size: int = 1100, overlap: int = 180) -> list[str]:
    if size <= overlap or overlap < 0:
        raise ValueError("Chunk size must exceed overlap")
    text = text.replace("\r\n", "\n").strip()
    result, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            split = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            if split > start:
                end = split
        value = text[start:end].strip()
        if value:
            result.append(value)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def hash_embedding(value: str, dimensions: int = 1536) -> list[float]:
    """Deterministic lexical demo vector; NOT a semantic embedding model."""
    result = [0.0] * dimensions
    for token in tokens(value):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        result[index] += 1 if digest[4] % 2 else -1
    length = math.sqrt(sum(x * x for x in result)) or 1
    return [x / length for x in result]


def cosine(a: list[float], b: list[float]) -> float:
    denom = math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / denom if denom else 0


def redact(value: str) -> str:
    """Basic outbound masking, not a comprehensive DLP or compliance system."""
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", value, flags=re.I)
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})\b", "[SECRET]", value)
    return value
