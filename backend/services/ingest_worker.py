import time
import asyncio
from datetime import datetime, timezone, timedelta
from db import db
import logging

class IngestWorker:
    """
    Async worker that drains the `ingest_jobs` queue.
    Scales via concurrency (asyncio.gather) or can run as separate process.
    """

    def __init__(self, concurrency: int = 4):
        self.concurrency = concurrency
        self.running = False
        self.logger = logging.getLogger("IngestWorker")
        
    async def run(self):
        """Worker loop - runs until stopped."""
        self.running = True
        self.logger.info("IngestWorker started.")
        while self.running:
            try:
                # Fetch pending jobs
                jobs = self._fetch_jobs(limit=self.concurrency)
                if not jobs:
                    await asyncio.sleep(2) # Backoff if idle
                    continue
                
                # Execute in parallel
                tasks = [self._process_job(job) for job in jobs]
                await asyncio.gather(*tasks)

            except Exception as e:
                self.logger.error(f"Worker main loop error: {e}")
                await asyncio.sleep(5)

    def _fetch_jobs(self, limit: int) -> List[Dict]:
        """Atomic fetch-and-lock implementation using SELECT FOR UPDATE SKIP LOCKED."""
        query = """
        WITH job AS (
            SELECT id, creator_id, platform_key, source_item_id, job_type, attempts
            FROM ingest_jobs
            WHERE status IN ('PENDING', 'RETRY')
              AND next_run_at <= NOW()
            ORDER BY priority DESC, created_at ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE ingest_jobs j
        SET status = 'RUNNING', updated_at = NOW()
        FROM job
        WHERE j.id = job.id
        RETURNING j.*
        """
        # Note: psycopg execute_query fetches all
        # To strictly use atomic behavior we rely on the database returning rows.
        # This implementation assumes the db.execute_query works correctly with RETURNING.
        return db.execute_query(query, (limit,))

    async def _process_job(self, job: Dict):
        """Execute a single job (Embed/Transcribe)."""
        job_id = job['id']
        job_type = job['job_type']
        
        try:
            self.logger.info(f"Processing job {job_id} ({job_type})")
            
            # Simulate heavy lifting
            if job_type == 'EMBED':
                await self._do_embedding(job['source_item_id'])
            elif job_type == 'TRANSCRIBE':
                await self._do_transcription(job['source_item_id'])
            
            # Mark Success
            db.execute_update(
                "UPDATE ingest_jobs SET status = 'COMPLETED', updated_at = NOW(), finished_at = NOW() WHERE id = %s",
                (job_id,)
            )

        except Exception as e:
            # Handle Failure & Retry
            attempts = job['attempts'] + 1
            max_attempts = 5
            
            if attempts >= max_attempts:
                new_status = 'FAILED'
                next_run = None
            else:
                new_status = 'RETRY'
                # Exponential backoff: 2s, 4s, 8s, 16s...
                delay = 2 ** attempts 
                next_run = datetime.now(timezone.utc) + timedelta(seconds=delay)
            
            db.execute_update(
                """
                UPDATE ingest_jobs 
                SET status = %s, attempts = %s, next_run_at = %s, last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (new_status, attempts, next_run, str(e), job_id)
            )

    async def _do_embedding(self, item_id: str):
        """Call Embedding Service."""
        # TODO: integrate with your existing rag.py or embedding model
        await asyncio.sleep(0.5) # Mock latency
        # Fetch item text -> chunks -> embed -> save to pgvector
        pass

    async def _do_transcription(self, item_id: str):
        """Call Transcription Service (Whisper/etc)."""
        await asyncio.sleep(2.0) # Mock time
        pass
