"""Explicit agent roles, coordinated by a finite-state supervisor.

Planner/Retrieval are deterministic tool agents. Answer/Verifier use the
provider. Learning has a separate evidence policy and human approval boundary.
There is no unbounded autonomous conversation between models.
"""
import asyncio
import hashlib
import re
from dataclasses import dataclass

from app.branding import APP_SLUG
from app.cache import TTLCache
from app.errors import AppError, ProviderError
from app.text import SUSPICIOUS, normalize

ABSTENTION = "I could not find enough support in your uploaded documents to answer that. Try adding a relevant document or making the question more specific."
POLICY_VERSION = f"{APP_SLUG}-policy-1"


@dataclass
class Plan:
    query: str
    retrieve: bool
    greeting: bool


class PlannerAgent:
    def plan(self, question, history, mode):
        greeting = normalize(question).strip("!.?") in {"hi", "hello", "hey", "thanks", "thank you"}
        query = question
        previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        if previous and (len(question.split()) < 7 or re.search(r"\b(it|that|they|those|this|their)\b", question, re.I)):
            query = previous[:1000] + "\nFollow-up: " + question
        return Plan(query=query, retrieve=mode == "knowledge" and not greeting, greeting=greeting)


class RetrievalAgent:
    def __init__(self, repo, provider, settings):
        self.repo, self.provider, self.settings = repo, provider, settings
        self.cache = TTLCache(settings.cache_max_entries, settings.cache_ttl_seconds)

    async def run(self, user_id, query, revision):
        key = (user_id, revision, self.settings.embedding_signature, POLICY_VERSION, hashlib.sha256(query.encode()).hexdigest())
        cached = self.cache.get(key)
        if cached is not None:
            return {**cached, "cache_hit": True}
        lexical_task = asyncio.create_task(asyncio.to_thread(self.repo.lexical_search, user_id, query, self.settings.embedding_signature, self.settings.top_k * 3))

        async def dense():
            vector = (await self.provider.embed([query]))[0]
            return await asyncio.to_thread(self.repo.vector_search, user_id, vector, self.settings.embedding_signature, self.settings.top_k * 3, self.settings.min_similarity)

        results = await asyncio.gather(lexical_task, dense(), return_exceptions=True)
        keyword, vector = results
        if isinstance(keyword, BaseException):
            raise keyword
        degraded = isinstance(vector, Exception)
        if isinstance(vector, BaseException) and not isinstance(vector, Exception):
            raise vector
        vector = [] if degraded else vector
        # Reciprocal-rank fusion combines ranks, not incompatible score scales.
        fused, originals = {}, {}
        for ranking in [keyword, vector]:
            for rank, item in enumerate(ranking, 1):
                originals[item["id"]] = item
                fused[item["id"]] = fused.get(item["id"], 0) + 1 / (60 + rank)
        ranked_ids = sorted(fused, key=lambda key: (-fused[key], key))[:self.settings.top_k]
        sources = []
        for i, chunk_id in enumerate(ranked_ids, 1):
            row = originals[chunk_id]
            if SUSPICIOUS.search(row["body"]):
                continue
            sources.append({"source_id": f"S{len(sources)+1}", "chunk_id": chunk_id, "document_id": row["document_id"], "title": row["title"], "body": row["body"], "quote": row["body"], "rank_score": round(fused[chunk_id], 6)})
        result = {"sources": sources, "degraded": degraded, "cache_hit": False}
        if not degraded:
            self.cache.set(key, result)
        return result


class AnswerAgent:
    def __init__(self, provider):
        self.provider = provider

    async def stream(self, question, history, sources, mode):
        async for token in self.provider.stream(question, history, sources, mode):
            yield token


class VerifierAgent:
    def __init__(self, provider):
        self.provider = provider

    def citation_check(self, answer, sources):
        ids = set(re.findall(r"\[(S\d+)\]", answer))
        known = {s["source_id"] for s in sources}
        abstained = answer.strip().lower().startswith("i could not find enough support")
        if abstained:
            # Only accept the application's controlled abstention, not arbitrary
            # model text starting with an abstention then adding unsupported facts.
            return answer.strip() == ABSTENTION or answer.strip() == "I could not find enough support in your uploaded documents to answer that."
        if not ids or not ids <= known:
            return False
        if re.search(r"\[(?:S\w+|\d+)\]", answer) and set(re.findall(r"\[(S\w+)\]", answer)) - known:
            return False
        # Every non-heading paragraph needs a source reference.
        paragraphs = [p for p in answer.split("\n\n") if p.strip() and not p.lstrip().startswith("#")]
        return bool(paragraphs) and all(re.search(r"\[S\d+\]", p) for p in paragraphs)

    async def run(self, question, answer, sources, quality):
        if not self.citation_check(answer, sources):
            return {"supported": False, "level": "citation_check_failed", "reason": "Missing or invalid source references"}
        if quality == "verified" and not answer.startswith("I could not find enough support"):
            verdict = await self.provider.verify(question, answer, [s["body"] for s in sources])
            return {**verdict, "level": "model_checked"}
        return {"supported": True, "level": "citations_checked", "reason": "References exist; semantic truth is not guaranteed"}


class LearningAgent:
    def __init__(self, provider):
        self.provider = provider

    async def evaluate(self, candidate):
        if SUSPICIOUS.search(candidate["correction"]):
            raise AppError(422, "unsafe_correction", "Correction contains instruction-like content")
        return await self.provider.verify(candidate["question"], candidate["correction"], [candidate["evidence_quote"]])
