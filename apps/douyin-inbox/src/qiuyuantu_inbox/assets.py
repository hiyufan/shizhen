"""共享素材任务及编码槽：依赖等待持久化，不占据准备 worker。"""

import asyncio
import time

ASSET_SCHEMA = """
CREATE TABLE IF NOT EXISTS asset_tasks (
 key TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL, updated REAL NOT NULL
);
"""


class DependencyPending(Exception):
    pass


class SharedAssets:
    def acquire_asset(self, job: dict, key: str, now: float) -> bool:
        with self.transaction() as db:
            self._owned(db, job)
            if self.finish_cancellation(db, job["id"], now):
                return False
            busy = db.execute(
                "SELECT 1 FROM asset_tasks a JOIN jobs j ON j.id=a.owner "
                "WHERE a.key=? AND j.state='processing' AND j.token=a.token AND j.lease>?",
                (key, now),
            ).fetchone()
            if busy:
                db.execute(
                    "UPDATE jobs SET state='blocked',blocked_on=?,token=NULL,lease=NULL,"
                    "attempts=max(0,attempts-1),updated=? WHERE id=?",
                    (key, now, job["id"]),
                )
                return False
            db.execute("INSERT OR REPLACE INTO asset_tasks VALUES (?,?,?,?)", (key, job["id"], job["token"], now))
            return True

    def release_asset(self, job: dict, key: str, now: float) -> None:
        with self.transaction() as db:
            deleted = db.execute(
                "DELETE FROM asset_tasks WHERE key=? AND owner=? AND token=?", (key, job["id"], job["token"])
            ).rowcount
            if deleted:
                db.execute(
                    "UPDATE jobs SET state='queued',blocked_on=NULL,available=?,updated=? "
                    "WHERE state='blocked' AND blocked_on=?",
                    (now, now, key),
                )


class AssetCoordinator:
    def __init__(self, store):
        self.store = store

    async def run(self, job: dict, key: str, operation):
        if not await asyncio.to_thread(self.store.acquire_asset, job, key, time.time()):
            raise DependencyPending()
        try:
            return await operation()
        finally:
            await asyncio.shield(asyncio.to_thread(self.store.release_asset, job, key, time.time()))
