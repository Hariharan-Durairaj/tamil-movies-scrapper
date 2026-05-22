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

    def reload_tasks(self):
        """Reload scheduled tasks when settings change"""
        if self.running:
            schedule.clear()  # Clear all existing schedules
            self._register_tasks()
            self.db.add_log('INFO', 'Scheduler tasks reloaded')

    def _register_tasks(self):
        settings  = self.db.get_all_settings()
        scan_time = settings.get('daily_scan_time', '16:50')

        if settings.get('daily_scan_enabled') == 'true':
            # Convert time to 24-hour format if needed
            scan_time = self._normalize_time(scan_time)
            
            try:
                job = schedule.every().day.at(scan_time).do(self._daily_forum_scan)
                
                # Check if scheduled time has already passed today
                from datetime import datetime, time as dt_time
                now = datetime.now()
                scheduled_hour, scheduled_minute = map(int, scan_time.split(':'))
                scheduled_time_today = now.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)
                
                # If we just scheduled it and the time has passed today, it won't run until tomorrow
                # Log this clearly
                if now > scheduled_time_today:
                    next_run = scheduled_time_today.replace(day=scheduled_time_today.day + 1)
                    self.db.add_log('INFO',
                                    f'Scheduled daily forum scan (next run tomorrow)',
                                    {'time': scan_time, 'next_run': next_run.strftime('%Y-%m-%d %H:%M')})
                else:
                    self.db.add_log('INFO',
                                    f'Scheduled daily forum scan (runs today)',
                                    {'time': scan_time, 'next_run': scheduled_time_today.strftime('%Y-%m-%d %H:%M')})
                    
            except Exception as e:
                self.db.add_log('ERROR',
                                f'Invalid time format: {scan_time}. Use 24-hour format (HH:MM)',
                                exc_info=e)
        else:
            self.db.add_log('INFO', 'Daily forum scan is disabled')

        schedule.every().monday.at("00:00").do(self._check_website_domain)
        schedule.every(30).days.do(self._clean_old_logs)
    
    def _normalize_time(self, time_str):
        """Convert time to 24-hour format (HH:MM)"""
        import re
        
        # Already in 24-hour format
        if re.match(r'^\d{1,2}:\d{2}$', time_str):
            parts = time_str.split(':')
            hour = int(parts[0])
            minute = int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
        
        # Try to parse AM/PM format
        am_pm_match = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)$', time_str.lower().strip())
        if am_pm_match:
            hour = int(am_pm_match.group(1))
            minute = int(am_pm_match.group(2))
            meridiem = am_pm_match.group(3)
            
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0
                
            return f"{hour:02d}:{minute:02d}"
        
        # Return original if can't parse
        return time_str

    # ------------------------------------------------------------------
    def _daily_forum_scan(self):
        started_at = datetime.now()
        self.db.add_log('INFO', 'Scheduled forum scan: task entered')
        try:
            settings  = self.db.get_all_settings()
            max_pages = int(settings.get('scan_pages', '3'))
            max_links = int(settings.get('scan_links', '50'))

            # Resolve next run time from settings (fixes NameError on scheduled_hour/minute)
            scan_time = self._normalize_time(settings.get('daily_scan_time', '16:50'))
            try:
                scan_hour, scan_minute = map(int, scan_time.split(':'))
            except Exception:
                scan_hour, scan_minute = 16, 50

            self.db.add_log('INFO', 'Scheduled daily forum scan starting',
                            {'max_pages': max_pages, 'max_links': max_links,
                             'scheduled_time': scan_time})
            self.db.update_task('daily_forum_scan',
                                last_run=started_at.isoformat(),
                                status='running')

            results = self.processor.scan_forum_for_new_movies(
                max_pages=max_pages,
                max_links=max_links
            )

            # Compute next run = tomorrow at the scheduled time
            from datetime import timedelta
            next_run_dt = (started_at + timedelta(days=1)).replace(
                hour=scan_hour, minute=scan_minute, second=0, microsecond=0)

            self.db.update_task('daily_forum_scan',
                                status='completed',
                                next_run=next_run_dt.isoformat())
            self.db.add_log('INFO',
                            'Scheduled forum scan finished',
                            {'movies_processed': len(results),
                             'duration_seconds': round((datetime.now() - started_at).total_seconds()),
                             'next_run': next_run_dt.strftime('%Y-%m-%d %H:%M')})

        except Exception as e:
            self.db.add_log('ERROR',
                            f'Daily forum scan task failed: {e}',
                            exc_info=e)
            try:
                self.db.update_task('daily_forum_scan', status='failed')
            except Exception:
                pass

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
