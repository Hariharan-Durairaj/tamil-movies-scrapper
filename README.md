# 🎬 Movie Automator

An automated movie downloading system that scrapes forums, checks IMDB/TMDB ratings, and manages downloads through qBittorrent and Radarr.

## Features

### Core Functionality
- **Forum Scraping**: Automatically scrape movie listings from forums
- **Rating Check**: Verify IMDB/TMDB ratings before downloading
- **Quality Selection**: Choose preferred quality (4K, 1080p, 720p) and codec (HEVC, AVC, x264)
- **Automated Download**: Send torrents to qBittorrent with category management
- **Radarr Integration**: Automatically add movies to Radarr
- **Tamil Movie Sync**: Monitor Radarr for manually added Tamil movies and auto-download

### Search & Discovery
- **Manual Search**: Search for specific movies by name
- **Automatic Scanning**: Daily scheduled scans for new releases
- **Smart Duplicate Detection**: Stop scanning after X duplicate movies found

### Database Tracking
- Complete movie database with:
  - Download status
  - IMDB ratings
  - Available qualities
  - File sizes
  - Poster images
  - Rejection reasons

### User Interface
- Modern dark-themed web interface
- Dashboard with statistics
- Movie library with filters (All, Downloaded, Pending, Rejected)
- Settings management
- Detailed logging system

## Installation

### Prerequisites
- Python 3.8 or higher
- qBittorrent with Web UI enabled
- Radarr (optional)
- OMDB API key (free from https://www.omdbapi.com/apikey.aspx)
- TMDB API key (optional, from https://www.themoviedb.org/settings/api)

### Setup

1. **Install Python Dependencies**
```bash
cd movie-automator
pip install -r requirements.txt
```

2. **Run the Server**
```bash
cd backend
python main.py
```

3. **Access the Web Interface**
Open your browser and go to: `http://localhost:8080`

4. **Initial Setup**
- On first launch, you'll be prompted to create an admin account
- Set your username and password
- You'll be automatically logged in

## Configuration

### Settings Page

Navigate to Settings in the web interface to configure:

#### General Settings
- **Port**: Server port (default: 8080, requires restart)
- **Website Base**: Forum website name (e.g., www.1tamilmv)
- **Forum URL**: Main forum listing page
- **Search URL**: Search page with `{query}` placeholder

#### Download Preferences
- **Preferred Quality**: 4K, 1080p, 720p, etc.
- **Preferred Codec**: HEVC, AVC, x264, x265
- **Rating Threshold**: Minimum IMDB rating (0-10)

#### API Configuration

**Radarr**
```
URL: http://localhost:7878
API Key: Your Radarr API key
```

**qBittorrent**
```
URL: http://localhost:8080
Username: admin
Password: your_password
```

**OMDB API**
```
API Key: Get from omdbapi.com
```

**TMDB API** (Optional)
```
API Key: Get from themoviedb.org
```

#### Automation Settings
- **Enable Daily Scan**: Automatic daily forum scanning
- **Scan Time**: Time to run daily scan (24h format)
- **Pages to Scan**: Number of forum pages
- **Max Links**: Maximum links to process
- **Duplicate Stop Count**: Stop after X duplicates found
- **Auto-download**: Download movies above rating threshold

### Test Connections
Use the "Test Connection" buttons to verify each service is properly configured.

## Usage

### Manual Movie Search

1. Go to the **Search** page
2. Enter movie name
3. Select from search results
4. System will:
   - Check IMDB rating
   - Find best quality match
   - Download if rating > threshold
   - Add to qBittorrent and Radarr

### Automatic Forum Scanning

**Dashboard Method:**
1. Click "Scan Forum for New Movies"
2. Configure pages/links to scan
3. Click "Start Scan"

**Scheduled Method:**
1. Enable "Daily Scan" in Settings → Automation
2. Set scan time
3. System will run automatically

### Radarr Tamil Movie Sync

1. Add Tamil movies to Radarr manually
2. Click "Sync Tamil Movies from Radarr" on Dashboard
3. System will find and download from forum

### Managing Movies

**Movies Page:**
- Filter by: All, Downloaded, Pending, Rejected
- View movie details (poster, rating, quality, size)
- Download manually if auto-download failed
- Delete movies from database

**Action Buttons:**
- **Download**: Start download manually
- **Details**: View full movie information
- **Delete**: Remove from database

### Override Features

When auto-processing fails, you get options to:

1. **Rating Below Threshold**: Override and download anyway
2. **Preferred Quality Not Available**: Select from available qualities
3. **Multiple Search Results**: Choose correct movie manually

## Database

SQLite database stored in: `database/movies.db`

### Tables
- **users**: User accounts
- **settings**: System configuration
- **movies**: Movie database
- **movie_qualities**: Available quality variants
- **logs**: System logs
- **scheduled_tasks**: Automation tracking

## Logging

**Access Logs:**
1. Go to **Logs** page
2. Filter by level (Debug, Info, Warning, Error)
3. Refresh or clear old logs

**Log Levels:**
- **DEBUG**: Detailed debugging information
- **INFO**: General information
- **WARNING**: Warning messages
- **ERROR**: Error messages

## Troubleshooting

### Connection Issues

**qBittorrent:**
- Ensure Web UI is enabled in qBittorrent settings
- Check username/password
- Verify URL (usually http://localhost:8080)

**Radarr:**
- Get API key from Radarr Settings → General → Security
- Verify URL (usually http://localhost:7878)

**Forum Website:**
- Check if website domain extension changed (.com, .cymru, .org)
- Update in Settings if needed

### Movies Not Downloading

1. Check logs for errors
2. Verify API connections in Settings
3. Check rating threshold settings
4. Ensure torrent links are valid

### Daily Scan Not Running

- Enable in Settings → Automation
- Check system is running at scheduled time
- Note: Currently requires manual implementation of cron job or task scheduler

## Auto-Start on Boot

### Linux (systemd)

Create `/etc/systemd/system/movie-automator.service`:

```ini
[Unit]
Description=Movie Automator
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/movie-automator/backend
ExecStart=/usr/bin/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable movie-automator
sudo systemctl start movie-automator
```

### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task
3. Trigger: At system startup
4. Action: Start a program
5. Program: `python`
6. Arguments: `C:\path\to\movie-automator\backend\main.py`

## Security Notes

- Passwords are hashed with bcrypt
- JWT tokens for authentication
- Change default SECRET_KEY in `backend/main.py` for production
- Run behind reverse proxy (nginx) for HTTPS in production

## File Structure

```
movie-automator/
├── backend/
│   ├── main.py              # FastAPI server
│   ├── database.py          # Database models
│   ├── movie_processor.py   # Core logic
│   ├── scraper.py          # Web scraping
│   └── api_clients.py      # External APIs
├── frontend/
│   ├── index.html          # Main page
│   ├── style.css           # Styles
│   └── app.js              # Frontend logic
├── database/
│   └── movies.db           # SQLite database
├── downloads/              # Torrent files
├── logs/                   # Log files
└── requirements.txt        # Python dependencies
```

## API Documentation

Access interactive API docs at: `http://localhost:8080/docs`

## Contributing

This is a personal automation project. Feel free to fork and customize for your needs.

## License

MIT License - Use at your own risk

## Disclaimer

This tool is for personal use only. Ensure you have the legal right to download content in your jurisdiction. The developers are not responsible for misuse.
