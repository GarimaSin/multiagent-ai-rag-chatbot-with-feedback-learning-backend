"""Durable, separately scalable document ingestion worker.

Run `python -m app.worker`; multiple PostgreSQL workers use SKIP LOCKED.
Expired leases are retried, publications are fenced by a unique lease token.
"""
import asyncio
import contextlib
import logging
import signal

from app.branding import APP_NAMESPACE
from app.config import Settings
from app.db import Database
from app.provider import make_provider
from app.repository import Repository
from app.text import SUSPICIOUS, chunks

log = logging.getLogger(f"{APP_NAMESPACE}.worker")


class IngestionWorker:
    def __init__(self, repo, provider, settings):
        self.repo, self.provider, self.settings = repo, provider, settings

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(self.settings.worker_lease_seconds / 3)
            renewed = await asyncio.to_thread(self.repo.renew_document, job["id"], job["token"], self.settings.worker_lease_seconds)
            if not renewed:
                return

    async def run_once(self):
        job = await asyncio.to_thread(self.repo.claim_document, self.settings.worker_lease_seconds)
        if not job:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            if SUSPICIOUS.search(job["body"]):
                await asyncio.to_thread(self.repo.fail_document, job, True)
                return True
            async with asyncio.timeout(self.settings.ingestion_deadline_seconds):
                parts = chunks(job["body"])
                if not parts or len(parts) > 800:
                    await asyncio.to_thread(self.repo.fail_document, job, True)
                    return True
                vectors = []
                for i in range(0, len(parts), 32):
                    vectors.extend(await self.provider.embed(parts[i:i+32]))
                await asyncio.to_thread(self.repo.publish_document, job, parts, vectors, self.settings.embedding_signature)
        except asyncio.CancelledError:
            # Do not remove another worker's lease. This token alone is fenced.
            await asyncio.shield(asyncio.to_thread(self.repo.fail_document, job))
            raise
        except Exception as error:
            log.warning("ingestion_failed document_id=%s error_type=%s", job["id"], type(error).__name__)
            await asyncio.to_thread(self.repo.fail_document, job)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        return True


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    db = Database(settings.database_url)
    if not await asyncio.to_thread(db.ready):
        raise RuntimeError("Run python -m app.manage init first")
    provider = make_provider(settings)
    repo = Repository(db)
    worker = IngestionWorker(repo, provider, settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    counter = 0
    try:
        while not stop.is_set():
            try:
                worked = await worker.run_once()
                counter += 1
                if counter % 60 == 0:
                    await asyncio.to_thread(repo.clean_rate_buckets)
                if not worked:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
            except Exception as error:
                log.warning("worker_loop_failed error_type=%s", type(error).__name__)
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await provider.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
