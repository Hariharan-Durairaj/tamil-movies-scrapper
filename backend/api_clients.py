import requests
from typing import Optional, Dict, List
import time
import json
import re
import urllib.parse
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Optional Selenium imports — used by IMDBScraper and DomainFinder.
# ---------------------------------------------------------------------------
# NOTE: We do NOT set _SELENIUM_AVAILABLE at module load time here.
# undetected-chromedriver can raise non-ImportError exceptions during import
# (e.g. OSError when it probes for the Chrome binary) which would silently
# set the flag to False even though the package is installed.
# Instead, each consumer does its own lazy import and handles failures locally.
# ---------------------------------------------------------------------------
def _import_selenium():
    """Lazy-import undetected-chromedriver + Selenium. Returns True on success."""
    global uc, By, WebDriverWait, EC, _SELENIUM_AVAILABLE
    if globals().get('_SELENIUM_AVAILABLE') is True:
        return True
    try:
        import undetected_chromedriver as _uc
        from selenium.webdriver.common.by import By as _By
        from selenium.webdriver.support.ui import WebDriverWait as _WDW
        from selenium.webdriver.support import expected_conditions as _EC
        globals()['uc'] = _uc
        globals()['By'] = _By
        globals()['WebDriverWait'] = _WDW
        globals()['EC'] = _EC
        globals()['_SELENIUM_AVAILABLE'] = True
        return True
    except Exception as e:
        print(f"[SELENIUM] Import failed: {e}")
        globals()['_SELENIUM_AVAILABLE'] = False
        return False

# Attempt import at load time (best-effort; failures are non-fatal)
_import_selenium()


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------
import os as _os
import shutil as _shutil

def _chromium_binary() -> str:
    """
    Return the path to the Chromium/Chrome binary.

    On Debian/Ubuntu the 'chromium' apt package installs the binary at
    /usr/bin/chromium (sometimes /usr/bin/chromium-browser).
    undetected-chromedriver looks for 'google-chrome' / 'google-chrome-stable'
    by default and fails to find the system Chromium unless we tell it explicitly.
    """
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in candidates:
        if _os.path.isfile(path):
            return path
    # Fall back to whatever is on PATH
    found = _shutil.which("chromium") or _shutil.which("chromium-browser") or _shutil.which("google-chrome")
    if found:
        return found
    raise FileNotFoundError(
        "No Chromium/Chrome binary found. "
        "Install 'chromium' or 'google-chrome-stable'."
    )


def _chromium_version() -> int:
    """
    Detect the installed Chromium major version at runtime by running
    'chromium --version' and parsing the output.

    This is passed to uc.Chrome(version_main=...) so undetected-chromedriver
    downloads the matching ChromeDriver automatically — no hardcoded version
    number that breaks every time the OS upgrades Chromium.
    """
    import subprocess
    binary = _chromium_binary()
    try:
        out = subprocess.check_output(
            [binary, "--version"], stderr=subprocess.DEVNULL, timeout=10
        )
        # e.g. "Chromium 148.0.7778.178" or "Google Chrome 148.0.7778.178 snap"
        version_str = out.decode().strip()
        major = int(version_str.split()[1].split(".")[0])
        print(f"[DRIVER] Detected Chromium major version: {major}")
        return major
    except Exception as e:
        print(f"[DRIVER] Could not detect Chromium version ({e}), defaulting to 148")
        return 148


def _chrome_options_base() -> "uc.ChromeOptions":
    """Shared uc.ChromeOptions required in every Docker/LXC context."""
    _import_selenium()
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return options


def _make_headless_driver():
    """Return a headless uc.Chrome instance for IMDB (bot-bypass via uc).

    version_main is detected at runtime from the installed Chromium binary
    so it always matches, regardless of which version the OS ships.
    """
    _import_selenium()
    options = _chrome_options_base()
    options.add_argument("--headless=new")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    )
    return uc.Chrome(
        version_main=_chromium_version(),
        browser_executable_path=_chromium_binary(),
        options=options,
    )


def _make_visible_driver():
    """Return a headful uc.Chrome instance on the Xvfb virtual display.

    No --headless flag — Chrome renders into the Xvfb framebuffer at DISPLAY=:99
    (started by entrypoint.sh), giving it a real viewport so Intersection Observer
    and visibility-based lazy-loading fire correctly on search result pages.
    Bot-bypass via uc is also active here (useful for Bing/Brave challenges).

    version_main is detected at runtime to always match the installed Chromium.
    """
    _import_selenium()
    options = _chrome_options_base()
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    )
    return uc.Chrome(
        version_main=_chromium_version(),
        browser_executable_path=_chromium_binary(),
        options=options,
    )


