import schedule
import time
import threading
from datetime import datetime
from database import Database
from movie_processor import MovieProcessor


class TaskScheduler:
    """Background task scheduler for automated operations"""

    def __init__(self, db: Database, processor: MovieProcessor):
        self.db        = db
        self.processor = processor
        self.running   = False
        self.thread    = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread  = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        self.db.add_log('INFO', 'Task scheduler started')

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        self.db.add_log('INFO', 'Task scheduler stopped')

    def _run_scheduler(self):
        self._register_tasks()
        while self.running:
            try:
                schedule.run_pending()
                time.sleep(60)
            except Exception as e:
                self.db.add_log('ERROR',
                                f'Scheduler main loop error: {e}',
                                exc_info=e)

    def _register_tasks(self):
        settings  = self.db.get_all_settings()
        scan_time = settings.get('daily_scan_time', '02:00')

        if settings.get('daily_scan_enabled') == 'true':
            schedule.every().day.at(scan_time).do(self._daily_forum_scan)
            self.db.add_log('INFO',
                            f'Scheduled daily forum scan',
                            {'time': scan_time})

        schedule.every().monday.at("00:00").do(self._check_website_domain)
        schedule.every(30).days.do(self._clean_old_logs)

    # ------------------------------------------------------------------
    def _daily_forum_scan(self):
        try:
            settings  = self.db.get_all_settings()
            max_pages = int(settings.get('scan_pages', '3'))
            max_links = int(settings.get('scan_links', '50'))

            self.db.add_log('INFO', 'Scheduled daily forum scan starting',
                            {'max_pages': max_pages, 'max_links': max_links})
            self.db.update_task('daily_forum_scan',
                                last_run=datetime.now().isoformat(),
                                status='running')

            results = self.processor.scan_forum_for_new_movies(
                max_pages=max_pages,
                max_links=max_links
            )

            self.db.update_task('daily_forum_scan',
                                status='completed',
                                next_run=datetime.now().replace(
                                    hour=2, minute=0, second=0).isoformat())
            self.db.add_log('INFO',
                            f'Scheduled forum scan finished',
                            {'movies_processed': len(results)})

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Daily forum scan task failed: {e}',
                            exc_info=e)
            self.db.update_task('daily_forum_scan', status='failed')

    # ------------------------------------------------------------------
    def _check_website_domain(self):
        """
        Weekly task: verify forum is reachable. If not, use DomainFinder
        (Google search via visible Chrome) to locate the new domain and
        update the full_domain, forum_url, and search_url settings automatically.
        """
        try:
            self.db.add_log('INFO', 'Weekly website domain check starting')
            forum_url    = self.db.get_setting('forum_url', '')
            website_base = self.db.get_setting('website_base', 'www.1tamilmv')

            from scraper import WebScraper
            from api_clients import DomainFinder
            scraper = WebScraper()

            if not scraper.check_website_accessibility(forum_url):
                self.db.add_log('WARNING',
                                'Forum URL is not accessible — searching for new domain',
                                {'forum_url': forum_url})
                finder     = DomainFinder()
                new_domain = finder.find_domain(website_base)
                if new_domain:
                    # Build full origin (scheme + domain)
                    new_origin = f"https://{new_domain}"
                    # Derive new forum_url and search_url using the old path suffixes
                    import urllib.parse
                    old_parsed    = urllib.parse.urlparse(forum_url)
                    new_forum_url = new_origin + old_parsed.path

                    old_search = self.db.get_setting('search_url', '')
                    old_search_parsed = urllib.parse.urlparse(old_search)
                    new_search_url    = new_origin + old_search_parsed.path + (
                        ('?' + old_search_parsed.query) if old_search_parsed.query else ''
                    )

                    self.db.update_settings({
                        'full_domain':  new_domain,
                        'forum_url':    new_forum_url,
                        'search_url':   new_search_url,
                    })
                    self.processor.refresh_clients()
                    self.db.add_log('INFO',
                                    'Domain updated automatically',
                                    {'new_domain':    new_domain,
                                     'new_forum_url': new_forum_url,
                                     'new_search_url': new_search_url})
                else:
                    self.db.add_log('ERROR',
                                    f'Could not locate a reachable domain for "{website_base}"',
                                    {'website_base': website_base})
            else:
                self.db.add_log('INFO', 'Domain check passed — forum is reachable',
                                {'forum_url': forum_url})

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Website domain check failed: {e}',
                            exc_info=e)

    # ------------------------------------------------------------------
    def _clean_old_logs(self):
        try:
            self.db.add_log('INFO', 'Monthly log cleanup starting')
            self.db.clear_old_logs(days=30)
            self.db.add_log('INFO', 'Monthly log cleanup complete')
        except Exception as e:
            self.db.add_log('ERROR',
                            f'Log cleanup task failed: {e}',
                            exc_info=e)
