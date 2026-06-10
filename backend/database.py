import psycopg2
import psycopg2.pool
import psycopg2.extras
from datetime import datetime
import json
import os
import traceback
from pathlib import Path
from contextlib import contextmanager

# PostgreSQL connection settings - pulled from environment variables with sane defaults
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', 'localhost'),
    'port':     int(os.environ.get('DB_PORT', '5432')),
    'dbname':   os.environ.get('DB_NAME', 'movie_automator'),
    'user':     os.environ.get('DB_USER', 'movie_user'),
    'password': os.environ.get('DB_PASSWORD', 'movie_password'),
}

class Database:
    def __init__(self):
        # Connection pool: min 2, max 20 connections
        # This eliminates "database locked" errors entirely — each thread
        # gets its own connection from the pool instead of fighting over one file.
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            **DB_CONFIG
        )
        self.init_db()

    @contextmanager
    def get_connection(self):
        """
        Context manager that borrows a connection from the pool and
        returns it automatically, even on exceptions.
        Using a context manager prevents connections being leaked when
        callers forget to call conn.close().
        """
        conn = self._pool.getconn()
        try:
            conn.autocommit = False          # explicit transactions
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------
    def init_db(self):
        """Create tables and indexes if they don't exist."""
        with self.get_connection() as conn:
            cur = conn.cursor()

            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS movies (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    year INTEGER,
                    original_language TEXT,
                    imdb_id TEXT,
                    imdb_rating REAL,
                    tmdb_id TEXT,
                    poster_url TEXT,
                    forum_url TEXT,
                    search_url TEXT,
                    is_downloaded BOOLEAN DEFAULT FALSE,
                    downloaded_quality TEXT,
                    file_size TEXT,
                    torrent_url TEXT,
                    torrent_name TEXT,
                    added_to_radarr BOOLEAN DEFAULT FALSE,
                    added_to_qbittorrent BOOLEAN DEFAULT FALSE,
                    source TEXT,
                    rejection_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS movie_qualities (
                    id SERIAL PRIMARY KEY,
                    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                    quality TEXT NOT NULL,
                    file_size TEXT,
                    torrent_url TEXT NOT NULL,
                    torrent_name TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id SERIAL PRIMARY KEY,
                    task_name TEXT UNIQUE NOT NULL,
                    last_run TIMESTAMPTZ,
                    next_run TIMESTAMPTZ,
                    status TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')

            # Indexes
            cur.execute('CREATE INDEX IF NOT EXISTS idx_movies_title       ON movies(title)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_movies_imdb_id     ON movies(imdb_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_movies_downloaded  ON movies(is_downloaded)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_logs_created_at    ON logs(created_at)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_logs_level         ON logs(level)')

            # -- Migrations: safe to run on existing databases --
            # Visible flag for failed torrent downloads
            cur.execute('ALTER TABLE movies ADD COLUMN IF NOT EXISTS download_failed BOOLEAN DEFAULT FALSE')
            # Rip type (WEB-DL / HDRip / etc.) per torrent variant
            cur.execute('ALTER TABLE movie_qualities ADD COLUMN IF NOT EXISTS rip_type TEXT')
            # Make torrent_name nullable if it was created NOT NULL
            cur.execute('''
                DO $$
                BEGIN
                    ALTER TABLE movie_qualities ALTER COLUMN torrent_name DROP NOT NULL;
                EXCEPTION WHEN others THEN NULL;
                END $$;
            ''')

            # Default settings
            defaults = {
                'port':                 '4040',
                'forum_url':            'https://www.1tamilmv.cymru/index.php?/forums/forum/14/',
                'search_url':           'https://www.1tamilmv.cymru/index.php?/search/&q={query}&quick=1',
                'website_base':         'www.1tamilmv',
                'qbittorrent_url':      '',
                'qbittorrent_username': '',
                'qbittorrent_password': '',
                'radarr_url':           '',
                'radarr_api_key':       '',
                'omdb_api_key':         '',
                'tmdb_api_key':         '',
                'rating_threshold':     '6.5',
                'preferred_quality':    '1080p',
                'preferred_codec':      'HEVC',
                'daily_scan_enabled':   'true',
                'daily_scan_time':      '16:50',
                'scan_pages':           '3',
                'scan_links':           '50',
                'duplicate_stop_count': '5',
                'auto_download':        'true',
                'auto_start_enabled':   'false',
                'full_domain':          'www.1tamilmv.cymru',
            }
            for key, value in defaults.items():
                cur.execute('''
                    INSERT INTO settings (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO NOTHING
                ''', (key, value))

    # ------------------------------------------------------------------
    # User methods
    # ------------------------------------------------------------------
    def create_user(self, username, password_hash):
        with self.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    'INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id',
                    (username, password_hash)
                )
                return cur.fetchone()[0]
            except psycopg2.IntegrityError:
                return None

    def get_user_by_username(self, username):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM users WHERE username = %s', (username,))
            row = cur.fetchone()
            return dict(row) if row else None

    def has_users(self):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM users')
            return cur.fetchone()[0] > 0

    # ------------------------------------------------------------------
    # Settings methods
    # ------------------------------------------------------------------
    def get_setting(self, key, default=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT value FROM settings WHERE key = %s', (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def set_setting(self, key, value):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            ''', (key, value))

    def get_all_settings(self):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT key, value FROM settings')
            return {row[0]: row[1] for row in cur.fetchall()}

    def update_settings(self, settings_dict):
        with self.get_connection() as conn:
            cur = conn.cursor()
            for key, value in settings_dict.items():
                cur.execute('''
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                ''', (key, value))

    # ------------------------------------------------------------------
    # Movie methods
    # ------------------------------------------------------------------
    def add_movie(self, movie_data):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO movies (
                    title, year, original_language, imdb_id, imdb_rating, tmdb_id,
                    poster_url, forum_url, search_url, is_downloaded, downloaded_quality,
                    file_size, torrent_url, torrent_name, added_to_radarr,
                    added_to_qbittorrent, source, rejection_reason, download_failed, updated_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()
                ) RETURNING id
            ''', (
                movie_data.get('title'),
                movie_data.get('year'),
                movie_data.get('original_language'),
                movie_data.get('imdb_id'),
                movie_data.get('imdb_rating'),
                movie_data.get('tmdb_id'),
                movie_data.get('poster_url'),
                movie_data.get('forum_url'),
                movie_data.get('search_url'),
                movie_data.get('is_downloaded', False),
                movie_data.get('downloaded_quality'),
                movie_data.get('file_size'),
                movie_data.get('torrent_url'),
                movie_data.get('torrent_name'),
                movie_data.get('added_to_radarr', False),
                movie_data.get('added_to_qbittorrent', False),
                movie_data.get('source'),
                movie_data.get('rejection_reason'),
                movie_data.get('download_failed', False),
            ))
            return cur.fetchone()[0]

    def update_movie(self, movie_id, movie_data):
        if not movie_data:
            return
        with self.get_connection() as conn:
            cur = conn.cursor()
            fields = [f"{k} = %s" for k in movie_data.keys()]
            fields.append("updated_at = NOW()")
            values = list(movie_data.values()) + [movie_id]
            cur.execute(
                f"UPDATE movies SET {', '.join(fields)} WHERE id = %s",
                values
            )

    def get_movie_by_id(self, movie_id):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM movies WHERE id = %s', (movie_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_movie_by_title_year(self, title, year=None):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if year:
                cur.execute('SELECT * FROM movies WHERE title = %s AND year = %s', (title, year))
            else:
                cur.execute('SELECT * FROM movies WHERE title = %s', (title,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_movies(self, filters=None):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            query = 'SELECT * FROM movies'
            params = []
            conditions = []

            if filters:
                if 'is_downloaded' in filters:
                    conditions.append('is_downloaded = %s')
                    params.append(bool(filters['is_downloaded']))
                if 'source' in filters:
                    conditions.append('source = %s')
                    params.append(filters['source'])
                if 'rejection_reason' in filters:
                    if filters['rejection_reason']:
                        conditions.append('rejection_reason IS NOT NULL')
                    else:
                        conditions.append('rejection_reason IS NULL')

            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
            query += ' ORDER BY created_at DESC'

            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def movie_exists(self, title, year=None):
        return self.get_movie_by_title_year(title, year) is not None

    def delete_movie(self, movie_id):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM movies WHERE id = %s', (movie_id,))

    # ------------------------------------------------------------------
    # Movie qualities
    # ------------------------------------------------------------------
    def add_movie_quality(self, movie_id, quality_data):
        # The quality column is NOT NULL — derive a label when the scraper
        # couldn't detect a resolution tag (e.g. "x264 - 700MB" entries).
        quality = quality_data.get('quality')
        if not quality:
            codec     = quality_data.get('codec') or ''
            file_size = quality_data.get('file_size') or ''
            quality   = ' '.join(filter(None, [codec, file_size])).strip() or 'Unknown'

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO movie_qualities (movie_id, quality, file_size, torrent_url, torrent_name, rip_type)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (
                movie_id,
                quality,
                quality_data.get('file_size'),
                quality_data.get('torrent_url'),
                quality_data.get('torrent_name'),
                quality_data.get('rip_type'),
            ))

    def get_movie_qualities(self, movie_id):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM movie_qualities WHERE movie_id = %s', (movie_id,))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Logging — with full detail
    # ------------------------------------------------------------------
    def add_log(self, level: str, message: str, context=None, exc_info=None):
        """
        Add a log entry.

        Parameters
        ----------
        level   : 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
        message : human-readable message
        context : optional dict of extra key/value data
        exc_info: pass sys.exc_info() or an Exception to capture full traceback
        """
        ctx = dict(context) if context else {}

        # If an exception object was supplied, capture its full traceback
        if exc_info is not None:
            if isinstance(exc_info, BaseException):
                ctx['exception_type'] = type(exc_info).__name__
                ctx['exception_message'] = str(exc_info)
                ctx['traceback'] = traceback.format_exception(
                    type(exc_info), exc_info, exc_info.__traceback__
                )
            elif isinstance(exc_info, tuple) and len(exc_info) == 3:
                # sys.exc_info() tuple
                if exc_info[0] is not None:
                    ctx['exception_type'] = exc_info[0].__name__
                    ctx['exception_message'] = str(exc_info[1])
                    ctx['traceback'] = traceback.format_exception(*exc_info)

        ctx_str = json.dumps(ctx, default=str) if ctx else None

        # Also print to stdout so the terminal/systemd journal always has it
        print(f"[{level}] {message}" + (f" | {json.dumps(ctx, default=str)}" if ctx else ''))

        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    'INSERT INTO logs (level, message, context) VALUES (%s, %s, %s)',
                    (level, message, ctx_str)
                )
        except Exception as e:
            # Logging must never crash the caller
            print(f"[LOGGING ERROR] Could not write log entry: {e}")

    def get_logs(self, limit=100, level=None):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if level:
                cur.execute(
                    'SELECT * FROM logs WHERE level = %s ORDER BY created_at DESC LIMIT %s',
                    (level, limit)
                )
            else:
                cur.execute(
                    'SELECT * FROM logs ORDER BY created_at DESC LIMIT %s',
                    (limit,)
                )
            return [dict(r) for r in cur.fetchall()]

    def clear_old_logs(self, days=30):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM logs WHERE created_at < NOW() - INTERVAL '%s days'",
                (days,)
            )

    # ------------------------------------------------------------------
    # Scheduled tasks
    # ------------------------------------------------------------------
    def update_task(self, task_name, last_run=None, next_run=None, status=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO scheduled_tasks (task_name, last_run, next_run, status)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (task_name) DO UPDATE SET
                    last_run = COALESCE(EXCLUDED.last_run, scheduled_tasks.last_run),
                    next_run = COALESCE(EXCLUDED.next_run, scheduled_tasks.next_run),
                    status   = COALESCE(EXCLUDED.status,   scheduled_tasks.status)
            ''', (task_name, last_run, next_run, status))

    def get_task(self, task_name):
        with self.get_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute('SELECT * FROM scheduled_tasks WHERE task_name = %s', (task_name,))
            row = cur.fetchone()
            return dict(row) if row else None
