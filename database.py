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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration_sec INTEGER,
                points_count INTEGER,
                avg_download_speed REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS track_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                download_speed REAL NOT NULL,
                t_from INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tems_measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'TEMS',
                measured_at TIMESTAMP NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                rsrp REAL,
                throughput_kbps REAL,
                cell_id TEXT
            )
        """)

        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_created ON tests(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_phone ON tests(phone)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_speed ON tests(download_speed)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tests_coords ON tests(latitude, longitude)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_phone ON tracks(phone)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_track_points_track ON track_points(track_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tems_measured ON tems_measurements(measured_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tems_coords ON tems_measurements(latitude, longitude)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tems_cell ON tems_measurements(cell_id)")

        tems_cols = await db.execute("PRAGMA table_info(tems_measurements)")
        tems_cols_rows = await tems_cols.fetchall()
        tems_has_throughput = any(r[1] == "throughput_kbps" for r in tems_cols_rows)
        if not tems_has_throughput:
            await db.execute("ALTER TABLE tems_measurements ADD COLUMN throughput_kbps REAL")

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


async def insert_track(phone, points, duration_sec):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        avg_speed = sum(p["download_speed"] for p in points) / len(points)
        cursor = await db.execute(
            "INSERT INTO tracks (phone, duration_sec, points_count, avg_download_speed) "
            "VALUES (?, ?, ?, ?)",
            (phone, duration_sec, len(points), round(avg_speed, 2))
        )
        track_id = cursor.lastrowid
        await db.executemany(
            "INSERT INTO track_points (track_id, latitude, longitude, download_speed, t_from) "
            "VALUES (?, ?, ?, ?, ?)",
            [(track_id, p["latitude"], p["longitude"], round(p["download_speed"], 2), p.get("t_from", 0))
             for p in points]
        )
        await db.commit()
        return track_id


async def get_tracks(date_from=None, date_to=None, phone=None, include_points=False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM tracks WHERE 1=1"
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

        query += " ORDER BY created_at DESC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        tracks = [dict(row) for row in rows]

        if include_points and tracks:
            for t in tracks:
                pcursor = await db.execute(
                    "SELECT latitude, longitude, download_speed, t_from "
                    "FROM track_points WHERE track_id = ? ORDER BY t_from",
                    (t["id"],)
                )
                prows = await pcursor.fetchall()
                t["points"] = [dict(p) for p in prows]

        return tracks


async def get_track(track_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        track = dict(row)
        pcursor = await db.execute(
            "SELECT latitude, longitude, download_speed, t_from "
            "FROM track_points WHERE track_id = ? ORDER BY t_from",
            (track_id,)
        )
        prows = await pcursor.fetchall()
        track["points"] = [dict(p) for p in prows]
        return track


async def insert_tems_measurements(rows):
    """Bulk insert TEMS measurements. rows: list of dicts."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO tems_measurements (source, measured_at, latitude, longitude, rsrp, throughput_kbps, cell_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r.get("source", "TEMS"), r["measured_at"], r["latitude"], r["longitude"],
              r.get("rsrp"), r.get("throughput_kbps"), r.get("cell_id")) for r in rows]
        )
        await db.commit()


async def get_tems(date_from=None, date_to=None, cell_id=None, source=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM tems_measurements WHERE 1=1"
        params = []

        if date_from:
            query += " AND measured_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND measured_at <= ?"
            params.append(date_to + " 23:59:59")
        if cell_id:
            query += " AND cell_id = ?"
            params.append(str(cell_id))
        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY measured_at ASC"

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_tems_cells():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT cell_id, COUNT(*) as cnt, MIN(rsrp) as min_rsrp, MAX(rsrp) as max_rsrp, "
            "AVG(rsrp) as avg_rsrp FROM tems_measurements "
            "GROUP BY cell_id ORDER BY cnt DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_tems_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT COUNT(*) as total, MIN(rsrp) as min_rsrp, MAX(rsrp) as max_rsrp, "
            "AVG(rsrp) as avg_rsrp FROM tems_measurements"
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}
