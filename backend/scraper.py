import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
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
    
    def extract_torrents_by_fileext(self, url: str) -> List[Dict]:
        """
        Extract torrent files using data-fileext="torrent" attribute
        Returns list of torrent download links with metadata
        """
        try:
            print(f"[SCRAPER] Fetching torrents from: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            torrent_links = soup.find_all('a', attrs={'data-fileext': 'torrent'})
            
            print(f"[SCRAPER] Found {len(torrent_links)} torrent files")
            
            torrents = []
            for idx, link in enumerate(torrent_links, 1):
                href = link.get('href')
                file_id = link.get('data-fileid')
                
                # Get torrent name
                name = None
                span = link.find('span')
                if span:
                    strong = span.find('strong')
                    name = strong.get_text(strip=True) if strong else span.get_text(strip=True)
                
                if not name:
                    name = link.get_text(strip=True)
                
                # Convert to absolute URL and unescape
                if href:
                    if not href.startswith('http'):
                        href = urljoin(url, href)
                    href = href.replace('&amp;', '&')
                
                if name and href:
                    # Extract quality and size info from name
                    quality_info = self.parse_torrent_name(name)
                    
                    torrents.append({
                        'name': name,
                        'torrent_url': href,
                        'file_id': file_id,
                        'type': 'direct_download',
                        **quality_info
                    })
                    
                    print(f"[SCRAPER] {idx}. {name}")
                    print(f"[SCRAPER]    Quality: {quality_info.get('quality')}, Size: {quality_info.get('file_size')}")
            
            return torrents
        
        except Exception as e:
            print(f"[SCRAPER] Error extracting torrents: {e}")
            return []
    
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
    
    def download_torrent(self, torrent_url: str, filename: str = None) -> Optional[str]:
        """
        Download a torrent file
        Returns the filepath if successful
        """
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
            'file_size': None
        }
        
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
