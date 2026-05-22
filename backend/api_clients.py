import requests
from typing import Optional, Dict, List
import time
import json
import re
import urllib.parse
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Optional Selenium imports — used by IMDBScraper and DomainFinder.
# If the package is not installed the classes degrade gracefully.
# ---------------------------------------------------------------------------
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------
def _make_headless_driver():
    """Return a headless uc.Chrome instance (for IMDB)."""
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"
    )
    return uc.Chrome(version_main=146, options=options)


def _make_visible_driver():
    """Return a visible (non-headless) uc.Chrome instance (for Google)."""
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"
    )
    return uc.Chrome(version_main=146, options=options)


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
        if not _SELENIUM_AVAILABLE:
            print("[IMDB] undetected_chromedriver not installed — cannot search")
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
        if not _SELENIUM_AVAILABLE:
            print("[IMDB] undetected_chromedriver not installed — cannot fetch by ID")
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
    Finds the current domain for a website whose TLD keeps changing
    by searching DuckDuckGo, Bing, and Brave and returning the first
    result URL that contains the site base name.

    No browser or Selenium needed — pure HTTP requests only.
    """

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    def find_domain(self, website_base: str) -> Optional[str]:
        """
        Search for website_base and return the domain from the first result.
        Tries DuckDuckGo, then Bing, then Brave.
        Returns None if all engines fail.
        """
        base_name = website_base.replace("www.", "").split(".")[0]  # e.g. "1tamilmv"
        print(f"[DOMAIN] Searching for: {website_base}")

        engines = [
            ("DuckDuckGo", self._search_duckduckgo),
            ("Bing",       self._search_bing),
            ("Brave",      self._search_brave),
        ]

        for engine_name, search_fn in engines:
            try:
                domain = search_fn(website_base, base_name)
                if domain:
                    print(f"[DOMAIN] {engine_name} found: {domain}")
                    return domain
                print(f"[DOMAIN] {engine_name}: no result, trying next...")
            except Exception as e:
                print(f"[DOMAIN] {engine_name} error: {e}")

        print(f"[DOMAIN] All search engines failed for: {website_base}")
        return None

    # ------------------------------------------------------------------
    def _search_duckduckgo(self, website_base: str, base_name: str) -> Optional[str]:
        """DuckDuckGo HTML — scraper-friendly, no JS needed.
        DDG wraps result URLs as //duckduckgo.com/l/?uddg=<encoded-real-url>
        so we unwrap the uddg parameter to get the actual domain."""
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": website_base, "b": "", "kl": "us-en"},
            headers=self._HEADERS,
            timeout=15,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]

            # DDG wraps every organic result URL like:
            #   //duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.1tamilmv.immo%2F&rut=...
            # Unwrap the real URL from the uddg query parameter.
            if "duckduckgo.com/l/" in href:
                try:
                    full = "https:" + href if href.startswith("//") else href
                    uddg = urllib.parse.parse_qs(urllib.parse.urlparse(full).query).get("uddg", [])
                    if uddg:
                        href = uddg[0]
                except Exception:
                    continue

            domain = self._domain_from_url(href)
            if domain and base_name in domain:
                return domain

        # Fallback: scan raw HTML for uddg= encoded values
        for encoded in re.findall(r'uddg=([^&\s]+)', resp.text):
            try:
                real_url = urllib.parse.unquote(encoded)
                domain = self._domain_from_url(real_url)
                if domain and base_name in domain:
                    return domain
            except Exception:
                continue

        return None

    def _search_bing(self, website_base: str, base_name: str) -> Optional[str]:
        """Bing web search — result links are direct hrefs."""
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": website_base},
            headers=self._HEADERS,
            timeout=15,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "bing.com" in href or "microsoft.com" in href:
                continue
            domain = self._domain_from_url(href)
            if domain and base_name in domain:
                return domain
        return None

    def _search_brave(self, website_base: str, base_name: str) -> Optional[str]:
        """Brave Search — result links are direct hrefs."""
        resp = requests.get(
            "https://search.brave.com/search",
            params={"q": website_base},
            headers=self._HEADERS,
            timeout=15,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "brave.com" in href:
                continue
            domain = self._domain_from_url(href)
            if domain and base_name in domain:
                return domain
        return None

    @staticmethod
    def _domain_from_url(url: str) -> Optional[str]:
        try:
            if not url.startswith("http"):
                return None
            return urllib.parse.urlparse(url).netloc or None
        except Exception:
            return None