# ===========================================================================
# OMDB
# ===========================================================================
class OMDBClient:
    """OMDB API Client"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "http://www.omdbapi.com/"

    def get_movie_info(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        try:
            params = {'apikey': self.api_key, 't': title}
            if year:
                params['y'] = year
            response = requests.get(self.base_url, params=params, timeout=10)
            data = response.json()
            if data.get('Response') == 'True':
                return {
                    'title':       data.get('Title'),
                    'year':        data.get('Year'),
                    'imdb_rating': data.get('imdbRating'),
                    'imdb_id':     data.get('imdbID'),
                    'poster':      data.get('Poster'),
                    'plot':        data.get('Plot'),
                    'genre':       data.get('Genre'),
                    'language':    data.get('Language'),
                }
            print(f"[OMDB] Movie not found: {title}")
            return None
        except Exception as e:
            print(f"[OMDB] Error fetching movie info: {e}")
            return None

    def get_by_imdb_id(self, imdb_id: str) -> Optional[Dict]:
        try:
            params = {'apikey': self.api_key, 'i': imdb_id}
            response = requests.get(self.base_url, params=params, timeout=10)
            data = response.json()
            if data.get('Response') == 'True':
                return {
                    'title':       data.get('Title'),
                    'year':        data.get('Year'),
                    'imdb_rating': data.get('imdbRating'),
                    'imdb_id':     data.get('imdbID'),
                    'poster':      data.get('Poster'),
                    'plot':        data.get('Plot'),
                    'genre':       data.get('Genre'),
                    'language':    data.get('Language'),
                }
            return None
        except Exception as e:
            print(f"[OMDB] Error fetching by IMDB ID: {e}")
            return None


# ===========================================================================
# TMDB
# ===========================================================================
class TMDBClient:
    """TMDB API Client"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"

    def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        try:
            params = {'api_key': self.api_key, 'query': title}
            if year:
                params['year'] = year
            response = requests.get(f"{self.base_url}/search/movie",
                                    params=params, timeout=10)
            data = response.json()
            if data.get('results') and len(data['results']) > 0:
                movie = data['results'][0]
                movie_id = movie['id']
                external_ids = self.get_external_ids(movie_id)
                return {
                    'title':             movie.get('title'),
                    'year':              movie.get('release_date', '')[:4] if movie.get('release_date') else None,
                    'tmdb_id':           str(movie_id),
                    'imdb_id':           external_ids.get('imdb_id'),
                    'poster':            (f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}"
                                         if movie.get('poster_path') else None),
                    'overview':          movie.get('overview'),
                    'vote_average':      movie.get('vote_average'),
                    'original_language': movie.get('original_language'),
                }
            return None
        except Exception as e:
            print(f"[TMDB] Error searching movie: {e}")
            return None

    def get_external_ids(self, tmdb_id: int) -> Dict:
        try:
            params = {'api_key': self.api_key}
            response = requests.get(f"{self.base_url}/movie/{tmdb_id}/external_ids",
                                    params=params, timeout=10)
            return response.json()
        except:
            return {}

    def get_movie_details(self, tmdb_id: int) -> Optional[Dict]:
        try:
            params = {'api_key': self.api_key}
            response = requests.get(f"{self.base_url}/movie/{tmdb_id}",
                                    params=params, timeout=10)
            return response.json()
        except Exception as e:
            print(f"[TMDB] Error getting movie details: {e}")
            return None


