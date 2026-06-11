import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote, unquote, urlparse, parse_qs
import re
import os
from typing import List, Dict, Optional
import time

class WebScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    def extract_specific_links(self, url: str) -> List[Dict]:
        """
        Extract links with specific format from search results
        <a data-linktype="link" data-searchable="" href="link">Some words here</a>
        """
        try:
            print(f"[SCRAPER] Fetching search results: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            links = soup.find_all('a', attrs={'data-linktype': 'link'})
            
            print(f"[SCRAPER] Found {len(links)} search results")
            
            results = []
            for link in links:
                href = link.get('href')
                text = link.get_text(strip=True)
                
                if href and not href.startswith('http'):
                    href = urljoin(url, href)
                
                if href and text:
                    results.append({
                        'text': text,
                        'href': href
                    })
            
            return results
        
        except Exception as e:
            print(f"[SCRAPER] Error extracting search links: {e}")
            return []
    
    def _fetch_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch a page and return a parsed BeautifulSoup, or None on error."""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except Exception as e:
            print(f"[SCRAPER] Error fetching page {url}: {e}")
            return None

    def _parse_fileext_torrents(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """
        FORMAT 1 (standard, current): <a data-fileext="torrent" data-fileid=...
        href="...attachment.php?id=...&key=...">name.torrent</a>
        """
        torrent_links = soup.find_all('a', attrs={'data-fileext': 'torrent'})
        print(f"[SCRAPER] Format 1 (data-fileext): {len(torrent_links)} torrent files")

        torrents = []
        for idx, link in enumerate(torrent_links, 1):
            href = link.get('href')
            file_id = link.get('data-fileid')

            name = None
            span = link.find('span')
            if span:
                strong = span.find('strong')
                name = strong.get_text(strip=True) if strong else span.get_text(strip=True)
            if not name:
                name = link.get_text(strip=True)

            if href:
                if not href.startswith('http'):
                    href = urljoin(base_url, href)
                href = href.replace('&amp;', '&')

            if name and href:
                quality_info = self.parse_torrent_name(name)
                torrents.append({
                    'name': name,
                    'torrent_url': href,
                    'file_id': file_id,
                    'type': 'direct_download',
                    'source_format': 'fileext',
                    **quality_info
                })
                print(f"[SCRAPER] {idx}. {name}")
                print(f"[SCRAPER]    Quality: {quality_info.get('quality')}, Size: {quality_info.get('file_size')}")
        return torrents

    def _clean_magnet_name(self, dn: str) -> str:
        """
        Clean a magnet `dn` (display name) into a torrent-style name.
        Removes the leading "www.1TamilMV.<tld> - " site prefix, the trailing
        container extension, and normalises non-breaking spaces.
        """
        name = unquote(dn or '')
        name = name.replace('\xa0', ' ').replace(' ', ' ')
        # Strip leading site prefix:  "www.1TamilMV.buzz - "
        name = re.sub(r'^\s*www\.[^\s]+\s*-\s*', '', name, flags=re.IGNORECASE)
        # Strip trailing container extension (.mkv / .mp4 / .avi)
        name = re.sub(r'\.(mkv|mp4|avi)\s*$', '', name, flags=re.IGNORECASE)
        return name.strip()

    def _parse_magnet_torrents(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """
        FORMAT 2 (fallback): magnet links. The torrent file is unavailable but
        a `magnet:?xt=urn:btih:...&dn=...` link carries the full name (with
        year, quality, codec, rip type, size) in its `dn` parameter.
        """
        magnet_links = soup.select('a[href^="magnet:"]')
        print(f"[SCRAPER] Format 2 (magnet): {len(magnet_links)} magnet links")

        torrents = []
        seen = set()
        for idx, link in enumerate(magnet_links, 1):
            magnet = (link.get('href') or '').replace('&amp;', '&')
            if not magnet.startswith('magnet:') or magnet in seen:
                continue
            seen.add(magnet)

            params = parse_qs(urlparse(magnet).query)
            dn   = (params.get('dn') or [''])[0]
            btih = (params.get('xt') or [''])[0].replace('urn:btih:', '')
            name = self._clean_magnet_name(dn) or link.get_text(strip=True) or btih

            quality_info = self.parse_torrent_name(name)
            torrents.append({
                'name': name,
                'torrent_url': magnet,      # magnet IS the actionable link
                'magnet': magnet,
                'is_magnet': True,
                'file_id': btih or None,
                'type': 'magnet',
                'source_format': 'magnet',
                **quality_info
            })
            print(f"[SCRAPER] {idx}. (magnet) {name}")
            print(f"[SCRAPER]    Quality: {quality_info.get('quality')}, Size: {quality_info.get('file_size')}")
        return torrents

    def _descriptive_name_before(self, link) -> Optional[str]:
        """
        Older posts put the full title line (e.g. "ASURAGURU (2020) Tamil TRUE
        WEB-DL - 1080p - AVC - ... - 8.6GB - ESub :") in the text just BEFORE
        the attachment link, while the link's own text only carries the quality
        tail. Return the nearest preceding text that contains a (YYYY) year.
        """
        node = link.find_previous(string=re.compile(r'\(\d{4}\)'))
        if node:
            text = re.sub(r'\s*:\s*$', '', str(node).strip())   # drop trailing colon
            if re.search(r'\(\d{4}\)', text):
                return text
        return None

    def _parse_ipsattachlink_torrents(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """
        FORMAT 3 (older posts, fallback): attachment links that have a
        data-fileid and an attachment.php href but NO data-fileext attribute.
        These are rendered with class="ipsAttachLink" and the link text is only
        the quality tail (e.g. "1080p - AVC - UNTOUCHED - 8.6GB.mp4.torrent"),
        so the descriptive title line before the link is preferred for the name.
        """
        candidates = soup.select('a.ipsAttachLink, a[data-fileid]')
        torrents = []
        seen = set()
        for link in candidates:
            if link.get('data-fileext') == 'torrent':
                continue  # already handled by Format 1
            href = link.get('href') or ''
            if 'attachment.php' not in href and '/file/' not in href:
                continue

            link_text = link.get('title') or link.get_text(strip=True)
            # Prefer the descriptive title line (has title/year/rip); the link's
            # own text is used to fill in any quality detail it may lack.
            desc = self._descriptive_name_before(link)
            name = desc or link_text
            if not name:
                continue
            if not href.startswith('http'):
                href = urljoin(base_url, href)
            href = href.replace('&amp;', '&')
            if href in seen:
                continue
            seen.add(href)

            # Parse quality from the richest text available (desc + link tail).
            quality_info = self.parse_torrent_name(f'{name} {link_text}')
            torrents.append({
                'name': name,
                'torrent_url': href,
                'file_id': link.get('data-fileid'),
                'type': 'direct_download',
                'source_format': 'ipsAttachLink',
                **quality_info
            })
        print(f"[SCRAPER] Format 3 (ipsAttachLink): {len(torrents)} torrent files")
        return torrents

    def extract_all_torrents(self, url: str) -> List[Dict]:
        """
        Fetch a forum post ONCE and find its torrents using a three-tier
        fallback chain (the page may use any of the formats below):

          1. data-fileext="torrent"  — standard / current posts
          2. magnet links            — name/year/rip parsed from the magnet dn
          3. ipsAttachLink           — older posts: attachment link, no fileext

        The first tier that yields results wins.
        """
        print(f"[SCRAPER] Fetching torrents from: {url}")
        soup = self._fetch_soup(url)
        if soup is None:
            return []

        torrents = self._parse_fileext_torrents(soup, url)
        if torrents:
            return torrents

        print("[SCRAPER] No data-fileext torrents — falling back to magnet links")
        torrents = self._parse_magnet_torrents(soup, url)
        if torrents:
            return torrents

        print("[SCRAPER] No magnet links — falling back to ipsAttachLink format")
        return self._parse_ipsattachlink_torrents(soup, url)

    def extract_torrents_by_fileext(self, url: str) -> List[Dict]:
        """
        Backwards-compatible entry point. Now runs the full fallback chain
        (data-fileext → magnet → ipsAttachLink) so existing callers keep
        working on older posts too.
        """
        return self.extract_all_torrents(url)

    def extract_links_with_ipshover(self, url: str) -> List[Dict]:
        """
        Extract forum links with data-ipshover attributes
        Used for getting latest movies from forum listing
        """
        try:
            print(f"[SCRAPER] Fetching forum page: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Method 1: Find links with data-ipshover
            ipshover_links = soup.find_all('a', attrs={'data-ipshover': True})
            
            print(f"[SCRAPER] Found {len(ipshover_links)} forum links")
            
            links = []
            for link in ipshover_links:
                href = link.get('href')
                title = link.get('title')
                
                # Get text from span or direct text
                text = None
                span = link.find('span')
                text = span.get_text(strip=True) if span else link.get_text(strip=True)
                
                # Convert to absolute URL
                if href and not href.startswith('http'):
                    href = urljoin(url, href)
                
                if href and text:
                    links.append({
                        'href': href,
                        'text': text,
                        'title': title if title else ''
                    })
            
            # Fallback if no ipshover links found
            if not links:
                print("[SCRAPER] No ipshover links, trying generic extraction")
                all_links = soup.find_all('a', href=True)
                
                for link in all_links:
                    href = link.get('href')
                    title = link.get('title')
                    span = link.find('span')
                    text = span.get_text(strip=True) if span else link.get_text(strip=True)
                    
                    if href and not href.startswith('http'):
                        href = urljoin(url, href)
                    
                    if href and text and len(text) > 0:
                        links.append({
                            'href': href,
                            'text': text,
                            'title': title if title else ''
                        })
            
            return links
        
        except Exception as e:
            print(f"[SCRAPER] Error extracting forum links: {e}")
            return []
    
    def get_total_pages(self, url: str) -> int:
        """
        Detect the total number of pages of an IPS forum.
        The pagination element is <ul class="ipsPagination" data-pages="97"
        data-ipsPagination-pages="97">. Reads data-pages directly.
        Returns 1 if the element is absent (single-page forum) or on error.
        """
        try:
            print(f"[SCRAPER] Detecting total pages: {url}")
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            pagination = soup.find('ul', class_='ipsPagination')
            if pagination:
                # BeautifulSoup lowercases attribute names, so
                # data-ipsPagination-pages becomes data-ipspagination-pages
                for attr in ('data-pages', 'data-ipspagination-pages'):
                    val = pagination.get(attr)
                    if val and str(val).strip().isdigit():
                        total = int(str(val).strip())
                        print(f"[SCRAPER] Total pages detected: {total}")
                        return total

                # Fallback: page-jump form input max attribute (max='97')
                page_input = pagination.find('input', attrs={'max': True})
                if page_input and str(page_input.get('max', '')).strip().isdigit():
                    total = int(str(page_input['max']).strip())
                    print(f"[SCRAPER] Total pages from input max: {total}")
                    return total

                # Last-resort fallback: parse the "Page 1 of 97" jump label text.
                m = re.search(r'Page\s+\d+\s+of\s+(\d+)',
                              pagination.get_text(' ', strip=True), re.IGNORECASE)
                if m:
                    total = int(m.group(1))
                    print(f"[SCRAPER] Total pages from 'Page X of Y' text: {total}")
                    return total

            print("[SCRAPER] No pagination found — assuming 1 page")
            return 1

        except Exception as e:
            print(f"[SCRAPER] Error detecting total pages: {e}")
            return 1

    def download_torrent(self, torrent_url: str, filename: str = None) -> Optional[str]:
        """
        Download a torrent file
        Returns the filepath if successful
        """
        # Magnet links are not downloadable files — they must be handed to
        # qBittorrent directly (see MovieProcessor.download_and_add_torrent).
        if torrent_url and torrent_url.startswith('magnet:'):
            print(f"[SCRAPER] Skipping file download for magnet link: {filename}")
            return None

        try:
            print(f"[SCRAPER] Downloading torrent: {filename or torrent_url}")
            response = requests.get(torrent_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            # Create downloads folder
            downloads_dir = os.path.join(os.path.dirname(__file__), '..', 'downloads')
            os.makedirs(downloads_dir, exist_ok=True)
            
            # Determine filename
            if not filename:
                if 'Content-Disposition' in response.headers:
                    filename = response.headers['Content-Disposition'].split('filename=')[1].strip('"')
                else:
                    filename = 'torrent_file.torrent'
            
            if not filename.endswith('.torrent'):
                filename += '.torrent'
            
            filepath = os.path.join(downloads_dir, filename)
            
            # Write file
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            file_size = len(response.content) / 1024
            print(f"[SCRAPER] ✓ Downloaded: {filepath} ({file_size:.2f} KB)")
            return filepath
        
        except Exception as e:
            print(f"[SCRAPER] ✗ Error downloading torrent: {e}")
            return None
    
    def parse_torrent_name(self, name: str) -> Dict:
        """
        Parse torrent name to extract quality, codec, and file size
        Example: "Movie (2016) Tamil TRUE WEB-DL - 1080p - AVC - 2.6GB"
        """
        info = {
            'quality': None,
            'codec': None,
            'file_size': None,
            'rip_type': None
        }

        # Extract rip type (source of the rip) — order matters: most
        # specific / highest-quality patterns first.
        rip_patterns = [
            (r'\bBlu[- ]?Ray\b|\bBDRip\b|\bBRRip\b',          'BluRay'),
            (r'\b(?:TRUE\s+)?WEB[- ]?DL\b',                   'WEB-DL'),
            (r'\bWEB[- ]?Rip\b',                              'WEBRip'),
            (r'\bHQ[- ]?(?:HD)?Rip\b|\bHD[- ]?Rip\b',         'HDRip'),
            (r'\bDVD[- ]?Rip\b',                              'DVDRip'),
            (r'\bHDTV[- ]?Rip\b|\bHDTV\b',                    'HDTV'),
            (r'\bHD[- ]?TC\b|\bHDTC\b',                       'HDTC'),
            (r'\bPre[- ]?DVD\b|\bDVD[- ]?Scr\b',              'PreDVD'),
            (r'\bCAM\b|\bHQ[- ]?CAM\b|\bTS\b',                'CAM/TS'),
        ]
        for pattern, label in rip_patterns:
            if re.search(pattern, name, re.IGNORECASE):
                info['rip_type'] = label
                break
        
        # Extract quality (4K, 2160p, 1080p, 720p, etc.)
        quality_patterns = [
            r'\b(4K|2160p|1080p|720p|480p)\b',
            r'\b(UHD|FHD|HD)\b'
        ]
        for pattern in quality_patterns:
            match = re.search(pattern, name, re.IGNORECASE)
            if match:
                info['quality'] = match.group(1)
                break
        
        # Extract codec (AVC, HEVC, x264, x265, etc.)
        codec_patterns = [
            r'\b(HEVC|AVC|x264|x265|H\.264|H\.265)\b'
        ]
        for pattern in codec_patterns:
            match = re.search(pattern, name, re.IGNORECASE)
            if match:
                info['codec'] = match.group(1)
                break
        
        # Extract file size (2.6GB, 1.4GB, 800MB, etc.)
        size_pattern = r'\b(\d+\.?\d*\s?(?:GB|MB|TB))\b'
        matches = re.findall(size_pattern, name, re.IGNORECASE)
        if matches:
            # Get the first size mentioned (usually the main file)
            info['file_size'] = matches[0]
        
        return info
    
    def parse_movie_title_year(self, text: str) -> Dict:
        """
        Parse movie title and year from forum post title
        Example: "Theri (2016) Tamil TRUE WEB-DL..." -> {title: "Theri", year: 2016}
        """
        # Pattern to match: Title (Year)
        pattern = r'^(.+?)\s*\((\d{4})\)'
        match = re.search(pattern, text)
        
        if match:
            return {
                'title': match.group(1).strip(),
                'year': int(match.group(2))
            }
        else:
            # Fallback: try to extract year separately
            year_match = re.search(r'\((\d{4})\)', text)
            if year_match:
                year = int(year_match.group(1))
                title = text[:year_match.start()].strip()
                return {'title': title, 'year': year}
            else:
                # No year found, return whole text as title
                return {'title': text.strip(), 'year': None}
    
    def check_website_accessibility(self, base_url: str) -> bool:
        """
        Check if website is accessible
        """
        try:
            response = requests.get(base_url, headers=self.headers, timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def find_current_domain(self, website_name: str) -> Optional[str]:
        """
        Search Google to find current domain for a website
        (Simplified version - in production, use actual Google search API or web scraping)
        """
        # This is a placeholder - in production, implement Google search
        # For now, return common variations
        common_extensions = ['.cymru', '.com', '.org', '.net', '.movies', '.tv', '.to', '.boo']
        
        for ext in common_extensions:
            test_url = f"https://{website_name}{ext}"
            if self.check_website_accessibility(test_url):
                return test_url
        
        return None
    
    def match_quality_preference(self, torrents: List[Dict], preferred_quality: str, 
                                 preferred_codec: str) -> Optional[Dict]:
        """
        Match torrents to quality preferences
        Returns best matching torrent or None
        """
        # First try exact match
        for torrent in torrents:
            if (torrent.get('quality') == preferred_quality and 
                torrent.get('codec') == preferred_codec):
                return torrent
        
        # Try matching quality only (different codec)
        for torrent in torrents:
            if torrent.get('quality') == preferred_quality:
                return torrent
        
        return None
    
    def get_available_qualities(self, torrents: List[Dict]) -> List[str]:
        """
        Get list of unique available qualities from torrents
        """
        qualities = set()
        for torrent in torrents:
            quality = torrent.get('quality')
            codec = torrent.get('codec')
            if quality:
                qual_str = f"{quality}"
                if codec:
                    qual_str += f" {codec}"
                qualities.add(qual_str)
        
        return sorted(list(qualities))
