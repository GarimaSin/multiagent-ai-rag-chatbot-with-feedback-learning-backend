import asyncio
import time

from app.agents import ABSTENTION, AnswerAgent, PlannerAgent, RetrievalAgent, VerifierAgent


class Supervisor:
    """Bounded flow: plan -> retrieve -> answer -> verify -> commit.

    Fast mode exposes provisional tokens. Only a final done event makes an
    answer authoritative in the UI. Verified mode buffers until verification.
    """
    def __init__(self, repo, provider, settings):
        self.repo, self.settings = repo, settings
        self.planner = PlannerAgent()
        self.retrieval = RetrievalAgent(repo, provider, settings)
        self.answer = AnswerAgent(provider)
        self.verifier = VerifierAgent(provider)

    async def run(self, principal, body, history):
        started = time.perf_counter()
        plan = self.planner.plan(body.message, history, body.mode)
        yield "status", {"agent": "PlannerAgent", "message": "Choosing an answer path"}
        if plan.greeting:
            answer = "Hello! Ask me a question, or add documents so I can answer using your own sources."
            yield "delta", {"text": answer}
            yield "result", {"content": answer, "details": {"sources": [], "verification": {"level": "local_response"}, "duration_ms": round((time.perf_counter()-started)*1000), "provider": self.settings.provider, "mode": body.mode, "quality": body.quality}}
            return
        sources, degraded, hit = [], False, False
        revision = await asyncio.to_thread(self.repo.kb_version, principal.id)
        if plan.retrieve:
            yield "status", {"agent": "RetrievalAgent", "message": "Searching your documents"}
            retrieval = await self.retrieval.run(principal.id, plan.query, revision)
            sources, degraded, hit = retrieval["sources"], retrieval["degraded"], retrieval["cache_hit"]
            yield "sources", {"sources": [{k: v for k, v in s.items() if k != "body"} for s in sources]}
        if plan.retrieve and not sources:
            answer = ABSTENTION
            verdict = {"supported": True, "level": "abstained", "reason": "No sufficiently relevant evidence was retrieved"}
            yield "delta", {"text": answer}
        else:
            yield "status", {"agent": "AnswerAgent", "message": "Drafting — not yet checked" if body.quality == "fast" else "Drafting privately before checking"}
            parts, size = [], 0
            stream = self.answer.stream(body.message, history, sources, body.mode)
            try:
                async for token in stream:
                    size += len(token)
                    if size > 30_000:
                        raise ValueError("Model response exceeded application output limit")
                    parts.append(token)
                    if body.quality == "fast":
                        yield "delta", {"text": token}
            finally:
                await stream.aclose()
            answer = "".join(parts).strip()
            if not answer:
                raise ValueError("Empty model response")
            if body.mode == "knowledge":
                yield "status", {"agent": "VerifierAgent", "message": "Checking sources"}
                verdict = await self.verifier.run(body.message, answer, sources, body.quality)
                if not verdict["supported"]:
                    # Fail closed instead of keeping an unsupported answer.
                    answer = ABSTENTION
                    verdict = {**verdict, "level": "abstained", "replaced_draft": True}
            else:
                verdict = {"level": "general_unverified", "reason": "General answers are not checked against documents"}
            # Recheck KB revision after slow model calls. Deleted/updated evidence
            # must not silently become a final newly-persisted answer.
            latest = await asyncio.to_thread(self.repo.kb_version, principal.id)
            if body.mode == "knowledge" and latest != revision:
                answer = "Your documents changed while I was answering. Please ask again so I can use the current sources."
                sources = []
                verdict = {"level": "knowledge_changed", "replaced_draft": True}
            if body.quality == "verified":
                for i in range(0, len(answer), 80):
                    yield "delta", {"text": answer[i:i+80]}
        details = {
            "sources": [{k: v for k, v in s.items() if k != "body"} for s in sources],
            "verification": verdict, "retrieval_degraded": degraded,
            "retrieval_cache_hit": hit, "provider": self.settings.provider,
            "mode": body.mode, "quality": body.quality, "knowledge_version": revision,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        yield "result", {"content": answer, "details": details}