# ===========================================================================
# Radarr
# ===========================================================================
class RadarrClient:
    """Radarr API Client"""
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.headers = {'X-Api-Key': api_key}

    def test_connection(self) -> bool:
        try:
            response = requests.get(f"{self.url}/api/v3/system/status",
                                    headers=self.headers, timeout=10)
            return response.status_code == 200
        except:
            return False

    def get_movies(self) -> List[Dict]:
        try:
            response = requests.get(f"{self.url}/api/v3/movie",
                                    headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            print(f"[RADARR] Error getting movies: {e}")
            return []

    def get_tamil_movies(self) -> List[Dict]:
        try:
            movies = self.get_movies()
            return [m for m in movies
                    if m.get('originalLanguage', {}).get('name') == 'Tamil']
        except Exception as e:
            print(f"[RADARR] Error getting Tamil movies: {e}")
            return []

    def add_movie(self, tmdb_id: int, quality_profile_id: int = 1,
                  root_folder: str = None, monitored: bool = True) -> Optional[Dict]:
        try:
            response = requests.get(f"{self.url}/api/v3/movie/lookup/tmdb",
                                    headers=self.headers,
                                    params={'tmdbId': tmdb_id}, timeout=10)
            if response.status_code != 200:
                print(f"[RADARR] Failed to lookup movie: {tmdb_id}")
                return None
            movie_info = response.json()
            return self._add_from_lookup(movie_info, quality_profile_id, root_folder, monitored)
        except Exception as e:
            print(f"[RADARR] Error adding movie: {e}")
            return None

    def _get_root_folder(self) -> Optional[str]:
        """Return the first configured root folder path from Radarr, or None."""
        try:
            resp = requests.get(f"{self.url}/api/v3/rootfolder",
                                headers=self.headers, timeout=10)
            if resp.status_code == 200:
                folders = resp.json()
                if folders:
                    return folders[0]['path']
        except Exception as e:
            print(f"[RADARR] Error fetching root folders: {e}")
        return None

    def get_root_folders(self) -> List[Dict]:
        """Return all configured root folders from Radarr."""
        try:
            resp = requests.get(f"{self.url}/api/v3/rootfolder",
                                headers=self.headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"[RADARR] Error fetching root folders: {e}")
        return []

    def _add_from_lookup(self, movie_info: Dict, quality_profile_id: int = 1,
                         root_folder: str = None, monitored: bool = True) -> Optional[Dict]:
        """POST a movie to Radarr given a lookup result dict."""
        if not root_folder:
            root_folder = self._get_root_folder()
        movie_data = {
            'title':            movie_info.get('title'),
            'tmdbId':           movie_info.get('tmdbId'),
            'qualityProfileId': quality_profile_id,
            'rootFolderPath':   root_folder,
            'monitored':        monitored,
            'addOptions':       {'searchForMovie': True},
        }
        response = requests.post(f"{self.url}/api/v3/movie",
                                 headers=self.headers,
                                 json=movie_data, timeout=10)
        if response.status_code in [200, 201]:
            print(f"[RADARR] Successfully added movie: {movie_info.get('title')} "
                  f"→ {root_folder}")
            return response.json()
        print(f"[RADARR] Failed to add movie: {response.text}")
        return None

    def add_movie_by_imdb_id(self, imdb_id: str, quality_profile_id: int = 1,
                              root_folder: str = None, monitored: bool = True) -> Optional[Dict]:
        """Look up a movie in Radarr by IMDB ID and add it."""
        try:
            response = requests.get(f"{self.url}/api/v3/movie/lookup/imdb",
                                    headers=self.headers,
                                    params={'imdbId': imdb_id}, timeout=10)
            if response.status_code != 200:
                print(f"[RADARR] IMDB lookup failed for {imdb_id}: HTTP {response.status_code}")
                return None
            movie_info = response.json()
            return self._add_from_lookup(movie_info, quality_profile_id, root_folder, monitored)
        except Exception as e:
            print(f"[RADARR] Error adding movie by IMDB ID {imdb_id}: {e}")
            return None

    def add_movie_by_title(self, title: str, year: Optional[int] = None,
                           quality_profile_id: int = 1,
                           root_folder: str = None, monitored: bool = True) -> Optional[Dict]:
        """
        Search Radarr's own lookup endpoint by title and add the best match.
        This works even when the movie isn't on TMDB yet (Radarr uses its own
        internal index which may have newer or regional titles).
        """
        try:
            term = f"{title} {year}" if year else title
            response = requests.get(f"{self.url}/api/v3/movie/lookup",
                                    headers=self.headers,
                                    params={'term': term}, timeout=10)
            if response.status_code != 200:
                msg = f"HTTP {response.status_code}"
                if response.status_code == 503:
                    msg += " — Radarr cannot reach TMDB. Check Radarr's own internet access (DNS/firewall inside its LXC or Docker network)."
                print(f"[RADARR] Title lookup failed for '{term}': {msg}")
                return None
            results = response.json()
            if not results:
                print(f"[RADARR] No lookup results for '{term}'")
                return None

            # Prefer an exact title+year match; fall back to first result
            title_lower = title.lower().strip()
            chosen = None
            for r in results:
                r_title = (r.get('title') or '').lower().strip()
                r_year  = r.get('year')
                if r_title == title_lower and (year is None or r_year == year):
                    chosen = r
                    break
            if not chosen:
                chosen = results[0]
                print(f"[RADARR] No exact match for '{term}' — using '{chosen.get('title')}'")

            return self._add_from_lookup(chosen, quality_profile_id, root_folder, monitored)
        except Exception as e:
            print(f"[RADARR] Error adding movie by title '{title}': {e}")
            return None

    def movie_exists(self, tmdb_id: int) -> bool:
        try:
            movies = self.get_movies()
            return any(m.get('tmdbId') == tmdb_id for m in movies)
        except:
            return False

    def find_movie(self, title: str, year: Optional[int] = None,
                   imdb_id: Optional[str] = None) -> Optional[Dict]:
        """
        Search Radarr's library for a movie by title+year or IMDB ID.
        Returns the full Radarr movie dict (including hasFile, movieFile, etc.) or None.
        """
        try:
            movies = self.get_movies()
            title_l = title.lower().strip()

            # First try IMDB ID match (most reliable)
            if imdb_id:
                for m in movies:
                    if (m.get('imdbId') or '').lower() == imdb_id.lower():
                        return m

            # Then title + optional year match
            for m in movies:
                radarr_title = (m.get('title') or '').lower().strip()
                radarr_year  = m.get('year')
                title_match  = (radarr_title == title_l)
                year_match   = (year is None or radarr_year is None or radarr_year == year)
                if title_match and year_match:
                    return m

            return None
        except Exception as e:
            print(f"[RADARR] Error finding movie '{title}': {e}")
            return None

    def get_movie_file_info(self, radarr_movie: Dict) -> Dict:
        """
        Extract file information from a Radarr movie dict.
        Returns a dict with: has_file, file_path, file_size_bytes, quality, date_added
        """
        has_file = bool(radarr_movie.get('hasFile', False))
        info = {
            'has_file': has_file,
            'file_path': None,
            'file_size_bytes': None,
            'quality': None,
            'date_added': None,
        }
        if has_file and radarr_movie.get('movieFile'):
            mf = radarr_movie['movieFile']
            info['file_path']       = mf.get('relativePath') or mf.get('path')
            info['file_size_bytes'] = mf.get('size')
            info['date_added']      = mf.get('dateAdded')
            quality_obj = mf.get('quality', {})
            if quality_obj:
                q = quality_obj.get('quality', {})
                info['quality'] = q.get('name') or q.get('resolution')
        return info


# ===========================================================================
# qBittorrent
# ===========================================================================
class QBittorrentClient:
    """qBittorrent API Client"""
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.logged_in = False

    def login(self) -> bool:
        try:
            response = self.session.post(
                f"{self.url}/api/v2/auth/login",
                data={'username': self.username, 'password': self.password},
                timeout=10,
            )
            # Older qBittorrent returns 200 + "Ok."
            # Newer qBittorrent (5.x+) returns 204 with empty body
            self.logged_in = response.status_code in (200, 204) and response.text.strip() in ('Ok.', '')
            if self.logged_in:
                print(f"[QBITTORRENT] Successfully logged in (HTTP {response.status_code})")
            else:
                print(f"[QBITTORRENT] Login failed: HTTP {response.status_code} — {response.text!r}")
            return self.logged_in
        except Exception as e:
            print(f"[QBITTORRENT] Login error: {e}")
            return False

    def test_connection(self) -> bool:
        if not self.logged_in:
            return self.login()
        try:
            response = self.session.get(f"{self.url}/api/v2/app/version", timeout=10)
            return response.status_code == 200
        except:
            return False

    def _parse_add_response(self, response, label: str) -> bool:
        """Handle qBittorrent add response for both old (string) and new (JSON) formats."""
        # 409 Conflict = torrent already exists, treat as success
        if response.status_code == 409:
            print(f"[QBITTORRENT] Torrent already exists (skipping): {label}")
            return True
        if response.status_code != 200:
            print(f"[QBITTORRENT] Failed to add torrent: HTTP {response.status_code} — {response.text!r}")
            return False
        text = response.text.strip()
        # Old format: plain string "Ok."
        if text == 'Ok.':
            print(f"[QBITTORRENT] Successfully added: {label}")
            return True
        # New format (5.x+): JSON with success_count
        try:
            data = json.loads(text)
            if data.get('success_count', 0) > 0:
                print(f"[QBITTORRENT] Successfully added: {label}")
                return True
            if data.get('failure_count', 0) > 0:
                print(f"[QBITTORRENT] qBittorrent reported failure: {text}")
                return False
        except (json.JSONDecodeError, AttributeError):
            pass
        # Fallback: any 200 response is treated as success
        print(f"[QBITTORRENT] Successfully added (unrecognised response format): {label}")
        return True

    def add_torrent_file(self, torrent_path: str, category: str = "radarr",
                         save_path: str = None) -> bool:
        if not self.logged_in:
            if not self.login():
                return False
        try:
            with open(torrent_path, 'rb') as f:
                files = {'torrents': f}
                data = {'category': category}
                # save_path is intentionally not forwarded: the backend runs in Docker
                # and its internal paths don't exist on the qBittorrent LXC.
                # qBittorrent will use its own configured default save path instead.
                response = self.session.post(
                    f"{self.url}/api/v2/torrents/add",
                    files=files, data=data, timeout=30,
                )
            success = self._parse_add_response(response, torrent_path)
            return success
        except Exception as e:
            print(f"[QBITTORRENT] Error adding torrent: {e}")
            return False

    def add_torrent_url(self, torrent_url: str, category: str = "radarr",
                        save_path: str = None) -> bool:
        if not self.logged_in:
            if not self.login():
                return False
        try:
            data = {'urls': torrent_url, 'category': category}
            # save_path intentionally omitted — see add_torrent_file note above.
            response = self.session.post(
                f"{self.url}/api/v2/torrents/add",
                data=data, timeout=30,
            )
            success = self._parse_add_response(response, torrent_url)
            return success
        except Exception as e:
            print(f"[QBITTORRENT] Error adding torrent URL: {e}")
            return False

    def get_torrents(self, category: str = None) -> List[Dict]:
        if not self.logged_in:
            if not self.login():
                return []
        try:
            params = {}
            if category:
                params['category'] = category
            response = self.session.get(
                f"{self.url}/api/v2/torrents/info",
                params=params, timeout=10,
            )
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            print(f"[QBITTORRENT] Error getting torrents: {e}")
            return []


# ===========================================================================
# IMDBScraper  — Selenium / undetected-chromedriver implementation
# ===========================================================================
class IMDBScraper:
    """
    IMDB scraper using undetected-chromedriver (bypasses Cloudflare/bot detection).

    search_movie(title)       → searches IMDB, returns rich metadata dict or None
    get_rating_by_id(imdb_id) → fetches the movie page, returns rich metadata dict or None

    Both return a dict with:
        imdb_id, title, year, imdb_rating, vote_count,
        poster_url, genres, director, cast, plot, release_date, runtime_minutes
    """

    # ------------------------------------------------------------------
    def search_movie(self, search_term: str) -> Optional[Dict]:
        """
        Search IMDB for *search_term*.
        Prefers an exact (case-insensitive) title match; falls back to first result.
        """
        if not _import_selenium():
            print("[IMDB] selenium not available — cannot search")
            return None

        driver = _make_headless_driver()
        url = (
            "https://www.imdb.com/find/?q="
            + urllib.parse.quote(search_term)
            + "&s=tt&ttype=ft"
        )
        try:
            driver.get(url)
            time.sleep(5)

            script_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "__NEXT_DATA__"))
            )
            data = json.loads(script_el.get_attribute("innerHTML"))

            results = (
                data.get('props', {})
                    .get('pageProps', {})
                    .get('titleResults', {})
                    .get('results', [])
            )
            if not results:
                print(f"[IMDB] No search results for '{search_term}'")
                return None

            # Prefer exact match; fall back to first result
            chosen_li = None
            for r in results:
                li = r.get('listItem', {})
                if li.get('titleText', '').lower() == search_term.lower():
                    chosen_li = li
                    break
            if chosen_li is None:
                chosen_li = results[0].get('listItem', {})
                print(
                    f"[IMDB] No exact match for '{search_term}' — "
                    f"using: {chosen_li.get('titleText')}"
                )

            return self._parse_list_item(chosen_li)

        except Exception as e:
            print(f"[IMDB] search_movie error: {e}")
            return None
        finally:
            driver.quit()

    # ------------------------------------------------------------------
    def get_rating_by_id(self, imdb_id: str) -> Optional[Dict]:
        """
        Fetch IMDB movie page by IMDB ID and return rich metadata dict.
        Uses the application/ld+json script tag embedded in the page.
        """
        if not _import_selenium():
            print("[IMDB] selenium not available — cannot fetch by ID")
            return None

        driver = _make_headless_driver()
        url = f"https://www.imdb.com/title/{imdb_id}/"
        try:
            driver.get(url)
            time.sleep(5)

            script_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//script[@type='application/ld+json']")
                )
            )
            data = json.loads(script_el.get_attribute("innerHTML"))
            return self._parse_ld_json(imdb_id, data)

        except Exception as e:
            print(f"[IMDB] get_rating_by_id error for {imdb_id}: {e}")
            return None
        finally:
            driver.quit()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_list_item(li: Dict) -> Dict:
        """Parse a listItem from IMDB search __NEXT_DATA__ JSON."""
        imdb_id    = li.get('titleId')
        title      = li.get('titleText')
        year       = li.get('releaseYear')
        rating_s   = li.get('ratingSummary', {})
        rating     = rating_s.get('aggregateRating')
        vote_count = rating_s.get('voteCount')
        poster_url = li.get('primaryImage', {}).get('url')
        genres     = li.get('genres', [])

        # Runtime (seconds → minutes)
        runtime_s = li.get('runtime')
        runtime_m = round(runtime_s / 60) if runtime_s else None

        # Release date dict
        rel_date = li.get('releaseDate', {})
        if rel_date.get('year'):
            release_date_str = (
                f"{rel_date['year']}-"
                f"{str(rel_date.get('month', 1)).zfill(2)}-"
                f"{str(rel_date.get('day', 1)).zfill(2)}"
            )
        else:
            release_date_str = None

        # Cast / director from principalCredits
        principals = li.get('principalCredits', [])
        director   = None
        cast       = []
        for p in principals:
            cat   = p.get('category', {}).get('id', '').lower()
            names = [x.get('name', {}).get('nameText', '') for x in p.get('credits', [])]
            if 'director' in cat and not director:
                director = names[0] if names else None
            elif 'actor' in cat or 'actress' in cat:
                cast.extend(names)

        result = {
            'imdb_id':         imdb_id,
            'title':           title,
            'year':            year,
            'imdb_rating':     rating,
            'vote_count':      vote_count,
            'poster_url':      poster_url,
            'genres':          genres,
            'director':        director,
            'cast':            cast[:5],
            'plot':            li.get('plot', ''),
            'release_date':    release_date_str,
            'runtime_minutes': runtime_m,
        }
        print(
            f"[IMDB] {title} ({year}) | {rating}/10 | "
            + (f"{vote_count:,} votes" if vote_count else "no votes")
        )
        return result

    @staticmethod
    def _parse_ld_json(imdb_id: str, data: Dict) -> Dict:
        """Parse the application/ld+json block from an IMDB title page."""
        agg        = data.get('aggregateRating', {})
        rating     = agg.get('ratingValue')
        vote_count = agg.get('ratingCount')

        # Cast
        cast = [a.get('name') for a in data.get('actor', []) if a.get('name')]

        # Director
        directors = data.get('director', [])
        director  = directors[0].get('name') if directors else None

        # Genres
        genres = data.get('genre', [])
        if isinstance(genres, str):
            genres = [genres]

        # Duration: "PT2H16M" → minutes
        duration_str = data.get('duration', '')
        runtime_m = None
        if duration_str:
            h_m = re.search(r'(\d+)H', duration_str)
            m_m = re.search(r'(\d+)M', duration_str)
            h   = int(h_m.group(1)) if h_m else 0
            m   = int(m_m.group(1)) if m_m else 0
            runtime_m = h * 60 + m

        # Year from datePublished
        pub_date = data.get('datePublished', '')
        year_str = pub_date[:4] if pub_date else None

        result = {
            'imdb_id':         imdb_id,
            'title':           data.get('name'),
            'year':            int(year_str) if year_str and year_str.isdigit() else None,
            'imdb_rating':     rating,
            'vote_count':      vote_count,
            'poster_url':      data.get('image'),
            'genres':          genres,
            'director':        director,
            'cast':            cast[:5],
            'plot':            data.get('description', ''),
            'release_date':    pub_date or None,
            'runtime_minutes': runtime_m,
            'trailer_url':     data.get('trailer', {}).get('url'),
        }
        print(
            f"[IMDB] {result['title']} ({result['year']}) | {rating}/10 | "
            + (f"{vote_count:,} votes" if vote_count else "no votes")
        )
        return result


