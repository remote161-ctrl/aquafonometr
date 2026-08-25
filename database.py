import aiosqlite
import bcrypt
from config import DB_PATH, DEFAULT_OPERATOR_LOGIN, DEFAULT_OPERATOR_PASSWORD


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                download_speed REAL NOT NULL,
                upload_speed REAL NOT NULL,
                ping REAL,
                jitter REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)

        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_created ON tests(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_phone ON tests(phone)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_speed ON tests(download_speed)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_coords ON tests(latitude, longitude)")

        await db.commit()

        cursor = await db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        if row[0] == 0:
            pw_hash = bcrypt.hashpw(
                DEFAULT_OPERATOR_PASSWORD.encode(),
                bcrypt.gensalt()
            ).decode()
            await db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (DEFAULT_OPERATOR_LOGIN, pw_hash)
            )
            await db.commit()
            print(f"[DB] Created default operator: {DEFAULT_OPERATOR_LOGIN}/{DEFAULT_OPERATOR_PASSWORD}")


async def insert_test(phone, latitude, longitude, download_speed, upload_speed, ping, jitter=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tests (phone, latitude, longitude, download_speed, upload_speed, ping, jitter)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (phone, latitude, longitude, download_speed, upload_speed, ping, jitter)
        )
        await db.commit()


async def get_tests(date_from=None, date_to=None, phone=None, speed_min=None, speed_max=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM tests WHERE 1=1"
        params = []

        if date_from:
            query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            params.append(date_to + " 23:59:59")
        if phone:
            digits = ''.join(c for c in phone if c.isdigit())
            if digits:
                query += " AND REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), '(', ''), ')', ''), ' ', '') LIKE ?"
                params.append(f"%{digits}%")
        if speed_min is not None:
            query += " AND download_speed >= ?"
            params.append(speed_min)
        if speed_max is not None:
            query += " AND download_speed <= ?"
            params.append(speed_max)

        query += " ORDER BY created_at DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def verify_user(username, password):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = await cursor.fetchone()
        if row and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return dict(row)
        return None


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT COUNT(*) as total, "
            "AVG(download_speed) as avg_down, "
            "AVG(upload_speed) as avg_up, "
            "AVG(ping) as avg_ping, "
            "MIN(download_speed) as min_down, "
            "MAX(download_speed) as max_down "
            "FROM tests"
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}


async def get_phone_history(phone, limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM tests WHERE phone = ? ORDER BY created_at DESC LIMIT ?",
            (phone, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_daily_stats(days=7):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT DATE(created_at) as day, "
            "COUNT(*) as count, "
            "AVG(download_speed) as avg_down, "
            "AVG(upload_speed) as avg_up, "
            "AVG(ping) as avg_ping "
            "FROM tests "
            "WHERE created_at >= DATE('now', ?) "
            "GROUP BY DATE(created_at) "
            "ORDER BY day",
            (f"-{days} days",)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
