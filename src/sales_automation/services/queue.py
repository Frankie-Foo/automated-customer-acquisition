from __future__ import annotations

from ..db import Repository
from ..logging_utils import log


class QueueService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def queue(self, limit: int) -> int:
        count = self.repo.queue_contacts(limit)
        log("queue.completed", count=count)
        return count

    def queue_contact(self, contact_id: int) -> bool:
        queued = self.repo.queue_contact(contact_id)
        log("queue.contact_completed", contact_id=contact_id, queued=queued)
        return queued

__all__ = ["QueueService"]