# ===========================================================================
# DomainFinder  — locate current 1tamilmv domain via search engines
# ===========================================================================
class DomainFinder:
    """
    Finds the current domain for a website whose TLD keeps changing.

    Strategy:
    1. Search DuckDuckGo (then Bing, then Brave) for the site base name.
    2. Collect up to `max_candidates` result URLs that contain the base name.
    3. For each candidate (in order), open it in Chrome, follow any redirects,
       land on the final URL, then verify it is the genuine site by checking
       for at least 2 of 3 known fingerprint strings in the page source.
    4. If verified → return that domain immediately.
    5. If no candidate passes verification → return all candidates so the
       frontend can show them for manual selection.

    Why follow redirects?
    ---------------------
    Search engines often list the old domain. When you click the link it
    redirects to the new domain. Chrome.get() follows HTTP and JS redirects
    automatically, so driver.current_url after page load gives us the real
    current domain, not the stale one shown in the search result.

    Fingerprint strings (2-of-3 required):
    - G-15B7F5LNBT      Google Analytics ID unique to 1TamilMV
    - t.me/tmvog         Telegram channel handle
    - data-focus-cookie='34'  Theme cookie baked into the HTML tag
    """

    _RESULT_WAIT = 10   # seconds to wait for search result links

    # At least this many fingerprints must be present to consider verified
    _VERIFY_MIN_MATCHES = 1
    _FINGERPRINTS = [
        "G-15B7F5LNBT",           # Google Analytics ID
        "t.me/tmvog",              # Telegram channel
        "data-focus-cookie='34'",  # Theme cookie ID
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_domain(self, website_base: str, max_candidates: int = 5, search_term: str = "") -> Dict:
        """
        Search for website_base and return a result dict:

        On verified success:
            {"verified": True,  "domain": "www.1tamilmv.xyz",  "candidates": [...]}

        On failure (no candidate passed verification):
            {"verified": False, "domain": None, "candidates": ["www.1tamilmv.old", ...]}

        `candidates` always contains every URL found so the frontend can
        offer a manual-selection fallback.

        search_term overrides the query used in all engines. Defaults to website_base.
        """
        if not _import_selenium():
            print("[DOMAIN] selenium not available — cannot search")
            return {"verified": False, "domain": None, "candidates": []}

        base_name = website_base.replace("www.", "").split(".")[0]
        query = search_term.strip() if search_term.strip() else website_base
        print(f"[DOMAIN] Searching for: {query} (base: {base_name}, max_candidates: {max_candidates})")

        engines = [
            ("Google",     self._collect_google),
            ("DuckDuckGo", self._collect_duckduckgo),
            ("Bing",       self._collect_bing),
            ("Brave",      self._collect_brave),
        ]

        all_candidates: list = []

        for engine_name, collect_fn in engines:
            if len(all_candidates) >= max_candidates:
                break
            driver = None
            try:
                print(f"[DOMAIN] Collecting candidates from {engine_name}...")
                driver = _make_visible_driver()
                found = collect_fn(driver, query, base_name, max_candidates)
                print(f"[DOMAIN] {engine_name} found {len(found)} candidate(s): {found}")
                for url in found:
                    if url not in all_candidates:
                        all_candidates.append(url)
                    if len(all_candidates) >= max_candidates:
                        break
            except Exception as e:
                print(f"[DOMAIN] {engine_name} collection error: {e}")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass

        if not all_candidates:
            print("[DOMAIN] No candidates found in any search engine")
            return {"verified": False, "domain": None, "candidates": []}

        print(f"[DOMAIN] Total candidates to verify: {all_candidates}")

        # Verify each candidate in order
        for i, candidate_url in enumerate(all_candidates):
            print(f"[DOMAIN] Verifying candidate {i+1}/{len(all_candidates)}: {candidate_url}")
            driver = None
            try:
                driver = _make_visible_driver()
                result = self._verify_candidate(driver, candidate_url)
                if result:
                    print(f"[DOMAIN] Verified! Final domain: {result}")
                    return {"verified": True, "domain": result, "candidates": all_candidates}
                else:
                    print(f"[DOMAIN] Candidate {candidate_url} failed verification")
            except Exception as e:
                print(f"[DOMAIN] Verification error for {candidate_url}: {e}")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass

        print("[DOMAIN] All candidates failed verification — returning list for manual selection")
        return {"verified": False, "domain": None, "candidates": all_candidates}

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _verify_candidate(self, driver, url: str) -> Optional[str]:
        """
        Open `url` in Chrome, wait for page load, follow any redirects,
        then check the page source for fingerprint strings.

        Returns the FINAL domain (after redirects) if verified, else None.

        Key insight: search engines list stale domains. When Chrome loads
        the link and the site has moved, it redirects automatically.
        driver.current_url after load gives the real current domain.
        """
        if not url.startswith("http"):
            url = "https://" + url
        try:
            driver.get(url)
            # Wait for body to be present — confirms page actually loaded
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception as e:
            print(f"[DOMAIN] Page load failed for {url}: {e}")
            return None

        # Get the final URL after all redirects
        final_url = driver.current_url
        final_domain = self._domain_from_url(final_url)
        print(f"[DOMAIN] Landed on: {final_url} (domain: {final_domain})")

        # Check fingerprints against page source
        page_source = driver.page_source
        matches = [fp for fp in self._FINGERPRINTS if fp in page_source]
        print(f"[DOMAIN] Fingerprint matches: {matches} ({len(matches)}/{len(self._FINGERPRINTS)})")

        if len(matches) >= self._VERIFY_MIN_MATCHES:
            return final_domain
        return None

    # ------------------------------------------------------------------
    # Per-engine candidate collectors
    # Each returns a list of full URLs (not just domains) containing base_name
    # ------------------------------------------------------------------

    def _collect_google(self, driver, website_base: str, base_name: str, limit: int) -> list:
        """Google Search — primary engine, best ranking quality."""
        query = urllib.parse.quote_plus(website_base)
        driver.get(f"https://www.google.com/search?q={query}&hl=en")
        try:
            WebDriverWait(driver, self._RESULT_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div#search a[jsname]"))
            )
        except Exception:
            time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        results = []

        # Google organic results: <a jsname="..."> inside div#search
        for a in soup.select("div#search a[jsname]"):
            href = a.get("href", "")
            if href.startswith("http") and base_name in href and href not in results:
                # Strip Google's /url?q= wrapper if present
                parsed = urllib.parse.urlparse(href)
                if parsed.path == "/url":
                    real = urllib.parse.parse_qs(parsed.query).get("q", [href])[0]
                    href = real
                if base_name in href and href not in results:
                    results.append(href)
            if len(results) >= limit:
                return results

        # Fallback: all links in search results area
        for a in soup.select("div#search a[href]"):
            href = a["href"]
            if href.startswith("/url?"):
                href = urllib.parse.parse_qs(urllib.parse.urlparse("https://google.com" + href).query).get("q", [""])[0]
            if href.startswith("http") and base_name in href and "google.com" not in href and href not in results:
                results.append(href)
            if len(results) >= limit:
                return results

        return results

    def _collect_duckduckgo(self, driver, website_base: str, base_name: str, limit: int) -> list:
        query = urllib.parse.quote_plus(website_base)
        driver.get(f"https://duckduckgo.com/?q={query}&ia=web")
        try:
            WebDriverWait(driver, self._RESULT_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[data-testid='result-title-a']"))
            )
        except Exception:
            time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        results = []

        # Primary: data-testid result anchors (in DOM order = search ranking order)
        for a in soup.find_all("a", attrs={"data-testid": "result-title-a"}):
            href = a.get("href", "")
            url = self._resolve_ddg_url(href)
            if url and base_name in url and url not in results:
                results.append(url)
            if len(results) >= limit:
                return results

        # Fallback: all hrefs
        for a in soup.find_all("a", href=True):
            href = a["href"]
            url = self._resolve_ddg_url(href)
            if url and base_name in url and url not in results:
                results.append(url)
            if len(results) >= limit:
                return results

        return results

    def _collect_bing(self, driver, website_base: str, base_name: str, limit: int) -> list:
        query = urllib.parse.quote_plus(website_base)
        driver.get(f"https://www.bing.com/search?q={query}")
        try:
            WebDriverWait(driver, self._RESULT_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.b_algo h2 a"))
            )
        except Exception:
            time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        results = []

        for a in soup.select("li.b_algo h2 a"):
            href = a.get("href", "")
            if href.startswith("http") and base_name in href and href not in results:
                results.append(href)
            if len(results) >= limit:
                return results

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "bing.com" in href or "microsoft.com" in href:
                continue
            if href.startswith("http") and base_name in href and href not in results:
                results.append(href)
            if len(results) >= limit:
                return results

        return results

    def _collect_brave(self, driver, website_base: str, base_name: str, limit: int) -> list:
        query = urllib.parse.quote_plus(website_base)
        driver.get(f"https://search.brave.com/search?q={query}&source=web")
        try:
            WebDriverWait(driver, self._RESULT_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.snippet a.heading-serpresult"))
            )
        except Exception:
            time.sleep(3)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        results = []

        for a in soup.select("div.snippet a.heading-serpresult"):
            href = a.get("href", "")
            if href.startswith("http") and base_name in href and href not in results:
                results.append(href)
            if len(results) >= limit:
                return results

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "brave.com" in href:
                continue
            if href.startswith("http") and base_name in href and href not in results:
                results.append(href)
            if len(results) >= limit:
                return results

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_ddg_url(href: str) -> Optional[str]:
        """Unwrap DDG redirect URLs and return the real destination URL."""
        if not href:
            return None
        if "duckduckgo.com/l/" in href:
            try:
                full = "https:" + href if href.startswith("//") else href
                uddg = urllib.parse.parse_qs(urllib.parse.urlparse(full).query).get("uddg", [])
                if uddg:
                    return uddg[0]
            except Exception:
                return None
        if href.startswith("http"):
            return href
        return None

    @staticmethod
    def _domain_from_url(url: str) -> Optional[str]:
        try:
            if not url.startswith("http"):
                return None
            return urllib.parse.urlparse(url).netloc or None
        except Exception:
            return None
