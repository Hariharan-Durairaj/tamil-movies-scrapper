from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict
import bcrypt
import jwt
from datetime import datetime, timedelta
import os
import sys
import traceback
import asyncio
import json
import logging
import logging.handlers
from pathlib import Path

from database import Database
from movie_processor import MovieProcessor
from api_clients import RadarrClient
from scheduler import TaskScheduler

# ---------------------------------------------------------------------------
# Initialise
# ---------------------------------------------------------------------------
app = FastAPI(title="Movie Automator API")
db  = Database()
processor  = MovieProcessor(db)
scheduler  = TaskScheduler(db, processor)

# ---------------------------------------------------------------------------
# Log file setup — rotate daily, keep 30 days
# ---------------------------------------------------------------------------
_log_dir = Path(__file__).parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file_path = _log_dir / "movie_automator.log"

_file_logger = logging.getLogger("movie_automator")
_file_logger.setLevel(logging.DEBUG)
_fh = logging.handlers.TimedRotatingFileHandler(
    str(_log_file_path), when="midnight", interval=1, backupCount=30, encoding="utf-8"
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_file_logger.addHandler(_fh)

# Patch db.add_log so every log entry is also written to file
_original_add_log = db.add_log.__func__ if hasattr(db.add_log, '__func__') else None

def _patched_add_log(self_db, level: str, message: str, details=None, exc_info=None):
    # Call original
    result = type(db).add_log(self_db, level, message, details, exc_info)
    # Mirror to file
    log_fn = getattr(_file_logger, level.lower(), _file_logger.info)
    extra = f" | {details}" if details else ""
    log_fn(f"{message}{extra}")
    if exc_info:
        _file_logger.debug(traceback.format_exc())
    return result

import types
db.add_log = types.MethodType(_patched_add_log, db)

# SSE: broadcast queue for live log streaming
_log_subscribers: List[asyncio.Queue] = []

_original_db_add_log = db.add_log
def _sse_add_log(level: str, message: str, details=None, exc_info=None):
    result = _original_db_add_log(level, message, details, exc_info)
    # Push to all SSE subscribers (non-blocking)
    event_data = json.dumps({
        "level": level,
        "message": message,
        "created_at": datetime.now().isoformat(),
        "details": details,
    })
    for q in list(_log_subscribers):
        try:
            q.put_nowait(event_data)
        except asyncio.QueueFull:
            pass
    return result

db.add_log = _sse_add_log

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-this-secret-in-production")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class SettingsUpdate(BaseModel):
    settings: Dict[str, str]

class MovieSearch(BaseModel):
    movie_name: str

class MovieProcess(BaseModel):
    title: str
    year: Optional[int] = None
    forum_url: Optional[str] = None

class MovieDownload(BaseModel):
    movie_id: int
    quality_id: Optional[int] = None

class ForumScan(BaseModel):
    max_pages: Optional[int] = 3
    max_links: Optional[int] = 50

class BulkAdd(BaseModel):
    movie_names: str   # comma-separated list

class ManualMovieUpdate(BaseModel):
    title: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
@app.get("/api/auth/check-setup")
def check_setup():
    has_users = db.has_users()
    return {"setup_needed": not has_users}

@app.post("/api/auth/setup", response_model=Token)
def setup_account(user: UserCreate):
    if db.has_users():
        raise HTTPException(status_code=400, detail="Setup already completed")
    password_hash = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
    user_id = db.create_user(user.username, password_hash)
    if not user_id:
        raise HTTPException(status_code=400, detail="Failed to create user")
    token = create_access_token({"sub": user.username})
    db.add_log('INFO', f'Initial setup completed by: {user.username}')
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/auth/login", response_model=Token)
def login(user: UserLogin):
    db_user = db.get_user_by_username(user.username)
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not bcrypt.checkpw(user.password.encode(), db_user['password_hash'].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings(token: str):
    verify_token(token)
    settings = db.get_all_settings()
    if 'qbittorrent_password' in settings:
        settings['qbittorrent_password'] = '***' if settings['qbittorrent_password'] else ''
    return settings

@app.post("/api/settings")
def update_settings(data: SettingsUpdate, token: str):
    verify_token(token)
    db.update_settings(data.settings)
    processor.refresh_clients()
    
    # Reload scheduler tasks if automation settings changed
    automation_keys = {'daily_scan_enabled', 'daily_scan_time', 'scan_pages', 'scan_links'}
    if any(key in data.settings for key in automation_keys):
        scheduler.reload_tasks()
    
    db.add_log('INFO', 'Settings updated', {'keys': list(data.settings.keys())})
    return {"success": True}

@app.get("/api/settings/test-connections")
def test_connections(token: str):
    verify_token(token)
    results = {'radarr': False, 'qbittorrent': False, 'omdb': False, 'tmdb': False}

    if processor.radarr:
        try:
            results['radarr'] = processor.radarr.test_connection()
        except Exception as e:
            db.add_log('WARNING', f'Radarr connection test error: {e}', exc_info=e)

    if processor.qbittorrent:
        try:
            results['qbittorrent'] = processor.qbittorrent.test_connection()
        except Exception as e:
            db.add_log('WARNING', f'qBittorrent connection test error: {e}', exc_info=e)

    if processor.omdb:
        try:
            results['omdb'] = processor.omdb.get_movie_info("Inception", 2010) is not None
        except Exception as e:
            db.add_log('WARNING', f'OMDB connection test error: {e}', exc_info=e)

    if processor.tmdb:
        try:
            results['tmdb'] = processor.tmdb.search_movie("Inception", 2010) is not None
        except Exception as e:
            db.add_log('WARNING', f'TMDB connection test error: {e}', exc_info=e)

    return results

# ---------------------------------------------------------------------------
# Movie search & processing
# ---------------------------------------------------------------------------
@app.post("/api/movies/search")
def search_movie(data: MovieSearch, token: str):
    verify_token(token)
    try:
        results = processor.search_movie_on_forum(data.movie_name)
        return {"results": results}
    except Exception as e:
        db.add_log('ERROR', f'Search endpoint error for "{data.movie_name}": {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/movies/process")
def process_movie(data: MovieProcess, token: str):
    verify_token(token)
    try:
        result = processor.process_movie(
            title=data.title,
            year=data.year,
            forum_url=data.forum_url,
            source='manual'
        )
        return result
    except Exception as e:
        db.add_log('ERROR',
                   f'Process endpoint error for "{data.title}": {e}',
                   {'title': data.title, 'year': data.year}, exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/movies/download")
def download_movie(data: MovieDownload, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(data.movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    try:
        if data.quality_id:
            qualities = db.get_movie_qualities(data.movie_id)
            quality   = next((q for q in qualities if q['id'] == data.quality_id), None)
            if not quality:
                raise HTTPException(status_code=404, detail="Quality not found")
            torrent = {
                'torrent_url': quality['torrent_url'],
                'name':        quality['torrent_name'],
                'quality':     quality['quality'],
                'file_size':   quality['file_size'],
            }
        else:
            if not movie['torrent_url']:
                raise HTTPException(status_code=400, detail="No torrent URL available for this movie")
            torrent = {
                'torrent_url': movie['torrent_url'],
                'name':        movie['torrent_name'] or movie['title'],
                'quality':     movie['downloaded_quality'],
                'file_size':   movie['file_size'],
            }

        success = processor.download_and_add_torrent(torrent, movie['title'])

        if success:
            db.update_movie(data.movie_id, {
                'is_downloaded':       True,
                'downloaded_quality':  torrent['quality'],
                'file_size':           torrent['file_size'],
                'added_to_qbittorrent': True,
                'rejection_reason':    None,   # clear any "manual download" flag
            })
            radarr_success = processor.add_to_radarr(movie['title'], movie['year'])
            if radarr_success:
                db.update_movie(data.movie_id, {'added_to_radarr': True})

        return {"success": success}

    except HTTPException:
        raise
    except Exception as e:
        db.add_log('ERROR',
                   f'Download endpoint error for movie id={data.movie_id}: {e}',
                   {'movie_id': data.movie_id}, exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/movies/forum-url")
def get_movie_from_url(url: str, token: str):
    verify_token(token)
    try:
        torrents = processor.get_movie_torrents(url)
        if not torrents:
            raise HTTPException(status_code=404, detail="No torrents found at that URL")
        return {"torrents": torrents}
    except HTTPException:
        raise
    except Exception as e:
        db.add_log('ERROR', f'forum-url endpoint error: {e}', {'url': url}, exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/movies/scan-forum")
def scan_forum(data: ForumScan, background_tasks: BackgroundTasks, token: str):
    verify_token(token)

    def run_scan():
        try:
            results = processor.scan_forum_for_new_movies(
                max_pages=data.max_pages,
                max_links=data.max_links
            )
            db.add_log('INFO',
                       f'Forum scan background task finished',
                       {'movies_processed': len(results)})
        except Exception as e:
            db.add_log('ERROR', f'Forum scan background task crashed: {e}', exc_info=e)

    background_tasks.add_task(run_scan)
    return {"message": "Forum scan started in background"}

# ---------------------------------------------------------------------------
# Movie management
# ---------------------------------------------------------------------------
@app.get("/api/movies")
def get_movies(token: str, filter: Optional[str] = None):
    verify_token(token)
    filters = {}
    if filter == 'downloaded':
        filters['is_downloaded'] = 1
    elif filter == 'pending':
        filters['is_downloaded'] = 0
    elif filter == 'rejected':
        filters['rejection_reason'] = True

    movies = db.get_all_movies(filters)
    for movie in movies:
        movie['available_qualities'] = db.get_movie_qualities(movie['id'])
    return {"movies": movies}

@app.get("/api/movies/{movie_id}")
def get_movie(movie_id: int, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    movie['available_qualities'] = db.get_movie_qualities(movie_id)
    return movie

@app.delete("/api/movies/{movie_id}")
def delete_movie(movie_id: int, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    db.delete_movie(movie_id)
    db.add_log('INFO', f'Deleted movie: {movie["title"]} ({movie["year"]})',
               {'movie_id': movie_id})
    return {"success": True}

# ---------------------------------------------------------------------------
# Bulk add movies (comma-separated names)
# ---------------------------------------------------------------------------
@app.post("/api/movies/bulk-add")
def bulk_add_movies(data: BulkAdd, background_tasks: BackgroundTasks, token: str):
    verify_token(token)
    names = [n.strip() for n in data.movie_names.split(',') if n.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="No movie names provided")

    def run_bulk():
        for name in names:
            try:
                db.add_log('INFO', f'Bulk add: processing "{name}"')
                processor.process_movie(title=name, source='bulk_add')
            except Exception as e:
                db.add_log('ERROR', f'Bulk add failed for "{name}": {e}', exc_info=e)

    background_tasks.add_task(run_bulk)
    return {"message": f"Bulk add started for {len(names)} movie(s)", "count": len(names)}

# ---------------------------------------------------------------------------
# IMDB refresh for a single movie
# ---------------------------------------------------------------------------
@app.post("/api/movies/{movie_id}/refresh-imdb")
def refresh_movie_imdb(movie_id: int, background_tasks: BackgroundTasks, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    def run_refresh():
        result = processor.refresh_imdb_data(movie_id)
        db.add_log('INFO' if result.get('success') else 'WARNING',
                   f'IMDB refresh for movie {movie_id}', result)

    background_tasks.add_task(run_refresh)
    return {"message": f"IMDB refresh started for '{movie['title']}'"}

# ---------------------------------------------------------------------------
# Library-wide refresh
# ---------------------------------------------------------------------------
@app.post("/api/movies/refresh-library")
def refresh_library(background_tasks: BackgroundTasks, token: str):
    verify_token(token)

    def run_refresh():
        result = processor.refresh_library()
        db.add_log('INFO', 'Library refresh complete', result)

    background_tasks.add_task(run_refresh)
    return {"message": "Library refresh started in background — all movies will be updated."}

# ---------------------------------------------------------------------------
# Manual movie info correction
# ---------------------------------------------------------------------------
@app.post("/api/movies/{movie_id}/manual-update")
def manual_update_movie(movie_id: int, data: ManualMovieUpdate, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    try:
        result = processor.manual_update_movie(
            movie_id=movie_id,
            title=data.title,
            year=data.year,
            imdb_id=data.imdb_id,
        )
        return result
    except Exception as e:
        db.add_log('ERROR', f'Manual update error for movie {movie_id}: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Re-download torrent for a movie
# ---------------------------------------------------------------------------
@app.post("/api/movies/{movie_id}/redownload")
def redownload_torrent(movie_id: int, background_tasks: BackgroundTasks, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")

    def run_redownload():
        result = processor.redownload_torrent(movie_id)
        db.add_log('INFO' if result.get('success') else 'WARNING',
                   f'Redownload result for movie {movie_id}', result)

    background_tasks.add_task(run_redownload)
    return {"message": f"Torrent re-download started for '{movie['title']}'"}

# ---------------------------------------------------------------------------
# Add a single movie to Radarr
# ---------------------------------------------------------------------------
@app.post("/api/movies/{movie_id}/add-to-radarr")
def add_movie_to_radarr(movie_id: int, token: str):
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    if not processor.radarr:
        raise HTTPException(status_code=400, detail="Radarr is not configured")
    try:
        success = processor.add_to_radarr(
            title=movie['title'],
            year=movie.get('year'),
            tmdb_id=int(movie['tmdb_id']) if movie.get('tmdb_id') else None
        )
        if success:
            db.update_movie(movie_id, {'added_to_radarr': True})
        return {"success": success,
                "message": "Added to Radarr" if success else "Failed to add to Radarr"}
    except Exception as e:
        db.add_log('ERROR', f'add-to-radarr error for movie {movie_id}: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Domain finder — find current 1tamilmv domain via Google
# ---------------------------------------------------------------------------
@app.post("/api/settings/find-domain")
def find_domain(token: str):
    verify_token(token)
    try:
        from api_clients import DomainFinder
        website_base = db.get_setting('website_base', '1tamilmv')
        db.add_log('INFO', f'Running domain search for: {website_base}')
        finder = DomainFinder()
        domain = finder.find_domain(website_base)
        if domain:
            db.add_log('INFO', f'Domain finder result: {domain}', {'website_base': website_base})
            return {"success": True, "domain": domain}
        else:
            db.add_log('WARNING', f'Domain finder could not locate domain for: {website_base}')
            return {"success": False, "domain": None}
    except Exception as e:
        db.add_log('ERROR', f'Domain finder error: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/settings/update-domain")
def update_domain(token: str, domain: str):
    """Update forum_url and search_url to use a new domain."""
    verify_token(token)
    try:
        import re
        forum_url  = db.get_setting('forum_url', '')
        search_url = db.get_setting('search_url', '')

        # Replace the host portion with the new domain
        new_forum  = re.sub(r'https?://[^/]+', f'https://{domain}', forum_url)
        new_search = re.sub(r'https?://[^/]+', f'https://{domain}', search_url)

        db.update_settings({
            'full_domain': domain,
            'forum_url':   new_forum,
            'search_url':  new_search,
        })
        processor.refresh_clients()
        db.add_log('INFO', f'Domain updated to {domain}',
                   {'forum_url': new_forum, 'search_url': new_search})
        return {"success": True, "forum_url": new_forum, "search_url": new_search}
    except Exception as e:
        db.add_log('ERROR', f'update-domain error: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Radarr integration
# ---------------------------------------------------------------------------
@app.get("/api/radarr/root-folders")
def get_radarr_root_folders(token: str):
    """Return all root folders configured in Radarr so the UI can offer a picker."""
    verify_token(token)
    if not processor.radarr:
        raise HTTPException(status_code=400, detail="Radarr not configured")
    try:
        folders = processor.radarr.get_root_folders()
        return {"folders": [f.get("path", "") for f in folders]}
    except Exception as e:
        db.add_log('ERROR', f'Could not fetch Radarr root folders: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/radarr/tamil-movies")
def get_radarr_tamil_movies(token: str):
    verify_token(token)
    if not processor.radarr:
        raise HTTPException(status_code=400, detail="Radarr not configured")
    try:
        tamil_movies = processor.radarr.get_tamil_movies()
        return {"movies": tamil_movies}
    except Exception as e:
        db.add_log('ERROR', f'Could not fetch Tamil movies from Radarr: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/radarr/sync-tamil")
def sync_tamil_movies(background_tasks: BackgroundTasks, token: str):
    verify_token(token)
    if not processor.radarr:
        raise HTTPException(status_code=400, detail="Radarr not configured")

    def run_sync():
        try:
            results = processor.sync_tamil_movies_from_radarr()
            saved     = sum(1 for r in results if r.get('action') == 'saved_radarr_has_file')
            processed = len(results) - saved
            db.add_log('INFO',
                       f'Radarr Tamil sync complete',
                       {'total': len(results),
                        'already_had_file_saved': saved,
                        'downloaded_from_forum': processed})
        except Exception as e:
            db.add_log('ERROR', f'Radarr Tamil sync background task crashed: {e}', exc_info=e)

    background_tasks.add_task(run_sync)
    return {"message": "Radarr Tamil sync started in background"}

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
@app.get("/api/logs")
def get_logs(token: str, limit: int = 100, level: Optional[str] = None):
    verify_token(token)
    logs = db.get_logs(limit=limit, level=level)
    return {"logs": logs}

@app.get("/api/logs/stream")
async def stream_logs(token: str, request: Request):
    """Server-Sent Events endpoint — pushes new log lines in real time."""
    verify_token(token)

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _log_subscribers.append(queue)

    async def event_generator():
        try:
            # Send a keep-alive comment immediately
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive ping
                    yield ": ping\n\n"
        finally:
            _log_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.delete("/api/logs")
def clear_logs(token: str, days: int = 30):
    verify_token(token)
    db.clear_old_logs(days=days)
    return {"success": True}

# ---------------------------------------------------------------------------
# Radarr file status for movie details modal
# ---------------------------------------------------------------------------
@app.get("/api/movies/{movie_id}/radarr-status")
def get_radarr_file_status(movie_id: int, token: str):
    """Return live Radarr file status for a specific movie."""
    verify_token(token)
    movie = db.get_movie_by_id(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    if not processor.radarr:
        return {"in_radarr": False, "has_file": False, "error": "Radarr not configured"}
    try:
        status_info = processor.get_radarr_file_status(
            title=movie['title'],
            year=movie.get('year'),
            imdb_id=movie.get('imdb_id'),
        )
        return status_info
    except Exception as e:
        db.add_log('ERROR', f'radarr-status error for movie {movie_id}: {e}', exc_info=e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def get_stats(token: str):
    verify_token(token)
    with db.get_connection() as conn:
        cur = conn.cursor()

        cur.execute('SELECT COUNT(*) FROM movies')
        total_movies = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM movies WHERE is_downloaded = TRUE')
        downloaded = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM movies WHERE is_downloaded = FALSE AND rejection_reason IS NULL')
        pending = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM movies WHERE rejection_reason IS NOT NULL')
        rejected = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM movies WHERE added_to_radarr = TRUE')
        in_radarr = cur.fetchone()[0]

    return {
        'total_movies': total_movies,
        'downloaded':   downloaded,
        'pending':      pending,
        'rejected':     rejected,
        'in_radarr':    in_radarr,
    }

# ---------------------------------------------------------------------------
# Scheduler Status
# ---------------------------------------------------------------------------
@app.get("/api/scheduler/status")
def get_scheduler_status(token: str):
    """Get current scheduler status and scheduled tasks"""
    verify_token(token)
    import schedule
    
    jobs_info = []
    for job in schedule.jobs:
        jobs_info.append({
            'next_run': job.next_run.isoformat() if job.next_run else None,
            'interval': str(job.interval),
            'unit': job.unit,
            'at_time': str(job.at_time) if job.at_time else None,
            'job_func': job.job_func.__name__ if hasattr(job.job_func, '__name__') else str(job.job_func)
        })
    
    return {
        'running': scheduler.running,
        'jobs': jobs_info,
        'jobs_count': len(schedule.jobs)
    }

@app.post("/api/scheduler/reload")
def reload_scheduler(token: str):
    """Manually reload scheduler tasks"""
    verify_token(token)
    scheduler.reload_tasks()
    return {"success": True, "message": "Scheduler tasks reloaded"}

@app.post("/api/scheduler/run-now")
def run_scheduled_scan_now(background_tasks: BackgroundTasks, token: str):
    """Run the daily forum scan immediately (doesn't affect schedule)"""
    verify_token(token)
    
    def run_scan():
        db.add_log('INFO', 'Manual run-now triggered via API')
        try:
            scheduler._daily_forum_scan()
        except Exception as e:
            db.add_log('ERROR', f'Manual run-now scan failed: {e}', exc_info=e)
    
    background_tasks.add_task(run_scan)
    db.add_log('INFO', 'Manual run-now: background task queued')
    return {"success": True, "message": "Scheduled scan started (running in background)"}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# ---------------------------------------------------------------------------
# Frontend static serving
# ---------------------------------------------------------------------------
frontend_path = Path(__file__).parent.parent / "frontend"

@app.get("/")
async def serve_root():
    return FileResponse(str(frontend_path / "index.html"))

app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(db.get_setting('port', '4040'))

    print(f"""
    ╔══════════════════════════════════════════╗
    ║   Movie Automator Server Starting...    ║
    ╠══════════════════════════════════════════╣
    ║  Server:   http://localhost:{port:<5}      ║
    ║  API Docs: http://localhost:{port:<5}/docs ║
    ║  DB:       PostgreSQL ({os.environ.get('DB_HOST','localhost')})    ║
    ╚══════════════════════════════════════════╝
    """)

    scheduler.start()

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    finally:
        scheduler.stop()
