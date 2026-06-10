import traceback
import sys
from database import Database
from scraper import WebScraper
from api_clients import OMDBClient, TMDBClient, RadarrClient, QBittorrentClient, IMDBScraper
from typing import Optional, Dict, List, Tuple
import re


class MovieProcessor:
    """Core logic for processing and downloading movies"""

    # Indian/South-Asian language codes used to bias OMDB/TMDB searches
    # so we don't accidentally grab a Hollywood remake with the same name.
    PREFERRED_LANGUAGES = {'ta', 'te', 'ml', 'kn', 'hi', 'bn', 'mr', 'pa'}
    PREFERRED_LANG_NAMES = ['tamil', 'telugu', 'malayalam', 'kannada',
                             'hindi', 'bengali', 'marathi', 'punjabi']

    def __init__(self, db: Database):
        self.db = db
        self.scraper = WebScraper()
        self._init_clients()

    # ------------------------------------------------------------------
    # Client initialisation
    # ------------------------------------------------------------------
    def _init_clients(self):
        settings = self.db.get_all_settings()

        omdb_key = settings.get('omdb_api_key', '')
        self.omdb = OMDBClient(omdb_key) if omdb_key else None

        tmdb_key = settings.get('tmdb_api_key', '')
        self.tmdb = TMDBClient(tmdb_key) if tmdb_key else None

        radarr_url = settings.get('radarr_url', '')
        radarr_key = settings.get('radarr_api_key', '')
        self.radarr = RadarrClient(radarr_url, radarr_key) if radarr_url and radarr_key else None

        qb_url  = settings.get('qbittorrent_url', '')
        qb_user = settings.get('qbittorrent_username', '')
        qb_pass = settings.get('qbittorrent_password', '')
        self.qbittorrent = QBittorrentClient(qb_url, qb_user, qb_pass) if qb_url else None

        self.imdb_scraper = IMDBScraper()

    def refresh_clients(self):
        self._init_clients()

    # ------------------------------------------------------------------
    # Radarr "already has file" check
    # ------------------------------------------------------------------
    def _radarr_has_movie(self, title: str, year: Optional[int] = None,
                          imdb_id: Optional[str] = None) -> Tuple[bool, bool]:
        """
        Check Radarr for a movie by title/year or IMDB ID.
        Returns (in_radarr: bool, has_file: bool).
        'has_file' means Radarr already has the file — no need to re-download.
        """
        if not self.radarr:
            return (False, False)
        try:
            m = self.radarr.find_movie(title, year, imdb_id=imdb_id)
            if m is None:
                return (False, False)
            return (True, bool(m.get('hasFile', False)))
        except Exception as e:
            self.db.add_log('WARNING', f'Radarr check failed for "{title}": {e}', exc_info=e)
            return (False, False)

    def get_radarr_file_status(self, title: str, year: Optional[int] = None,
                               imdb_id: Optional[str] = None) -> Dict:
        """
        Return full Radarr file status for the details modal.
        Keys: in_radarr, has_file, file_path, quality, file_size_bytes, date_added, monitored
        """
        if not self.radarr:
            return {'in_radarr': False, 'has_file': False, 'error': 'Radarr not configured'}
        try:
            m = self.radarr.find_movie(title, year, imdb_id=imdb_id)
            if m is None:
                return {'in_radarr': False, 'has_file': False}
            file_info = self.radarr.get_movie_file_info(m)
            return {
                'in_radarr':        True,
                'monitored':        m.get('monitored', False),
                'radarr_id':        m.get('id'),
                'radarr_title':     m.get('title'),
                **file_info,
            }
        except Exception as e:
            self.db.add_log('WARNING',
                            f'Radarr file status check failed for "{title}": {e}', exc_info=e)
            return {'in_radarr': False, 'has_file': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Forum search
    # ------------------------------------------------------------------
    def search_movie_on_forum(self, movie_name: str) -> List[Dict]:
        try:
            search_url_template = self.db.get_setting('search_url')
            search_url = search_url_template.replace('{query}', movie_name)
            self.db.add_log('INFO', f'Searching forum for: {movie_name}', {'url': search_url})

            results = self.scraper.extract_specific_links(search_url)

            parsed_results = []
            for result in results:
                parsed = self.scraper.parse_movie_title_year(result['text'])
                parsed_results.append({**result, 'parsed_title': parsed['title'],
                                        'parsed_year': parsed['year']})

            self.db.add_log('INFO', f'Found {len(parsed_results)} results for: {movie_name}')
            return parsed_results

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Error searching forum for "{movie_name}": {e}',
                            {'movie': movie_name}, exc_info=e)
            return []

    def _find_best_forum_result(self, movie_name: str,
                                 search_results: List[Dict]) -> Optional[Dict]:
        """
        From forum search results, return the best match for movie_name.
        Priority:
          1. Exact case-insensitive title match
          2. Result whose parsed_title *contains* the search term
          3. First result (fallback — log it so user can see)
        """
        if not search_results:
            return None

        name_lower = movie_name.lower().strip()

        # Exact match
        for r in search_results:
            if r.get('parsed_title', '').lower().strip() == name_lower:
                return r

        # Contains match (e.g. "Vadam" matches "Vadam (Extended Cut)")
        for r in search_results:
            if name_lower in r.get('parsed_title', '').lower():
                return r

        # Fallback to first result
        return search_results[0]

    def get_movie_torrents(self, forum_url: str) -> List[Dict]:
        try:
            self.db.add_log('INFO', f'Extracting torrents from: {forum_url}')
            torrents = self.scraper.extract_torrents_by_fileext(forum_url)
            self.db.add_log('INFO', f'Found {len(torrents)} torrents at {forum_url}')
            return torrents
        except Exception as e:
            self.db.add_log('ERROR',
                            f'Error extracting torrents from "{forum_url}": {e}',
                            {'url': forum_url}, exc_info=e)
            return []

    # ------------------------------------------------------------------
    # Rating lookup  (OMDB → TMDB → IMDB scrape)
    # Biased toward Indian-language films.
    # ------------------------------------------------------------------
    def get_movie_rating(self, title: str, year: Optional[int] = None
                         ) -> Tuple[Optional[str], Optional[float], Optional[str]]:
        """
        Try OMDB → TMDB → IMDB scraping.
        Returns (imdb_id, rating, poster_url).
        Prefers results whose language is an Indian language to avoid
        accidentally matching a Hollywood film with the same name.
        """
        imdb_id = None
        rating  = None
        poster  = None
        omdb_fallback = None   # keep OMDB result in case TMDB finds nothing better

        def _is_indian_lang_omdb(data) -> bool:
            lang = (data.get('language') or '').lower() if data else ''
            return any(l in lang for l in self.PREFERRED_LANG_NAMES)

        # --- OMDB ---
        if self.omdb:
            try:
                self.db.add_log('DEBUG', f'Querying OMDB for: {title} ({year})')
                omdb_data = self.omdb.get_movie_info(title, year)

                # The forum year may be wrong: if the year-search found
                # nothing, or found a non-Indian-language film, retry
                # WITHOUT the year and prefer an Indian-language match.
                if year and (omdb_data is None or not _is_indian_lang_omdb(omdb_data)):
                    self.db.add_log('DEBUG',
                                    f'OMDB year-search for "{title}" ({year}) found no '
                                    f'Indian-language match — retrying without year')
                    retry_data = self.omdb.get_movie_info(title)
                    if retry_data and _is_indian_lang_omdb(retry_data):
                        self.db.add_log('INFO',
                                        f'OMDB: forum year {year} appears wrong for "{title}" — '
                                        f'matched Indian-language film without year',
                                        {'imdb_id': retry_data.get('imdb_id')})
                        omdb_data = retry_data
                    elif omdb_data is None:
                        omdb_data = retry_data

                if omdb_data:
                    imdb_id    = omdb_data.get('imdb_id')
                    rating_str = omdb_data.get('imdb_rating')
                    poster     = omdb_data.get('poster')
                    lang       = (omdb_data.get('language') or '').lower()
                    lang_ok    = any(l in lang for l in self.PREFERRED_LANG_NAMES)

                    if rating_str and rating_str != 'N/A':
                        rating = float(rating_str)
                        if lang_ok:
                            # Perfect — right language
                            self.db.add_log('INFO',
                                            f'OMDB rating for "{title}": {rating} [{lang}]',
                                            {'imdb_id': imdb_id})
                            return (imdb_id, rating, poster)
                        else:
                            # Possibly wrong film — stash as fallback, try TMDB
                            omdb_fallback = (imdb_id, rating, poster)
                            self.db.add_log('DEBUG',
                                            f'OMDB match for "{title}" is not Indian-language '
                                            f'({lang}) — checking TMDB',
                                            {'imdb_id': imdb_id})
                            rating = None   # reset so TMDB runs
                    else:
                        self.db.add_log('DEBUG',
                                        f'OMDB returned no usable rating for "{title}"',
                                        {'raw_rating': rating_str, 'imdb_id': imdb_id})
                else:
                    self.db.add_log('DEBUG', f'OMDB found no match for "{title}" ({year})')
            except Exception as e:
                self.db.add_log('WARNING',
                                f'OMDB lookup failed for "{title}": {e}', exc_info=e)

        # --- TMDB (try with year, then without) ---
        if self.tmdb and not rating:
            try:
                self.db.add_log('DEBUG', f'Querying TMDB for: {title} ({year})')
                tmdb_data = self.tmdb.search_movie(title, year)

                def _is_indian_lang_tmdb(data) -> bool:
                    return bool(data) and \
                        (data.get('original_language') or '').lower() in self.PREFERRED_LANGUAGES

                # Year might be wrong in the forum post: if the year-search
                # found nothing, or found a non-Indian-language film, retry
                # without the year and prefer an Indian-language match.
                if year and (not tmdb_data or not _is_indian_lang_tmdb(tmdb_data)):
                    self.db.add_log('DEBUG',
                                    f'TMDB year-search for "{title}" ({year}) found no '
                                    f'Indian-language match — retrying without year')
                    retry_data = self.tmdb.search_movie(title)
                    if retry_data and _is_indian_lang_tmdb(retry_data):
                        self.db.add_log('INFO',
                                        f'TMDB: forum year {year} appears wrong for "{title}" — '
                                        f'matched Indian-language film without year',
                                        {'tmdb_id': retry_data.get('tmdb_id')})
                        tmdb_data = retry_data
                    elif not tmdb_data:
                        tmdb_data = retry_data

                if tmdb_data:
                    orig_lang = (tmdb_data.get('original_language') or '').lower()
                    imdb_id   = tmdb_data.get('imdb_id') or imdb_id
                    poster    = tmdb_data.get('poster') or poster
                    vote_avg  = tmdb_data.get('vote_average')

                    if vote_avg:
                        rating = float(vote_avg)
                        self.db.add_log('INFO',
                                        f'TMDB rating for "{title}": {rating} [{orig_lang}]',
                                        {'tmdb_id': tmdb_data.get('tmdb_id'),
                                         'imdb_id': imdb_id,
                                         'original_language': orig_lang})
                        return (imdb_id, rating, poster)
                    else:
                        self.db.add_log('DEBUG',
                                        f'TMDB returned no vote_average for "{title}"')
                else:
                    self.db.add_log('DEBUG', f'TMDB found no match for "{title}" ({year})')
            except Exception as e:
                self.db.add_log('WARNING',
                                f'TMDB lookup failed for "{title}": {e}', exc_info=e)

        # Use OMDB non-Indian fallback if TMDB also failed
        if omdb_fallback and not rating:
            imdb_id, rating, poster = omdb_fallback
            self.db.add_log('INFO',
                            f'Using OMDB non-Indian fallback for "{title}": {rating}',
                            {'imdb_id': imdb_id})
            return (imdb_id, rating, poster)

        # --- IMDB scrape by known ID ---
        if not rating and imdb_id:
            try:
                self.db.add_log('DEBUG', f'Scraping IMDB page for: {imdb_id}')
                scraped = self.imdb_scraper.get_rating_by_id(imdb_id)
                if scraped:
                    rating = scraped.get('imdb_rating')
                    poster = scraped.get('poster_url') or poster
                    self.db.add_log('INFO', f'IMDB scraped rating for "{title}": {rating}',
                                    {'imdb_id': imdb_id})
                    return (imdb_id, rating, poster)
            except Exception as e:
                self.db.add_log('WARNING',
                                f'IMDB scrape failed for id={imdb_id}: {e}', exc_info=e)

        # --- IMDB search (no ID yet) ---
        if not rating:
            try:
                self.db.add_log('DEBUG', f'Searching IMDB directly for: {title}')
                imdb_data = self.imdb_scraper.search_movie(title)
                if imdb_data:
                    imdb_id = imdb_data.get('imdb_id') or imdb_id
                    rating  = imdb_data.get('imdb_rating')
                    poster  = imdb_data.get('poster_url') or poster
                    self.db.add_log('INFO', f'IMDB search rating for "{title}": {rating}',
                                    {'imdb_id': imdb_id})
                    return (imdb_id, rating, poster)
            except Exception as e:
                self.db.add_log('WARNING',
                                f'IMDB search failed for "{title}": {e}', exc_info=e)

        self.db.add_log('WARNING',
                        f'No rating found for "{title}" ({year}) from any source.',
                        {'title': title, 'year': year, 'imdb_id': imdb_id})
        return (imdb_id, rating, poster)

    # ------------------------------------------------------------------
    # Library-wide IMDB refresh  (OMDB → TMDB → IMDB per movie)
    # ------------------------------------------------------------------
    def refresh_library(self) -> Dict:
        """
        Iterate every movie in the DB and refresh rating + poster.
        Returns summary stats.
        """
        movies = self.db.get_all_movies()
        total = len(movies)
        updated = 0
        failed  = 0

        self.db.add_log('INFO', f'Library refresh starting', {'total_movies': total})

        for movie in movies:
            try:
                result = self.refresh_imdb_data(movie['id'])
                if result.get('success'):
                    updated += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                self.db.add_log('ERROR',
                                f'Library refresh failed for "{movie["title"]}": {e}',
                                exc_info=e)

        self.db.add_log('INFO', f'Library refresh complete',
                        {'total': total, 'updated': updated, 'failed': failed})
        return {'total': total, 'updated': updated, 'failed': failed}

    def refresh_imdb_data(self, movie_id: int) -> Dict:
        """
        Re-fetch rating+metadata for one movie using OMDB → TMDB → IMDB priority.
        Updates poster_url and imdb_rating in the DB.
        """
        movie = self.db.get_movie_by_id(movie_id)
        if not movie:
            return {'success': False, 'message': 'Movie not found'}

        title = movie['title']
        year  = movie.get('year')

        try:
            new_imdb_id, rating, poster = self.get_movie_rating(title, year)
            update = {}
            if new_imdb_id:
                update['imdb_id'] = new_imdb_id
            if rating is not None:
                update['imdb_rating'] = rating
            if poster:
                update['poster_url'] = poster
            if update:
                self.db.update_movie(movie_id, update)
            self.db.add_log('INFO', f'Refreshed data for "{title}"',
                            {'rating': rating, 'imdb_id': new_imdb_id})
            return {'success': True, 'rating': rating, 'poster_url': poster,
                    'imdb_id': new_imdb_id}

        except Exception as e:
            self.db.add_log('ERROR', f'refresh_imdb_data failed for "{title}": {e}', exc_info=e)
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # Manual movie info correction
    # ------------------------------------------------------------------
    def manual_update_movie(self, movie_id: int, title: str,
                             year: Optional[int] = None,
                             imdb_id: Optional[str] = None) -> Dict:
        """
        Correct a movie's title/year/IMDB ID and re-fetch its rating and poster.
        Used when the automatic lookup grabbed the wrong film.
        """
        movie = self.db.get_movie_by_id(movie_id)
        if not movie:
            return {'success': False, 'message': 'Movie not found'}

        update: Dict = {'title': title}
        if year:
            update['year'] = year
        if imdb_id:
            update['imdb_id'] = imdb_id
        self.db.update_movie(movie_id, update)

        # Re-fetch with corrected info
        new_imdb_id, rating, poster = self.get_movie_rating(title, year)
        rating_update: Dict = {}
        if new_imdb_id:
            rating_update['imdb_id'] = imdb_id or new_imdb_id
        if rating is not None:
            rating_update['imdb_rating'] = rating
        if poster:
            rating_update['poster_url'] = poster
        if rating_update:
            self.db.update_movie(movie_id, rating_update)

        self.db.add_log('INFO',
                        f'Manual update for movie {movie_id}: "{title}" ({year})',
                        {'imdb_id': imdb_id or new_imdb_id, 'rating': rating})
        return {'success': True, 'rating': rating, 'poster_url': poster,
                'imdb_id': imdb_id or new_imdb_id}

    # ------------------------------------------------------------------
    # Re-download torrent (search forum again)
    # ------------------------------------------------------------------
    def redownload_torrent(self, movie_id: int) -> Dict:
        """
        Search the forum for a movie's torrent and send it to qBittorrent.
        Used from the "Re-download Torrent" button on the Movies page.
        """
        movie = self.db.get_movie_by_id(movie_id)
        if not movie:
            return {'success': False, 'message': 'Movie not found'}

        title     = movie['title']
        forum_url = movie.get('forum_url')

        # Find forum page if missing
        if not forum_url:
            results = self.search_movie_on_forum(title)
            best    = self._find_best_forum_result(title, results)
            if not best:
                return {'success': False,
                        'message': f'Could not find "{title}" on the forum'}
            forum_url = best['href']
            self.db.update_movie(movie_id, {'forum_url': forum_url})

        torrents = self._safe_get_torrents(forum_url, title)
        if not torrents:
            return {'success': False, 'message': 'No torrents found at forum page'}

        # Store any new quality variants not already in DB
        existing = {q['torrent_url'] for q in self.db.get_movie_qualities(movie_id)}
        for t in torrents:
            if t.get('torrent_url') not in existing:
                self.db.add_movie_quality(movie_id, t)

        selected, _ = self.select_quality(torrents)
        torrent = selected or torrents[0]

        success = self.download_and_add_torrent(torrent, title)
        if success:
            self.db.update_movie(movie_id, {
                'is_downloaded':        True,
                'downloaded_quality':   torrent.get('quality'),
                'file_size':            torrent.get('file_size'),
                'torrent_url':          torrent.get('torrent_url'),
                'torrent_name':         torrent.get('name'),
                'added_to_qbittorrent': True,
                'rejection_reason':     None,
                'download_failed':      False,
            })
        else:
            self.db.update_movie(movie_id, {'download_failed': True})
        return {
            'success': success,
            'message': 'Torrent sent to qBittorrent' if success else 'qBittorrent rejected torrent'
        }

    # ------------------------------------------------------------------
    # Rip-type quality ranking (best → worst)
    # ------------------------------------------------------------------
    RIP_RANK = {
        'BluRay': 1, 'WEB-DL': 2, 'WEBRip': 3, 'HDRip': 4, 'HDTV': 5,
        'DVDRip': 6, 'HDTC': 7, 'PreDVD': 8, 'CAM/TS': 9,
    }

    def rank_torrents(self, torrents: List[Dict]) -> List[Dict]:
        """Sort torrents best-first by rip type (unknown rip types last)."""
        return sorted(torrents,
                      key=lambda t: self.RIP_RANK.get(t.get('rip_type'), 99))

    # ------------------------------------------------------------------
    # Quality selection
    # ------------------------------------------------------------------
    def select_quality(self, torrents: List[Dict], preferred_quality: str = None,
                       preferred_codec: str = None) -> Tuple[Optional[Dict], List[Dict]]:
        if not torrents:
            return (None, [])

        if not preferred_quality:
            preferred_quality = self.db.get_setting('preferred_quality', '1080p')
        if not preferred_codec:
            preferred_codec = self.db.get_setting('preferred_codec', 'HEVC')

        selected = self.scraper.match_quality_preference(torrents, preferred_quality, preferred_codec)

        if selected:
            alternatives = [t for t in torrents if t != selected]
            return (selected, alternatives)
        else:
            return (None, torrents)

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------
    def download_and_add_torrent(self, torrent: Dict, movie_title: str) -> bool:
        """Download .torrent file and push it to qBittorrent."""
        try:
            torrent_url  = torrent['torrent_url']
            torrent_name = torrent.get('name') or torrent.get('torrent_name', '')

            self.db.add_log('INFO', f'Downloading torrent file for: {movie_title}',
                            {'torrent_url': torrent_url, 'torrent_name': torrent_name})
            filepath = self.scraper.download_torrent(torrent_url, torrent_name)

            if not filepath:
                self.db.add_log('ERROR',
                                f'Torrent file download returned no path for "{movie_title}"',
                                {'torrent_url': torrent_url})
                return False

            if self.qbittorrent:
                self.db.add_log('INFO', f'Adding torrent to qBittorrent: {movie_title}',
                                {'filepath': filepath})
                success = self.qbittorrent.add_torrent_file(filepath, category='radarr')
                if success:
                    self.db.add_log('INFO', f'qBittorrent accepted torrent for: {movie_title}')
                    return True
                else:
                    self.db.add_log('ERROR',
                                    f'qBittorrent rejected torrent for "{movie_title}"',
                                    {'filepath': filepath,
                                     'qbittorrent_url': self.qbittorrent.url})
                    return False
            else:
                self.db.add_log('WARNING',
                                'qBittorrent not configured — torrent downloaded but not queued',
                                {'filepath': filepath})
                return False

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Unexpected error downloading/queuing torrent for "{movie_title}": {e}',
                            {'torrent_url': torrent.get('torrent_url')}, exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Radarr helper
    # ------------------------------------------------------------------
    def add_to_radarr(self, title: str, year: Optional[int],
                      tmdb_id=None, imdb_id=None) -> bool:
        """
        Add movie to Radarr. Returns True on success. NEVER raises.

        Root folder is read from the 'radarr_root_folder' setting; if blank
        the RadarrClient falls back to Radarr's own first configured path.

        Lookup priority when no tmdb_id is supplied:
          1. TMDB API search  (standard path)
          2. Radarr IMDB-ID lookup  (when we have an imdb_id but no tmdb_id)
          3. Radarr title/year search  (last resort — works for movies not yet on TMDB)
        """
        try:
            if not self.radarr:
                self.db.add_log('DEBUG', 'Radarr not configured — skipping',
                                {'title': title})
                return False

            # Read the user-configured root folder (may be empty string → None)
            root_folder = self.db.get_setting('radarr_root_folder', '').strip() or None
            if root_folder:
                self.db.add_log('DEBUG',
                                f'Using configured Radarr root folder: {root_folder}',
                                {'title': title})

            # ── Step 1: resolve tmdb_id via TMDB API ──────────────────
            if not tmdb_id and self.tmdb:
                try:
                    tmdb_data = self.tmdb.search_movie(title, year)
                    if tmdb_data:
                        tmdb_id = int(tmdb_data['tmdb_id'])
                        self.db.add_log('DEBUG',
                                        f'TMDB resolved tmdb_id={tmdb_id} for "{title}"')
                    else:
                        self.db.add_log('INFO',
                                        f'TMDB returned no results for "{title}" ({year}) '
                                        f'— will try IMDB/Radarr fallbacks')
                except Exception as e:
                    self.db.add_log('WARNING',
                                    f'TMDB lookup for Radarr failed for "{title}": {e}',
                                    exc_info=e)

            # ── Guard: already in Radarr? ──────────────────────────────
            if tmdb_id and self.radarr.movie_exists(tmdb_id):
                self.db.add_log('INFO',
                                f'"{title}" already in Radarr (tmdb_id={tmdb_id})')
                return True

            # ── Step 2: add via TMDB ID (normal path) ─────────────────
            if tmdb_id:
                self.db.add_log('INFO', f'Adding "{title}" to Radarr via TMDB ID',
                                {'tmdb_id': tmdb_id,
                                 'root_folder': root_folder or '(Radarr default)'})
                result = self.radarr.add_movie(tmdb_id, root_folder=root_folder)
                if result:
                    self.db.add_log('INFO', f'Radarr accepted "{title}" (TMDB)',
                                    {'tmdb_id': tmdb_id,
                                     'root_folder': root_folder or '(Radarr default)'})
                    return True
                self.db.add_log('WARNING',
                                f'Radarr rejected TMDB add for "{title}" — trying fallbacks',
                                {'tmdb_id': tmdb_id})

            # ── Step 3: add via IMDB ID (when TMDB has no record) ─────
            if imdb_id:
                self.db.add_log('INFO', f'Adding "{title}" to Radarr via IMDB ID',
                                {'imdb_id': imdb_id,
                                 'root_folder': root_folder or '(Radarr default)'})
                result = self.radarr.add_movie_by_imdb_id(imdb_id,
                                                           root_folder=root_folder)
                if result:
                    self.db.add_log('INFO', f'Radarr accepted "{title}" (IMDB ID)',
                                    {'imdb_id': imdb_id})
                    return True
                self.db.add_log('WARNING',
                                f'Radarr IMDB-ID add failed for "{title}" — trying title search',
                                {'imdb_id': imdb_id})

            # ── Step 4: add via Radarr title search (last resort) ─────
            self.db.add_log('INFO',
                            f'Adding "{title}" to Radarr via title search',
                            {'title': title, 'year': year,
                             'root_folder': root_folder or '(Radarr default)'})
            result = self.radarr.add_movie_by_title(title, year,
                                                     root_folder=root_folder)
            if result:
                self.db.add_log('INFO', f'Radarr accepted "{title}" (title search)')
                return True

            self.db.add_log('ERROR',
                            f'All Radarr add attempts failed for "{title}" ({year})',
                            {'title': title, 'year': year,
                             'tmdb_id': tmdb_id, 'imdb_id': imdb_id})
            return False

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Unexpected error adding "{title}" to Radarr: {e}',
                            {'title': title, 'year': year}, exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Main process_movie pipeline
    # ------------------------------------------------------------------
    def process_movie(self, title: str, year: Optional[int] = None,
                      forum_url: Optional[str] = None,
                      source: str = 'manual') -> Dict:
        """
        Full pipeline:
          0. Check Radarr — if file already exists there, record & skip download
          1. Resolve forum URL (multi-result fallback search)
          2. Get rating (OMDB→TMDB→IMDB, Indian-language biased)
          3. Fetch torrents early so ALL rejection paths can store them
          4. Rating threshold check (store torrents even when rejected)
          5. Quality selection (store torrents even when preferred quality missing)
          6. Download + add to services
        """
        result = {
            'success': False,
            'movie_id': None,
            'title': title,
            'year': year,
            'rating': None,
            'downloaded': False,
            'added_to_radarr': False,
            'needs_user_action': False,
            'action_type': None,
            'options': None,
            'message': '',
        }

        try:
            # ── Step 0: Radarr already has the file? ───────────────────
            # We do a quick title/year check first (imdb_id not yet resolved).
            in_radarr, radarr_has_file = self._radarr_has_movie(title, year)
            if radarr_has_file:
                rejection_msg = (
                    f'Already in Radarr with file — skipping torrent download. '
                    f'Radarr already has "{title}" ({year}) downloaded.'
                )
                self.db.add_log('INFO',
                                f'Radarr already has file for "{title}" ({year}) — '
                                f'skipping torrent download.',
                                {'source': source})
                imdb_id, rating, poster = self.get_movie_rating(title, year)
                movie_data = {
                    'title':            title,
                    'year':             year,
                    'imdb_id':          imdb_id,
                    'imdb_rating':      rating,
                    'poster_url':       poster,
                    'forum_url':        forum_url,
                    'source':           source,
                    'is_downloaded':    True,
                    'added_to_radarr':  True,
                    'rejection_reason': rejection_msg,
                }
                if not self.db.movie_exists(title, year):
                    movie_id = self.db.add_movie(movie_data)
                    result['movie_id'] = movie_id
                else:
                    existing = self.db.get_movie_by_title_year(title, year)
                    result['movie_id'] = existing['id']
                    self.db.update_movie(existing['id'],
                                         {'is_downloaded': True, 'added_to_radarr': True,
                                          'rejection_reason': rejection_msg})
                result['success']         = True
                result['downloaded']      = True
                result['added_to_radarr'] = True
                result['message'] = (
                    f'"{title}" already downloaded in Radarr — recorded without re-downloading.'
                )
                return result

            # ── Step 1: resolve forum URL ──────────────────────────────
            if not forum_url:
                search_results = self.search_movie_on_forum(title)
                if not search_results:
                    result['message'] = f'No search results found on forum for "{title}"'
                    self.db.add_log('WARNING', result['message'])
                    return result

                best = self._find_best_forum_result(title, search_results)
                if not best:
                    result['needs_user_action'] = True
                    result['action_type']       = 'select_result'
                    result['options']           = search_results
                    result['message'] = (
                        f'Could not find a good match for "{title}" — '
                        f'please select the correct entry'
                    )
                    return result

                name_lower = title.lower().strip()
                if best['parsed_title'].lower().strip() != name_lower:
                    self.db.add_log('INFO',
                                    f'Forum: using "{best["parsed_title"]}" as match for "{title}"',
                                    {'href': best['href']})

                forum_url = best['href']
                year = year or best.get('parsed_year')

            # ── Step 2: rating ─────────────────────────────────────────
            try:
                imdb_id, rating, poster = self.get_movie_rating(title, year)
            except Exception as e:
                self.db.add_log('ERROR',
                                f'Rating lookup crashed for "{title}": {e}', exc_info=e)
                imdb_id = rating = poster = None

            result['rating'] = rating

            # ── Step 3: fetch torrents EARLY so all paths can store them
            torrents = self._safe_get_torrents(forum_url, title)

            # ── Step 3b: no torrents at all → do NOT save anything ──────
            # (Bug fix: posts without torrent files were still being added
            # to the database. If there are no torrents, nothing is saved.)
            if not torrents:
                result['message'] = (f'No torrent files found at forum page for '
                                     f'"{title}" — movie not saved.')
                self.db.add_log('WARNING', result['message'], {'forum_url': forum_url,
                                                               'source': source})
                return result

            # ── Step 4: rating threshold check ─────────────────────────
            threshold = float(self.db.get_setting('rating_threshold', '6.5'))

            if rating is None:
                self.db.add_log('WARNING',
                                f'No rating found for "{title}" — saving with torrents.',
                                {'forum_url': forum_url})
                movie_data = {
                    'title':            title,
                    'year':             year,
                    'imdb_id':          imdb_id,
                    'imdb_rating':      None,
                    'poster_url':       poster,
                    'forum_url':        forum_url,
                    'source':           source,
                    'added_to_radarr':  in_radarr,
                    'rejection_reason': 'No rating found — manual download available',
                }
                if torrents:
                    best_t, _ = self.select_quality(torrents)
                    t = best_t or torrents[0]
                    movie_data['torrent_url']  = t.get('torrent_url')
                    movie_data['torrent_name'] = t.get('name')

                movie_id = self.db.add_movie(movie_data)
                result['movie_id'] = movie_id
                for t in (torrents or []):
                    self.db.add_movie_quality(movie_id, t)

                result['needs_user_action'] = True
                result['action_type']       = 'no_rating'
                result['message'] = (
                    f'No IMDB/TMDB rating found for "{title}". '
                    f'Movie saved — click Download to get it manually.'
                )
                return result

            if rating < threshold:
                self.db.add_log('INFO',
                                f'"{title}" rating {rating} below threshold {threshold} '
                                f'— saving with torrents for manual override.')
                movie_data = {
                    'title':            title,
                    'year':             year,
                    'imdb_id':          imdb_id,
                    'imdb_rating':      rating,
                    'poster_url':       poster,
                    'forum_url':        forum_url,
                    'source':           source,
                    'added_to_radarr':  in_radarr,
                    'rejection_reason': f'Rating {rating} below threshold {threshold}',
                }
                if torrents:
                    best_t, _ = self.select_quality(torrents)
                    t = best_t or torrents[0]
                    movie_data['torrent_url']  = t.get('torrent_url')
                    movie_data['torrent_name'] = t.get('name')

                movie_id = self.db.add_movie(movie_data)
                result['movie_id'] = movie_id
                for t in (torrents or []):
                    self.db.add_movie_quality(movie_id, t)

                result['needs_user_action'] = True
                result['action_type']       = 'override_rating'
                result['message'] = (
                    f'Rating {rating} is below threshold {threshold}. '
                    f'Override and download anyway?'
                )
                return result

            # ── Step 5: quality selection ───────────────────────────────
            if source == 'full_scan':
                # Full Forum Scanner: download 1080p torrents ONLY.
                # Dedupe identical torrent URLs; if multiple distinct 1080p
                # torrents remain (different rip types), save the movie and
                # ask the user which one to send to qBittorrent.
                torrents_1080 = []
                seen_urls = set()
                for t in torrents:
                    if (t.get('quality') or '').lower() != '1080p':
                        continue
                    u = t.get('torrent_url')
                    if u in seen_urls:
                        continue
                    seen_urls.add(u)
                    torrents_1080.append(t)

                if not torrents_1080:
                    movie_data = {
                        'title':            title,
                        'year':             year,
                        'imdb_id':          imdb_id,
                        'imdb_rating':      rating,
                        'poster_url':       poster,
                        'forum_url':        forum_url,
                        'source':           source,
                        'added_to_radarr':  in_radarr,
                        'rejection_reason': 'No 1080p torrent available — select manually',
                        'torrent_url':      torrents[0].get('torrent_url'),
                        'torrent_name':     torrents[0].get('name'),
                    }
                    movie_id = self.db.add_movie(movie_data)
                    result['movie_id'] = movie_id
                    for t in torrents:
                        self.db.add_movie_quality(movie_id, t)
                    result['needs_user_action'] = True
                    result['action_type']       = 'select_quality'
                    result['message'] = f'No 1080p torrent available for "{title}".'
                    return result

                if len(torrents_1080) > 1:
                    ranked = self.rank_torrents(torrents_1080)
                    rip_types = [t.get('rip_type') or '?' for t in ranked]
                    movie_data = {
                        'title':            title,
                        'year':             year,
                        'imdb_id':          imdb_id,
                        'imdb_rating':      rating,
                        'poster_url':       poster,
                        'forum_url':        forum_url,
                        'source':           source,
                        'added_to_radarr':  in_radarr,
                        'rejection_reason': (f'Multiple 1080p torrents available '
                                             f'({", ".join(rip_types)}) — choose which '
                                             f'to send to qBittorrent'),
                        'torrent_url':      ranked[0].get('torrent_url'),
                        'torrent_name':     ranked[0].get('name'),
                    }
                    movie_id = self.db.add_movie(movie_data)
                    result['movie_id'] = movie_id
                    # Store ALL torrents so the details view can show the
                    # rip-type differences between the duplicate links.
                    for t in torrents:
                        self.db.add_movie_quality(movie_id, t)
                    result['needs_user_action'] = True
                    result['action_type']       = 'choose_torrent'
                    result['options']           = ranked
                    result['message'] = (f'{len(torrents_1080)} different 1080p torrents '
                                         f'found for "{title}" — selection required.')
                    self.db.add_log('INFO', result['message'],
                                    {'rip_types': rip_types, 'forum_url': forum_url})
                    return result

                selected_torrent = torrents_1080[0]
                alternatives = [t for t in torrents if t is not selected_torrent]
            else:
                selected_torrent, alternatives = self.select_quality(torrents)

            if not selected_torrent:
                movie_data = {
                    'title':            title,
                    'year':             year,
                    'imdb_id':          imdb_id,
                    'imdb_rating':      rating,
                    'poster_url':       poster,
                    'forum_url':        forum_url,
                    'source':           source,
                    'added_to_radarr':  in_radarr,
                    'rejection_reason': 'Preferred quality not available — select manually',
                }
                if alternatives:
                    t = alternatives[0]
                    movie_data['torrent_url']  = t.get('torrent_url')
                    movie_data['torrent_name'] = t.get('name')

                movie_id = self.db.add_movie(movie_data)
                result['movie_id'] = movie_id
                for t in alternatives:
                    self.db.add_movie_quality(movie_id, t)

                result['needs_user_action'] = True
                result['action_type']       = 'select_quality'
                result['options']           = alternatives
                result['message'] = (
                    f'Preferred quality not available for "{title}". '
                    f'Select from available options.'
                )
                return result

            # ── Step 6: download + add to services ──────────────────────
            download_success = self.download_and_add_torrent(selected_torrent, title)
            radarr_success   = in_radarr or self.add_to_radarr(title, year, imdb_id=imdb_id)

            movie_data = {
                'title':                title,
                'year':                 year,
                'imdb_id':              imdb_id,
                'imdb_rating':          rating,
                'poster_url':           poster,
                'forum_url':            forum_url,
                'source':               source,
                'is_downloaded':        download_success,
                'downloaded_quality':   selected_torrent.get('quality'),
                'file_size':            selected_torrent.get('file_size'),
                'torrent_url':          selected_torrent.get('torrent_url'),
                'torrent_name':         selected_torrent.get('name'),
                'added_to_qbittorrent': download_success,
                'added_to_radarr':      radarr_success,
                'download_failed':      not download_success,
            }
            if not download_success:
                movie_data['rejection_reason'] = 'Torrent download failed — retry manually'
            movie_id = self.db.add_movie(movie_data)
            result['movie_id'] = movie_id
            for t in alternatives:
                self.db.add_movie_quality(movie_id, t)

            result['success']         = True
            result['downloaded']      = download_success
            result['added_to_radarr'] = radarr_success
            result['message']         = 'Movie processed successfully'
            return result

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Unhandled exception processing "{title}" ({year}): {e}',
                            {'title': title, 'year': year, 'forum_url': forum_url,
                             'source': source},
                            exc_info=e)
            result['message'] = f'Unexpected error: {e}'
            return result

    # ------------------------------------------------------------------
    # Forum scan
    # ------------------------------------------------------------------
    def scan_forum_for_new_movies(self, max_pages: int = 3, max_links: int = 50) -> List[Dict]:
        results             = []
        duplicate_count     = 0
        duplicate_threshold = int(self.db.get_setting('duplicate_stop_count', '5'))
        forum_url_template  = self.db.get_setting('forum_url')
        auto_download       = self.db.get_setting('auto_download', 'true') == 'true'

        self.db.add_log('INFO', f'Starting forum scan',
                        {'max_pages': max_pages, 'max_links': max_links,
                         'auto_download': auto_download})

        try:
            links_processed = 0

            for page in range(1, max_pages + 1):
                forum_url = (forum_url_template if page == 1
                             else forum_url_template.rstrip('/') + f'/page/{page}/')
                self.db.add_log('DEBUG', f'Scanning forum page {page}', {'url': forum_url})

                try:
                    forum_links = self.scraper.extract_links_with_ipshover(forum_url)
                except Exception as e:
                    self.db.add_log('ERROR',
                                    f'Failed to fetch forum page {page}: {e}',
                                    {'url': forum_url}, exc_info=e)
                    continue

                for link in forum_links:
                    if links_processed >= max_links:
                        break

                    parsed = self.scraper.parse_movie_title_year(link['text'])
                    title  = parsed['title']
                    year   = parsed['year']

                    if self.db.movie_exists(title, year):
                        duplicate_count += 1
                        self.db.add_log('DEBUG', f'Duplicate: "{title}" ({year})',
                                        {'duplicate_count': duplicate_count,
                                         'threshold': duplicate_threshold})
                        if duplicate_count >= duplicate_threshold:
                            self.db.add_log('INFO',
                                            f'Reached {duplicate_count} consecutive duplicates '
                                            f'(threshold={duplicate_threshold}), stopping scan.')
                            return results
                        continue

                    duplicate_count = 0
                    links_processed += 1

                    if auto_download:
                        try:
                            result = self.process_movie(title, year, link['href'],
                                                        source='auto_scan')
                            results.append(result)
                        except Exception as e:
                            self.db.add_log('ERROR',
                                            f'process_movie crashed for "{title}" ({year}): {e}',
                                            {'href': link['href']}, exc_info=e)
                    else:
                        self.db.add_log('INFO',
                                        f'New movie found (auto-download off): "{title}" ({year})',
                                        {'href': link['href']})
                        try:
                            self.db.add_movie({
                                'title':     title,
                                'year':      year,
                                'forum_url': link['href'],
                                'source':    'auto_scan',
                            })
                        except Exception as e:
                            self.db.add_log('ERROR',
                                            f'Could not save movie "{title}" to DB: {e}',
                                            exc_info=e)

                if links_processed >= max_links:
                    break

            self.db.add_log('INFO', f'Forum scan complete',
                            {'pages_scanned': max_pages,
                             'links_processed': links_processed,
                             'movies_processed': len(results)})
            return results

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Forum scan aborted due to unexpected error: {e}', exc_info=e)
            return results

    # ------------------------------------------------------------------
    # Full Forum Scanner — scans EVERY page of the forum
    # ------------------------------------------------------------------
    def full_forum_scan(self, state: Dict) -> None:
        """
        Scan the entire forum from page 1 to the last page.
        Total page count is read from the ipsPagination element so the
        progress bar has a known denominator from the start.
        Progress is reported through the shared `state` dict, which the
        /api/movies/full-scan/status endpoint exposes to the frontend.
        Duplicates are skipped (never stop the scan). Movies are processed
        with source='full_scan' → 1080p-only download workflow.
        """
        forum_url_template = self.db.get_setting('forum_url')

        total_pages = self.scraper.get_total_pages(forum_url_template)
        state.update({
            'total_pages':      total_pages,
            'current_page':     0,
            'movies_found':     0,
            'movies_processed': 0,
            'movies_failed':    0,
            'needs_action':     0,
            'skipped_existing': 0,
        })
        self.db.add_log('INFO', f'Full forum scan started — {total_pages} pages detected',
                        {'forum_url': forum_url_template})

        try:
            for page in range(1, total_pages + 1):
                if state.get('cancel'):
                    self.db.add_log('INFO', f'Full forum scan cancelled at page {page}')
                    break

                state['current_page'] = page
                forum_url = (forum_url_template if page == 1
                             else forum_url_template.rstrip('/') + f'/page/{page}/')

                try:
                    forum_links = self.scraper.extract_links_with_ipshover(forum_url)
                except Exception as e:
                    self.db.add_log('ERROR',
                                    f'Full scan: failed to fetch page {page}: {e}',
                                    {'url': forum_url}, exc_info=e)
                    continue

                for link in forum_links:
                    if state.get('cancel'):
                        break

                    parsed = self.scraper.parse_movie_title_year(link['text'])
                    title  = parsed['title']
                    year   = parsed['year']

                    if not title:
                        continue

                    if self.db.movie_exists(title, year):
                        state['skipped_existing'] += 1
                        continue

                    state['movies_found'] += 1
                    try:
                        r = self.process_movie(title, year, link['href'],
                                               source='full_scan')
                        if r.get('success'):
                            state['movies_processed'] += 1
                        elif r.get('needs_user_action'):
                            state['needs_action'] += 1
                        else:
                            state['movies_failed'] += 1
                    except Exception as e:
                        state['movies_failed'] += 1
                        self.db.add_log('ERROR',
                                        f'Full scan: process_movie crashed for '
                                        f'"{title}" ({year}): {e}',
                                        {'href': link['href']}, exc_info=e)

            self.db.add_log('INFO', 'Full forum scan complete', {
                'pages_scanned':    state['current_page'],
                'total_pages':      total_pages,
                'movies_found':     state['movies_found'],
                'movies_processed': state['movies_processed'],
                'needs_action':     state['needs_action'],
                'movies_failed':    state['movies_failed'],
                'skipped_existing': state['skipped_existing'],
            })
        except Exception as e:
            state['error'] = str(e)
            self.db.add_log('ERROR', f'Full forum scan aborted: {e}', exc_info=e)
        finally:
            state['running'] = False

    # ------------------------------------------------------------------
    # Radarr Tamil sync
    # ------------------------------------------------------------------
    def sync_tamil_movies_from_radarr(self) -> List[Dict]:
        """
        Pull Tamil movies from Radarr and download any missing from local DB.
        If Radarr already has the file, record it without downloading.
        """
        if not self.radarr:
            self.db.add_log('WARNING', 'Radarr not configured — cannot sync Tamil movies')
            return []

        results = []

        try:
            tamil_movies = self.radarr.get_tamil_movies()
            self.db.add_log('INFO',
                            f'Radarr sync: found {len(tamil_movies)} Tamil movies')

            for movie in tamil_movies:
                title    = movie.get('title')
                year     = movie.get('year')
                has_file = movie.get('hasFile', False)

                if not title:
                    continue

                if self.db.movie_exists(title, year):
                    self.db.add_log('DEBUG',
                                    f'Radarr sync: "{title}" ({year}) already in local DB')
                    continue

                if has_file:
                    self.db.add_log('INFO',
                                    f'Radarr sync: "{title}" ({year}) already has file — '
                                    f'saving without downloading',
                                    {'radarr_id': movie.get('id')})
                    movie_data = {
                        'title':           title,
                        'year':            year,
                        'tmdb_id':         str(movie.get('tmdbId', '')),
                        'imdb_id':         movie.get('imdbId'),
                        'poster_url':      (f"https://image.tmdb.org/t/p/w500"
                                            f"{movie.get('images', [{}])[0].get('remoteUrl', '')}"
                                            if movie.get('images') else None),
                        'is_downloaded':   True,
                        'added_to_radarr': True,
                        'source':          'radarr_sync',
                        'rejection_reason': None,
                    }
                    try:
                        movie_id = self.db.add_movie(movie_data)
                        results.append({'title': title, 'year': year,
                                        'action': 'saved_radarr_has_file',
                                        'movie_id': movie_id})
                    except Exception as e:
                        self.db.add_log('ERROR',
                                        f'Could not save Radarr movie "{title}" to DB: {e}',
                                        exc_info=e)
                    continue

                self.db.add_log('INFO',
                                f'Radarr sync: "{title}" ({year}) not downloaded — searching forum',
                                {'radarr_id': movie.get('id')})
                try:
                    result = self.process_movie(title, year, source='radarr_sync')
                    results.append(result)
                except Exception as e:
                    self.db.add_log('ERROR',
                                    f'process_movie failed for Radarr sync of "{title}": {e}',
                                    exc_info=e)

        except Exception as e:
            self.db.add_log('ERROR', f'Radarr Tamil sync aborted: {e}', exc_info=e)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _normalize_torrent(self, torrent: Dict) -> Dict:
        t = dict(torrent)
        if not t.get('torrent_name'):
            t['torrent_name'] = t.get('name') or ''
        if not t.get('name'):
            t['name'] = t.get('torrent_name') or ''
        return t

    def _safe_get_torrents(self, forum_url: str, title: str) -> List[Dict]:
        """get_movie_torrents but guaranteed not to raise. Normalizes keys."""
        try:
            raw = self.get_movie_torrents(forum_url)
            return [self._normalize_torrent(t) for t in raw]
        except Exception as e:
            self.db.add_log('ERROR',
                            f'Could not retrieve torrents for "{title}": {e}',
                            {'forum_url': forum_url}, exc_info=e)
            return []
