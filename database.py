import aiosqlite
from datetime import datetime, timezone
from config import DATABASE_PATH, IST

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA synchronous = NORMAL;")
        await db.execute("PRAGMA foreign_keys = ON;")

        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                created_at TIMESTAMP NOT NULL,
                last_active_at TIMESTAMP NOT NULL
            )
        """)

        # Uploads table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                provider TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        """)

        # Migration: ensure file_unique_id exists if table was pre-existing
        try:
            await db.execute("ALTER TABLE uploads ADD COLUMN file_unique_id TEXT;")
        except Exception:
            pass

        # Indexes for ultra-fast deduplication & lookups
        await db.execute("CREATE INDEX IF NOT EXISTS idx_uploads_unique_id ON uploads (file_unique_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads (user_id, id DESC);")
        await db.commit()

async def touch_user(user_id: int):
    now_iso = datetime.now(IST).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, created_at, last_active_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_active_at = excluded.last_active_at;
        """, (user_id, now_iso, now_iso))
        await db.commit()

async def find_existing_upload(file_unique_id: str) -> dict | None:
    """Finds if this exact file was already uploaded previously (deduplication cache)."""
    if not file_unique_id:
        return None
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM uploads WHERE file_unique_id = ? ORDER BY id DESC LIMIT 1
        """, (file_unique_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def record_upload(user_id: int, file_id: str, file_unique_id: str, file_name: str, file_size: int, provider: str, url: str) -> int:
    await touch_user(user_id)
    now_iso = datetime.now(IST).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("""
            INSERT INTO uploads (user_id, file_id, file_unique_id, file_name, file_size, provider, url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, file_id, file_unique_id, file_name, file_size, provider, url, now_iso))
        await db.commit()
        return cur.lastrowid

async def get_upload_by_id(upload_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def delete_upload(upload_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            upload_data = dict(row)
        await db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        await db.commit()
        return upload_data

async def get_user_recent_uploads(user_id: int, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM uploads WHERE user_id = ? ORDER BY id DESC LIMIT ?
        """, (user_id, limit)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_bot_statistics() -> dict:
    now_ist = datetime.now(IST)
    today_start = datetime(now_ist.year, now_ist.month, now_ist.day, tzinfo=IST).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]

        async with db.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM uploads") as cur:
            row = await cur.fetchone()
            total_uploads = row[0]
            total_bytes = row[1]

        async with db.execute("SELECT COUNT(*) FROM uploads WHERE created_at >= ?", (today_start,)) as cur:
            uploads_today = (await cur.fetchone())[0]

        return {
            "total_users": total_users,
            "total_uploads": total_uploads,
            "total_bytes": total_bytes,
            "uploads_today": uploads_today
        }
