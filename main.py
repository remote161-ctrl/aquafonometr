import os
import csv as csvmod
import time
import math
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from jose import jwt, JWTError

from config import (
    HOST, PORT, SECRET_KEY, ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    DOWNLOAD_SIZE_MB, UPLOAD_SIZE_MB,
    RATE_LIMIT_SECONDS,
)
from database import (
    init_db, insert_test, get_tests, verify_user, get_stats,
    get_phone_history, get_daily_stats, insert_track, get_tracks,
)

BASE_DIR = os.path.dirname(__file__)

# --- Logging ---
log_path = os.path.join(BASE_DIR, "server.log")
handler = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
logger = logging.getLogger("aquafonometr")

# --- BTS Cache ---
_bts_cache = {"sites": [], "ts": 0}
BTS_CACHE_TTL = 3600


def _load_bts_csv():
    now = time.time()
    if _bts_cache["sites"] and (now - _bts_cache["ts"]) < BTS_CACHE_TTL:
        return _bts_cache["sites"]
    csv_path = os.path.join(BASE_DIR, "bts_hw_coordinates.csv")
    sites = []
    if os.path.isfile(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csvmod.DictReader(f)
            for row in reader:
                try:
                    lat = float(row["lat"])
                    lon = float(row["lon"])
                except (ValueError, KeyError):
                    continue
                if lat == 0 or lon == 0:
                    continue
                sites.append({
                    "name": row.get("name", ""),
                    "hostid": row.get("hostid", ""),
                    "lat": lat,
                    "lon": lon,
                    "location": row.get("location", ""),
                })
    _bts_cache["sites"] = sites
    _bts_cache["ts"] = now
    logger.info(f"[BTS] Loaded {len(sites)} base stations")
    return sites


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# --- Rate limiting ---
_rate_limits = {}
_track_rate_limits = {}


def _check_rate_limit(ip):
    now = time.time()
    last = _rate_limits.get(ip, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False, int(RATE_LIMIT_SECONDS - (now - last))
    _rate_limits[ip] = now
    return True, 0


def _check_track_rate_limit(ip):
    now = time.time()
    last = _track_rate_limits.get(ip, 0)
    if now - last < 5:
        return False, int(5 - (now - last))
    _track_rate_limits[ip] = now
    return True, 0


# --- App ---

@asynccontextmanager
async def lifespan(application):
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    await init_db()
    _load_bts_csv()
    logger.info(f"[STARTUP] AQUAMER download={DOWNLOAD_SIZE_MB}MB upload={UPLOAD_SIZE_MB}MB")
    yield

app = FastAPI(title="AQUAMER", lifespan=lifespan)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# --- Auth helpers ---

def create_token(username: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# --- Pydantic models ---

class SubmitData(BaseModel):
    phone: str
    latitude: float
    longitude: float
    download_speed: float
    upload_speed: float
    ping: Optional[float] = None
    jitter: Optional[float] = None


class TrackPoint(BaseModel):
    latitude: float
    longitude: float
    download_speed: float
    t_from: int = 0


class TrackData(BaseModel):
    phone: str
    points: list[TrackPoint]
    duration_sec: int = 0


class LoginData(BaseModel):
    username: str
    password: str


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "subscriber.html")


@app.get("/operator", response_class=HTMLResponse)
async def operator_page(request: Request):
    return templates.TemplateResponse(request, "operator.html")


@app.post("/api/auth/login")
async def login(data: LoginData):
    user = await verify_user(data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(data.username), "username": data.username}


@app.get("/api/auth/me")
async def auth_me(user: str = Depends(get_current_user)):
    return {"username": user}


@app.post("/api/submit")
async def submit(data: SubmitData, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    allowed, wait = _check_rate_limit(client_ip)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Too many requests. Wait {wait}s")

    if not data.phone or len(data.phone) < 5:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    if data.download_speed < 0 or data.upload_speed < 0:
        raise HTTPException(status_code=400, detail="Invalid speed values")

    await insert_test(
        phone=data.phone.strip(),
        latitude=data.latitude,
        longitude=data.longitude,
        download_speed=round(data.download_speed, 2),
        upload_speed=round(data.upload_speed, 2),
        ping=round(data.ping, 2) if data.ping else None,
        jitter=round(data.jitter, 2) if data.jitter else None,
    )
    logger.info(f"[SUBMIT] {data.phone} ↓{data.download_speed:.1f} ↑{data.upload_speed:.1f} ping={data.ping} jitter={data.jitter} from {client_ip}")
    return {"status": "ok", "message": "Test result saved"}


@app.get("/api/results")
async def results(
    user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    speed_min: Optional[float] = Query(None),
    speed_max: Optional[float] = Query(None),
):
    tests = await get_tests(
        date_from=date_from, date_to=date_to,
        phone=phone, speed_min=speed_min, speed_max=speed_max
    )
    return {"results": tests, "count": len(tests)}


@app.post("/api/track/submit")
async def track_submit(data: TrackData, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    allowed, wait = _check_track_rate_limit(client_ip)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Too many requests. Wait {wait}s")

    if not data.phone or len(data.phone) < 5:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    if len(data.points) < 2:
        raise HTTPException(status_code=400, detail="Track needs at least 2 points")
    if len(data.points) > 1000:
        raise HTTPException(status_code=400, detail="Track too long (max 1000 points)")
    for p in data.points:
        if p.download_speed < 0:
            raise HTTPException(status_code=400, detail="Invalid speed value")

    track_id = await insert_track(
        phone=data.phone.strip(),
        points=[p.model_dump() for p in data.points],
        duration_sec=data.duration_sec,
    )
    logger.info(f"[TRACK] {data.phone} {len(data.points)} pts, {data.duration_sec}s from {client_ip}")
    return {"status": "ok", "track_id": track_id}


@app.get("/api/tracks")
async def tracks_list(
    user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    include_points: int = Query(0),
):
    tracks = await get_tracks(
        date_from=date_from, date_to=date_to,
        phone=phone, include_points=bool(include_points),
    )
    return {"tracks": tracks, "count": len(tracks)}


@app.get("/api/stats")
async def stats(user: str = Depends(get_current_user)):
    return await get_stats()


@app.get("/api/daily-stats")
async def daily_stats(user: str = Depends(get_current_user), days: int = Query(7)):
    return await get_daily_stats(days)


@app.get("/api/history/{phone}")
async def phone_history(phone: str, user: str = Depends(get_current_user)):
    tests = await get_phone_history(phone)
    return {"results": tests, "count": len(tests)}


@app.get("/api/export")
async def export_csv(
    user: str = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    speed_min: Optional[float] = Query(None),
    speed_max: Optional[float] = Query(None),
):
    tests = await get_tests(
        date_from=date_from, date_to=date_to,
        phone=phone, speed_min=speed_min, speed_max=speed_max
    )
    import io
    output = io.StringIO()
    writer = csvmod.writer(output)
    writer.writerow(["id", "phone", "latitude", "longitude", "download_speed", "upload_speed", "ping", "jitter", "created_at"])
    for t in tests:
        writer.writerow([t["id"], t["phone"], t["latitude"], t["longitude"],
                        t["download_speed"], t["upload_speed"], t.get("ping", ""), t.get("jitter", ""), t["created_at"]])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aquafonometr_export.csv"}
    )


@app.get("/api/bts")
async def get_bts(user: str = Depends(get_current_user)):
    sites = _load_bts_csv()
    return {"sites": sites, "count": len(sites)}


@app.get("/api/nearest-bts")
async def nearest_bts(
    user: str = Depends(get_current_user),
    lat: float = Query(...),
    lon: float = Query(...),
    limit: int = Query(5),
):
    sites = _load_bts_csv()
    for s in sites:
        s["distance_km"] = round(_haversine(lat, lon, s["lat"], s["lon"]), 2)
    nearest = sorted(sites, key=lambda x: x["distance_km"])[:limit]
    return {"sites": nearest}


@app.get("/api/stats-by-zone")
async def stats_by_zone(user: str = Depends(get_current_user)):
    all_tests = await get_tests()
    zones = defaultdict(list)
    for t in all_tests:
        zlat = round(t["latitude"], 2)
        zlon = round(t["longitude"], 2)
        zones[(zlat, zlon)].append(t)

    result = []
    for (zlat, zlon), tests in zones.items():
        speeds = [t["download_speed"] for t in tests]
        result.append({
            "lat": zlat, "lon": zlon,
            "count": len(tests),
            "avg_download": round(sum(speeds) / len(speeds), 2),
            "min_download": round(min(speeds), 2),
            "max_download": round(max(speeds), 2),
        })
    return {"zones": result}


# --- SpeedTest endpoints ---

DOWNLOAD_CHUNK_MB = 1
DOWNLOAD_CHUNK_SIZE = DOWNLOAD_CHUNK_MB * 1024 * 1024
DATA_BLOCK = os.urandom(DOWNLOAD_CHUNK_SIZE)

STREAM_CHUNK_SIZE = 64 * 1024  # 64KB — smaller chunks for more accurate measurement
STREAM_BLOCK = os.urandom(STREAM_CHUNK_SIZE)


def _stream_download(total_bytes: int):
    sent = 0
    while sent < total_bytes:
        chunk = DATA_BLOCK[:min(DOWNLOAD_CHUNK_SIZE, total_bytes - sent)]
        yield chunk
        sent += len(chunk)


def _stream_infinite():
    while True:
        yield STREAM_BLOCK


@app.get("/speedtest/stream")
async def speedtest_stream():
    return StreamingResponse(
        _stream_infinite(),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        }
    )


@app.get("/speedtest/download")
async def speedtest_download():
    total_bytes = DOWNLOAD_SIZE_MB * 1024 * 1024
    return StreamingResponse(
        _stream_download(total_bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(total_bytes),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        }
    )


@app.get("/speedtest/download/{size_mb}")
async def speedtest_download_size(size_mb: int):
    size_mb = max(1, min(size_mb, 200))
    total_bytes = size_mb * 1024 * 1024
    return StreamingResponse(
        _stream_download(total_bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(total_bytes),
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )


@app.post("/speedtest/upload")
async def speedtest_upload(request: Request):
    body = await request.body()
    size_mb = len(body) / (1024 * 1024)
    return {"status": "ok", "received_mb": round(size_mb, 2)}


@app.post("/speedtest/upload-stream")
async def speedtest_upload_stream(request: Request):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
    return {"bytes": total}


@app.get("/speedtest/ping")
async def speedtest_ping():
    return {"ts": time.time()}


if __name__ == "__main__":
    import uvicorn
    ssl_cert = os.path.join(BASE_DIR, "cert.pem")
    ssl_key = os.path.join(BASE_DIR, "key.pem")
    use_ssl = os.path.isfile(ssl_cert) and os.path.isfile(ssl_key)
    proto = "https" if use_ssl else "http"
    print(f"=== AQUAMER Server ===")
    print(f"Subscriber page: {proto}://{HOST}:{PORT}/")
    print(f"Operator page:   {proto}://{HOST}:{PORT}/operator")
    print(f"Default login:   admin / admin")
    ssl_kw = {"ssl_certfile": ssl_cert, "ssl_keyfile": ssl_key} if use_ssl else {}
    uvicorn.run(app, host=HOST, port=PORT, workers=4, **ssl_kw)
