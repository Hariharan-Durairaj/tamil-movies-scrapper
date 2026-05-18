// API Configuration
const API_BASE = window.location.origin;
let authToken = localStorage.getItem('authToken');

// API Helper Functions
async function apiCall(endpoint, options = {}) {
    const config = {
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        },
        ...options
    };

    // Add auth token if available
    if (authToken && !endpoint.includes('/auth/')) {
        config.headers['Authorization'] = `Bearer ${authToken}`;
        // Also add as query param for some endpoints
        if (endpoint.includes('?')) {
            endpoint += `&token=${authToken}`;
        } else {
            endpoint += `?token=${authToken}`;
        }
    }

    const response = await fetch(`${API_BASE}${endpoint}`, config);
    
    if (response.status === 401) {
        // Token expired, logout
        logout();
        throw new Error('Session expired');
    }

    return response;
}

// Loading overlay
function showLoading(message = 'Loading...') {
    const overlay = document.getElementById('loading-overlay');
    const text = document.getElementById('loading-text');
    text.textContent = message;
    overlay.classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loading-overlay').classList.add('hidden');
}

// Toast notifications — real implementation defined later in the file

// Initialize App
async function initApp() {
    // Check if setup is needed
    const response = await apiCall('/api/auth/check-setup');
    const data = await response.json();

    if (data.setup_needed) {
        showSetupScreen();
    } else {
        if (authToken) {
            showAppScreen();
            await loadDashboard();
        } else {
            showLoginScreen();
        }
    }
}

function showSetupScreen() {
    document.getElementById('auth-screen').classList.remove('hidden');
    document.getElementById('app-screen').classList.add('hidden');
    document.getElementById('auth-submit').textContent = 'Setup Account';
    document.getElementById('auth-error').textContent = '';
}

function showLoginScreen() {
    document.getElementById('auth-screen').classList.remove('hidden');
    document.getElementById('app-screen').classList.add('hidden');
    document.getElementById('auth-submit').textContent = 'Login';
    document.getElementById('auth-error').textContent = '';
}

function showAppScreen() {
    document.getElementById('auth-screen').classList.add('hidden');
    document.getElementById('app-screen').classList.remove('hidden');
}

// Authentication
document.getElementById('auth-submit').addEventListener('click', async () => {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const errorEl = document.getElementById('auth-error');

    if (!username || !password) {
        errorEl.textContent = 'Please enter username and password';
        return;
    }

    const isSetup = document.getElementById('auth-submit').textContent === 'Setup Account';
    const endpoint = isSetup ? '/api/auth/setup' : '/api/auth/login';

    try {
        const response = await apiCall(endpoint, {
            method: 'POST',
            body: JSON.stringify({ username, password })
        });

        if (response.ok) {
            const data = await response.json();
            authToken = data.access_token;
            localStorage.setItem('authToken', authToken);
            showAppScreen();
            await loadDashboard();
        } else {
            const error = await response.json();
            errorEl.textContent = error.detail || 'Authentication failed';
        }
    } catch (error) {
        errorEl.textContent = 'Connection error';
        console.error(error);
    }
});

// Logout
document.getElementById('logout-btn').addEventListener('click', logout);

function logout() {
    authToken = null;
    localStorage.removeItem('authToken');
    stopLogStream();
    showLoginScreen();
}

// Navigation
document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
        if (e.target.id === 'logout-btn') return;
        
        e.preventDefault();
        const page = e.target.dataset.page;

        // Stop live log stream when leaving logs page
        if (page !== 'logs') {
            stopLogStream();
        }
        
        // Update active nav link
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        e.target.classList.add('active');

        // Show page
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');

        // Load page data
        if (page === 'dashboard') loadDashboard();
        else if (page === 'movies') loadMovies();
        else if (page === 'settings') loadSettings();
        else if (page === 'logs') loadLogs();
    });
});

// Dashboard
async function loadDashboard() {
    try {
        const response = await apiCall('/api/stats');
        const stats = await response.json();

        document.getElementById('stat-total').textContent = stats.total_movies;
        document.getElementById('stat-downloaded').textContent = stats.downloaded;
        document.getElementById('stat-pending').textContent = stats.pending;
        document.getElementById('stat-rejected').textContent = stats.rejected;
        document.getElementById('stat-radarr').textContent = stats.in_radarr;
    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

// Scan Forum
document.getElementById('btn-scan-forum').addEventListener('click', () => {
    document.getElementById('scan-config').classList.remove('hidden');
});

document.getElementById('btn-cancel-scan').addEventListener('click', () => {
    document.getElementById('scan-config').classList.add('hidden');
});

document.getElementById('btn-start-scan').addEventListener('click', async () => {
    const pages = document.getElementById('scan-pages').value;
    const links = document.getElementById('scan-links').value;

    showLoading('Scanning forum for new movies...');
    
    try {
        const response = await apiCall('/api/movies/scan-forum', {
            method: 'POST',
            body: JSON.stringify({
                max_pages: parseInt(pages),
                max_links: parseInt(links)
            })
        });

        const data = await response.json();
        hideLoading();
        showToast(data.message, 'success');
        document.getElementById('scan-config').classList.add('hidden');
        
        // Refresh dashboard
        await loadDashboard();
    } catch (error) {
        hideLoading();
        showToast('Error scanning forum', 'error');
        console.error(error);
    }
});

// Sync Radarr
document.getElementById('btn-sync-radarr').addEventListener('click', async () => {
    showLoading('Syncing Tamil movies from Radarr...');
    
    try {
        const response = await apiCall('/api/radarr/sync-tamil', {
            method: 'POST'
        });

        const data = await response.json();
        hideLoading();
        showToast(data.message, 'success');
        
        // Refresh dashboard
        await loadDashboard();
    } catch (error) {
        hideLoading();
        showToast('Error syncing Radarr', 'error');
        console.error(error);
    }
});

// Search Movies
document.getElementById('btn-search').addEventListener('click', async () => {
    const query = document.getElementById('search-input').value;
    
    if (!query) {
        showToast('Please enter a movie name', 'warning');
        return;
    }

    showLoading('Searching...');
    
    try {
        const response = await apiCall('/api/movies/search', {
            method: 'POST',
            body: JSON.stringify({ movie_name: query })
        });

        const data = await response.json();
        hideLoading();
        
        displaySearchResults(data.results);
    } catch (error) {
        hideLoading();
        showToast('Search error', 'error');
        console.error(error);
    }
});

function displaySearchResults(results) {
    const container = document.getElementById('search-results');
    container.innerHTML = '';

    if (results.length === 0) {
        container.innerHTML = '<p class="no-results">No results found</p>';
        return;
    }

    results.forEach(result => {
        const item = document.createElement('div');
        item.className = 'result-item';
        item.innerHTML = `
            <h4>${result.parsed_title} ${result.parsed_year ? `(${result.parsed_year})` : ''}</h4>
            <p>${result.text.substring(0, 150)}...</p>
        `;
        item.addEventListener('click', () => processSearchResult(result));
        container.appendChild(item);
    });
}

async function processSearchResult(result) {
    showLoading('Processing movie...');
    
    try {
        const response = await apiCall('/api/movies/process', {
            method: 'POST',
            body: JSON.stringify({
                title: result.parsed_title,
                year: result.parsed_year,
                forum_url: result.href
            })
        });

        const data = await response.json();
        hideLoading();

        if (data.needs_user_action) {
            showUserActionModal(data);
        } else if (data.success) {
            showToast('Movie processed successfully!', 'success');
            await loadDashboard();
        } else {
            showToast(data.message || 'Error processing movie', 'error');
        }
    } catch (error) {
        hideLoading();
        showToast('Error processing movie', 'error');
        console.error(error);
    }
}

function showUserActionModal(data) {
    const modal = document.getElementById('movie-modal');
    const modalBody = document.getElementById('modal-body');
    
    let content = `<h3>${data.title} ${data.year ? `(${data.year})` : ''}</h3>`;
    content += `<p>${data.message}</p>`;

    if (data.action_type === 'select_result') {
        content += '<div class="results-list">';
        data.options.forEach((option, idx) => {
            content += `
                <div class="result-option" data-index="${idx}">
                    <h4>${option.parsed_title} ${option.parsed_year ? `(${option.parsed_year})` : ''}</h4>
                    <p>${option.text}</p>
                </div>
            `;
        });
        content += '</div>';
        content += '<button class="btn-danger" onclick="closeModal()">Cancel</button>';
    } else if (data.action_type === 'select_quality') {
        content += '<div class="qualities-list">';
        data.options.forEach((quality, idx) => {
            content += `
                <div class="quality-option" data-movie-id="${data.movie_id}" data-quality-id="${quality.id || idx}">
                    <h4>${quality.quality || 'Unknown'} ${quality.codec || ''}</h4>
                    <p>Size: ${quality.file_size || 'N/A'}</p>
                    <button class="btn-primary">Download</button>
                </div>
            `;
        });
        content += '</div>';
    } else if (data.action_type === 'override_rating') {
        content += `
            <p>Rating: ${data.rating}/10</p>
            <button class="btn-primary" onclick="downloadMovie(${data.movie_id})">Download Anyway</button>
            <button class="btn-secondary" onclick="closeModal()">Cancel</button>
        `;
    }

    modalBody.innerHTML = content;
    modal.classList.remove('hidden');

    // Add event listeners
    modal.querySelector('.close').onclick = closeModal;
    
    // Quality selection
    modal.querySelectorAll('.quality-option button').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const parent = e.target.closest('.quality-option');
            const movieId = parent.dataset.movieId;
            const qualityId = parent.dataset.qualityId;
            downloadMovie(movieId, qualityId);
        });
    });
}

function closeModal() {
    document.getElementById('movie-modal').classList.add('hidden');
}

async function downloadMovie(movieId, qualityId = null) {
    closeModal();
    showLoading('Downloading movie...');
    
    try {
        const response = await apiCall('/api/movies/download', {
            method: 'POST',
            body: JSON.stringify({
                movie_id: parseInt(movieId),
                quality_id: qualityId ? parseInt(qualityId) : null
            })
        });

        const data = await response.json();
        hideLoading();

        if (data.success) {
            showToast('Movie download started!', 'success');
            await loadDashboard();
        } else {
            showToast('Download failed', 'error');
        }
    } catch (error) {
        hideLoading();
        showToast('Error downloading movie', 'error');
        console.error(error);
    }
}

// Movies List
let currentMovieFilter = 'all';

document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentMovieFilter = e.target.dataset.filter;
        loadMovies();
    });
});

async function loadMovies() {
    showLoading('Loading movies...');
    
    try {
        let endpoint = '/api/movies';
        if (currentMovieFilter !== 'all') {
            endpoint += `?filter=${currentMovieFilter}`;
        }

        const response = await apiCall(endpoint);
        const data = await response.json();
        hideLoading();

        // Client-side filter for radarr (the API doesn't have these yet)
        let movies = data.movies;
        if (currentMovieFilter === 'in_radarr') {
            movies = movies.filter(m => m.added_to_radarr);
        } else if (currentMovieFilter === 'not_in_radarr') {
            movies = movies.filter(m => !m.added_to_radarr);
        }

        displayMovies(movies);
    } catch (error) {
        hideLoading();
        showToast('Error loading movies', 'error');
        console.error(error);
    }
}

function displayMovies(movies) {
    const container = document.getElementById('movies-list');
    container.innerHTML = '';

    if (movies.length === 0) {
        container.innerHTML = '<p class="no-results">No movies found</p>';
        return;
    }

    movies.forEach(movie => {
        const card = document.createElement('div');
        card.className = 'movie-card';

        const posterUrl = movie.poster_url || '';
        const rating = movie.imdb_rating ? parseFloat(movie.imdb_rating) : null;
        const ratingClass = rating && rating < 7 ? 'low' : '';

        const posterHtml = posterUrl
            ? `<img src="${posterUrl}" alt="${movie.title}" onload="this.classList.add('loaded')" onerror="this.style.display='none'">`
            : '';

        const radarrBadge = movie.added_to_radarr
            ? `<span class="status-badge radarr-yes" title="In Radarr">&#x1F7E2; Radarr</span>`
            : `<span class="status-badge radarr-no" title="Not in Radarr">&#x1F534; Radarr</span>`;

        const addRadarrBtn = !movie.added_to_radarr
            ? `<button class="btn-secondary btn-add-radarr" data-id="${movie.id}" title="Add to Radarr">+ Radarr</button>`
            : '';

        const downloadBtn = !movie.is_downloaded
            ? `<button class="btn-primary btn-download" data-id="${movie.id}">Download</button>`
            : '';

        card.innerHTML = `
            <div class="movie-poster-wrapper">
                ${posterHtml}
            </div>
            <div class="movie-info">
                <div class="movie-title">${movie.title} ${movie.year ? `(${movie.year})` : ''}</div>
                <div class="movie-meta">
                    <span>${movie.file_size || 'N/A'}</span>
                    ${rating ? `<span class="movie-rating ${ratingClass}">${rating}/10</span>` : ''}
                </div>
                <div class="movie-status">
                    ${movie.is_downloaded ? '<span class="status-badge downloaded">Downloaded</span>' : ''}
                    ${movie.rejection_reason ? '<span class="status-badge rejected">Rejected</span>' : ''}
                    ${movie.downloaded_quality ? `<span class="status-badge">${movie.downloaded_quality}</span>` : ''}
                    ${radarrBadge}
                </div>
                <div class="movie-actions">
                    ${downloadBtn}
                    <button class="btn-secondary btn-details" data-id="${movie.id}">Details</button>
                    <button class="btn-icon btn-refresh-imdb" data-id="${movie.id}" title="Refresh IMDB rating & poster">&#x21BB; IMDB</button>
                    <button class="btn-icon btn-redownload" data-id="${movie.id}" title="Re-search forum and re-download torrent">&#x2B07; Torrent</button>
                    <button class="btn-icon btn-edit"
                        data-id="${movie.id}"
                        data-title="${(movie.title || '').replace(/"/g, '&quot;')}"
                        data-year="${movie.year || ''}"
                        data-imdbid="${movie.imdb_id || ''}"
                        title="Correct movie info">&#x270F; Edit</button>
                    ${addRadarrBtn}
                    <button class="btn-danger btn-delete" data-id="${movie.id}">&#x1F5D1;</button>
                </div>
            </div>
        `;

        container.appendChild(card);
    });

    // ── Download ─────────────────────────────────────────────────────
    document.querySelectorAll('.btn-download').forEach(btn => {
        btn.addEventListener('click', e => downloadMovie(e.target.dataset.id));
    });

    // ── Details ──────────────────────────────────────────────────────
    document.querySelectorAll('.btn-details').forEach(btn => {
        btn.addEventListener('click', async e => {
            await showMovieDetails(e.target.dataset.id);
        });
    });

    // ── Refresh IMDB ─────────────────────────────────────────────────
    document.querySelectorAll('.btn-refresh-imdb').forEach(btn => {
        btn.addEventListener('click', async e => {
            const b = e.target;
            const movieId = b.dataset.id;
            b.textContent = '...';
            b.disabled = true;
            try {
                const res = await apiCall(`/api/movies/${movieId}/refresh-imdb`, { method: 'POST' });
                showToast(res.ok ? 'IMDB refresh started — reload in a moment.' : 'Refresh failed',
                          res.ok ? 'success' : 'error');
            } catch (err) {
                showToast('Error starting refresh', 'error');
            } finally {
                b.innerHTML = '&#x21BB; IMDB';
                b.disabled = false;
            }
        });
    });

    // ── Re-download torrent ───────────────────────────────────────────
    document.querySelectorAll('.btn-redownload').forEach(btn => {
        btn.addEventListener('click', async e => {
            const b = e.target;
            const movieId = b.dataset.id;
            b.textContent = '...';
            b.disabled = true;
            try {
                const res  = await apiCall(`/api/movies/${movieId}/redownload`, { method: 'POST' });
                const data = await res.json();
                showToast(data.message, res.ok ? 'success' : 'error');
            } catch (err) {
                showToast('Error starting re-download', 'error');
            } finally {
                b.innerHTML = '&#x2B07; Torrent';
                b.disabled = false;
            }
        });
    });

    // ── Edit / manual update ─────────────────────────────────────────
    document.querySelectorAll('.btn-edit').forEach(btn => {
        btn.addEventListener('click', e => {
            const b = e.target;
            showEditMovieModal(b.dataset.id, b.dataset.title,
                               b.dataset.year, b.dataset.imdbid);
        });
    });

    // ── Add to Radarr ────────────────────────────────────────────────
    document.querySelectorAll('.btn-add-radarr').forEach(btn => {
        btn.addEventListener('click', async e => {
            const b = e.target;
            const movieId = b.dataset.id;
            b.textContent = '...';
            b.disabled = true;
            try {
                const res  = await apiCall(`/api/movies/${movieId}/add-to-radarr`, { method: 'POST' });
                const data = await res.json();
                showToast(data.message, data.success ? 'success' : 'error');
                if (data.success) await loadMovies();
            } catch (err) {
                showToast('Error adding to Radarr', 'error');
                b.textContent = '+ Radarr';
                b.disabled = false;
            }
        });
    });

    // ── Delete ───────────────────────────────────────────────────────
    document.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', async e => {
            if (confirm('Are you sure you want to delete this movie?')) {
                await deleteMovie(e.target.dataset.id);
            }
        });
    });
}

// ── Edit movie modal ──────────────────────────────────────────────────────
function showEditMovieModal(movieId, currentTitle, currentYear, currentImdbId) {
    const modal    = document.getElementById('movie-modal');
    const modalBody = document.getElementById('modal-body');

    modalBody.innerHTML = `
        <h3>Edit Movie Info</h3>
        <p style="color:var(--text-secondary);margin-bottom:1rem;">
            Correct the title, year, or IMDB ID so the system re-fetches the right poster and rating.
        </p>
        <div class="form-group">
            <label>Title:</label>
            <input type="text" id="edit-title" class="search-input" value="${(currentTitle || '').replace(/"/g, '&quot;')}">
        </div>
        <div class="form-group">
            <label>Year:</label>
            <input type="number" id="edit-year" class="search-input" value="${currentYear || ''}" placeholder="e.g. 2024">
        </div>
        <div class="form-group">
            <label>IMDB ID (optional):</label>
            <input type="text" id="edit-imdbid" class="search-input" value="${currentImdbId || ''}" placeholder="e.g. tt1234567">
            <small style="color:var(--text-secondary);">If you know the exact IMDB ID, entering it ensures the correct movie is fetched.</small>
        </div>
        <div style="display:flex;gap:1rem;margin-top:1.5rem;">
            <button id="btn-save-edit" class="btn-primary">Save & Refresh</button>
            <button class="btn-secondary" onclick="closeModal()">Cancel</button>
        </div>
        <p id="edit-status" style="margin-top:1rem;color:var(--success-color);"></p>
    `;

    modal.classList.remove('hidden');
    modal.querySelector('.close').onclick = closeModal;

    document.getElementById('btn-save-edit').addEventListener('click', async () => {
        const title  = document.getElementById('edit-title').value.trim();
        const year   = parseInt(document.getElementById('edit-year').value) || null;
        const imdbId = document.getElementById('edit-imdbid').value.trim() || null;
        const statusEl = document.getElementById('edit-status');

        if (!title) {
            statusEl.style.color = 'var(--danger-color)';
            statusEl.textContent = 'Title cannot be empty.';
            return;
        }

        document.getElementById('btn-save-edit').disabled = true;
        document.getElementById('btn-save-edit').textContent = 'Saving...';
        statusEl.textContent = '';

        try {
            const res  = await apiCall(`/api/movies/${movieId}/manual-update`, {
                method: 'POST',
                body: JSON.stringify({ title, year, imdb_id: imdbId })
            });
            const data = await res.json();

            if (data.success) {
                statusEl.style.color = 'var(--success-color)';
                statusEl.textContent = `Updated! Rating: ${data.rating ?? 'N/A'}/10`;
                setTimeout(() => {
                    closeModal();
                    loadMovies();
                }, 1200);
            } else {
                statusEl.style.color = 'var(--danger-color)';
                statusEl.textContent = data.message || 'Update failed.';
            }
        } catch (err) {
            statusEl.style.color = 'var(--danger-color)';
            statusEl.textContent = 'Network error.';
        } finally {
            const saveBtn = document.getElementById('btn-save-edit');
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save & Refresh';
            }
        }
    });
}


async function showMovieDetails(movieId) {
    showLoading('Loading details...');
    
    try {
        const [movieRes, radarrRes] = await Promise.all([
            apiCall(`/api/movies/${movieId}`),
            apiCall(`/api/movies/${movieId}/radarr-status`),
        ]);
        const movie       = await movieRes.json();
        const radarrInfo  = radarrRes.ok ? await radarrRes.json() : null;
        hideLoading();

        const modal     = document.getElementById('movie-modal');
        const modalBody = document.getElementById('modal-body');

        // ── Radarr status block ──────────────────────────────────────
        let radarrBlock = '';
        if (radarrInfo) {
            if (radarrInfo.in_radarr) {
                const fileStatus = radarrInfo.has_file
                    ? `<span style="color:var(--success-color);font-weight:bold;">✔ File present in Radarr</span>`
                    : `<span style="color:var(--warning-color);font-weight:bold;">⚠ In Radarr but NO file yet</span>`;

                let fileDetails = '';
                if (radarrInfo.has_file) {
                    const sizeMB = radarrInfo.file_size_bytes
                        ? (radarrInfo.file_size_bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
                        : 'N/A';
                    fileDetails = `
                        <div style="margin-top:0.5rem;padding:0.75rem;background:var(--bg-card);border-radius:6px;font-size:0.9rem;">
                            <p><strong>Path:</strong> ${radarrInfo.file_path || 'N/A'}</p>
                            <p><strong>Quality:</strong> ${radarrInfo.quality || 'N/A'}</p>
                            <p><strong>File Size:</strong> ${sizeMB}</p>
                            <p><strong>Added:</strong> ${radarrInfo.date_added ? new Date(radarrInfo.date_added).toLocaleDateString() : 'N/A'}</p>
                        </div>`;
                }

                radarrBlock = `
                    <div style="margin:1rem 0;padding:1rem;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border-color);">
                        <h4 style="margin-bottom:0.5rem;">🎬 Radarr Status</h4>
                        <p>${fileStatus}</p>
                        <p style="font-size:0.85rem;color:var(--text-secondary);">
                            Monitored: ${radarrInfo.monitored ? 'Yes' : 'No'}
                            ${radarrInfo.radarr_title && radarrInfo.radarr_title !== movie.title
                                ? ` &nbsp;|&nbsp; Radarr title: <em>${radarrInfo.radarr_title}</em>` : ''}
                        </p>
                        ${fileDetails}
                    </div>`;
            } else if (radarrInfo.error) {
                radarrBlock = `
                    <div style="margin:1rem 0;padding:0.75rem;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border-color);">
                        <h4 style="margin-bottom:0.25rem;">🎬 Radarr Status</h4>
                        <span style="color:var(--text-secondary);font-size:0.9rem;">${radarrInfo.error}</span>
                    </div>`;
            } else {
                radarrBlock = `
                    <div style="margin:1rem 0;padding:0.75rem;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border-color);">
                        <h4 style="margin-bottom:0.25rem;">🎬 Radarr Status</h4>
                        <span style="color:var(--danger-color);font-weight:bold;">✘ Not in Radarr</span>
                    </div>`;
            }
        }

        // ── Main details ─────────────────────────────────────────────
        let content = `
            <h3>${escapeHtml(movie.title)} ${movie.year ? `(${movie.year})` : ''}</h3>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;margin-bottom:1rem;">
                <p><strong>Rating:</strong> ${movie.imdb_rating || 'N/A'}/10</p>
                <p><strong>IMDB ID:</strong> ${movie.imdb_id
                    ? `<a href="https://www.imdb.com/title/${movie.imdb_id}" target="_blank" style="color:var(--primary-color);">${movie.imdb_id}</a>`
                    : 'N/A'}</p>
                <p><strong>Quality:</strong> ${movie.downloaded_quality || 'N/A'}</p>
                <p><strong>File Size:</strong> ${movie.file_size || 'N/A'}</p>
                <p><strong>Local Status:</strong> ${movie.is_downloaded
                    ? '<span style="color:var(--success-color);">Downloaded</span>'
                    : '<span style="color:var(--text-secondary);">Not Downloaded</span>'}</p>
                <p><strong>Source:</strong> ${movie.source || 'N/A'}</p>
            </div>
            ${radarrBlock}
        `;

        if (movie.rejection_reason) {
            content += `
                <div style="margin:0.75rem 0;padding:0.75rem;background:rgba(239,68,68,0.1);border-radius:6px;border-left:3px solid var(--danger-color);">
                    <strong>Note:</strong> ${escapeHtml(movie.rejection_reason)}
                </div>`;
        }

        if (movie.available_qualities && movie.available_qualities.length > 0) {
            content += '<h4 style="margin:1rem 0 0.5rem;">Available Qualities:</h4><ul style="padding-left:1.5rem;">';
            movie.available_qualities.forEach(q => {
                content += `<li>${q.quality || '?'} ${q.codec || ''} — ${q.file_size || 'N/A'}</li>`;
            });
            content += '</ul>';
        }

        content += '<div style="margin-top:1.5rem;"><button class="btn-secondary" onclick="closeModal()">Close</button></div>';

        modalBody.innerHTML = content;
        modal.classList.remove('hidden');
        modal.querySelector('.close').onclick = closeModal;

    } catch (error) {
        hideLoading();
        showToast('Error loading details', 'error');
        console.error(error);
    }
}

async function deleteMovie(movieId) {
    showLoading('Deleting...');
    
    try {
        const response = await apiCall(`/api/movies/${movieId}`, {
            method: 'DELETE'
        });

        hideLoading();
        
        if (response.ok) {
            showToast('Movie deleted', 'success');
            await loadMovies();
        } else {
            showToast('Delete failed', 'error');
        }
    } catch (error) {
        hideLoading();
        showToast('Error deleting movie', 'error');
        console.error(error);
    }
}

// Settings
let currentSettings = {};

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        
        const tab = e.target.dataset.tab;
        document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        document.getElementById(`settings-${tab}`).classList.add('active');
    });
});

async function loadSettings() {
    showLoading('Loading settings...');
    
    try {
        const response = await apiCall('/api/settings');
        currentSettings = await response.json();
        hideLoading();

        // Populate form fields
        document.querySelectorAll('.setting-input').forEach(input => {
            const key = input.dataset.key;
            if (currentSettings[key] !== undefined) {
                if (input.type === 'checkbox') {
                    input.checked = currentSettings[key] === 'true';
                } else {
                    input.value = currentSettings[key];
                }
            }
        });

        document.querySelectorAll('.setting-checkbox').forEach(checkbox => {
            const key = checkbox.dataset.key;
            if (currentSettings[key] !== undefined) {
                checkbox.checked = currentSettings[key] === 'true';
            }
        });

        // Populate the read-only Domain tab URL previews
        const forumPreview = document.getElementById('domain-forum-url-preview');
        const searchPreview = document.getElementById('domain-search-url-preview');
        if (forumPreview && currentSettings['forum_url'])
            forumPreview.value = currentSettings['forum_url'];
        if (searchPreview && currentSettings['search_url'])
            searchPreview.value = currentSettings['search_url'];
    } catch (error) {
        hideLoading();
        showToast('Error loading settings', 'error');
        console.error(error);
    }
}

document.getElementById('btn-save-settings').addEventListener('click', async () => {
    const settings = {};

    document.querySelectorAll('.setting-input').forEach(input => {
        const key = input.dataset.key;
        if (input.type === 'checkbox') {
            settings[key] = input.checked ? 'true' : 'false';
        } else {
            settings[key] = input.value;
        }
    });

    document.querySelectorAll('.setting-checkbox').forEach(checkbox => {
        const key = checkbox.dataset.key;
        settings[key] = checkbox.checked ? 'true' : 'false';
    });

    showLoading('Saving settings...');
    
    try {
        const response = await apiCall('/api/settings', {
            method: 'POST',
            body: JSON.stringify({ settings })
        });

        hideLoading();
        
        if (response.ok) {
            showToast('Settings saved successfully!', 'success');
            currentSettings = settings;
        } else {
            showToast('Failed to save settings', 'error');
        }
    } catch (error) {
        hideLoading();
        showToast('Error saving settings', 'error');
        console.error(error);
    }
});

// Test connections
document.getElementById('test-radarr').addEventListener('click', async () => {
    await testConnection('radarr');
});

document.getElementById('test-qbittorrent').addEventListener('click', async () => {
    await testConnection('qbittorrent');
});

document.getElementById('test-omdb').addEventListener('click', async () => {
    await testConnection('omdb');
});

document.getElementById('test-tmdb').addEventListener('click', async () => {
    await testConnection('tmdb');
});

async function testConnection(service) {
    const statusEl = document.getElementById(`${service}-status`);
    statusEl.textContent = 'Testing...';
    statusEl.className = 'status-indicator';
    
    try {
        const response = await apiCall('/api/settings/test-connections');
        const results = await response.json();
        
        if (results[service]) {
            statusEl.textContent = 'Connected ✓';
            statusEl.classList.add('success');
        } else {
            statusEl.textContent = 'Failed ✗';
            statusEl.classList.add('error');
        }

        setTimeout(() => {
            statusEl.textContent = '';
            statusEl.className = 'status-indicator';
        }, 3000);
    } catch (error) {
        statusEl.textContent = 'Error ✗';
        statusEl.classList.add('error');
        console.error(error);
    }
}

// Logs
let currentLogLevel = '';

document.getElementById('log-level-filter').addEventListener('change', (e) => {
    currentLogLevel = e.target.value;
    // Stop stream, reload history with new filter, restart stream
    stopLogStream();
    loadLogs();
});

document.getElementById('btn-refresh-logs').addEventListener('click', () => {
    stopLogStream();
    loadLogs();
});

// Auto-scroll toggle
document.getElementById('btn-autoscroll-logs') && 
document.getElementById('btn-autoscroll-logs').addEventListener('click', (e) => {
    _logAutoScroll = !_logAutoScroll;
    e.target.textContent = _logAutoScroll ? '⬇ Auto-scroll ON' : '⬇ Auto-scroll OFF';
    e.target.style.opacity = _logAutoScroll ? '1' : '0.5';
    if (_logAutoScroll) {
        const container = document.getElementById('logs-container');
        container.scrollTop = container.scrollHeight;
    }
});

document.getElementById('btn-clear-logs').addEventListener('click', async () => {
    if (!confirm('Clear logs older than 30 days?')) return;
    
    showLoading('Clearing logs...');
    
    try {
        const response = await apiCall('/api/logs?days=30', {
            method: 'DELETE'
        });

        hideLoading();
        
        if (response.ok) {
            showToast('Logs cleared', 'success');
            await loadLogs();
        } else {
            showToast('Failed to clear logs', 'error');
        }
    } catch (error) {
        hideLoading();
        showToast('Error clearing logs', 'error');
        console.error(error);
    }
});

// ── Live log streaming via SSE ────────────────────────────────────────────
let _logEventSource = null;
let _logAutoScroll  = true;

function startLogStream() {
    if (_logEventSource) return; // already running
    const url = `${API_BASE}/api/logs/stream?token=${encodeURIComponent(authToken)}`;
    _logEventSource = new EventSource(url);

    _logEventSource.onmessage = (e) => {
        try {
            const log = JSON.parse(e.data);
            // Filter by current level selection
            if (currentLogLevel && log.level !== currentLogLevel) return;
            appendLogEntry(log);
        } catch (_) {}
    };

    _logEventSource.onerror = () => {
        // Reconnect silently after 3 s if still on logs page
        stopLogStream();
        setTimeout(() => {
            if (document.getElementById('page-logs') &&
                document.getElementById('page-logs').classList.contains('active')) {
                startLogStream();
            }
        }, 3000);
    };

    // Show live indicator
    const liveIndicator = document.getElementById('log-live-indicator');
    if (liveIndicator) {
        liveIndicator.style.display = 'inline-flex';
    }
}

function stopLogStream() {
    if (_logEventSource) {
        _logEventSource.close();
        _logEventSource = null;
    }
    const liveIndicator = document.getElementById('log-live-indicator');
    if (liveIndicator) {
        liveIndicator.style.display = 'none';
    }
}

function appendLogEntry(log) {
    const container = document.getElementById('logs-container');
    const noResults = container.querySelector('p');
    if (noResults) noResults.remove();

    const entry = document.createElement('div');
    entry.className = `log-entry ${log.level}`;
    const date = new Date(log.created_at);
    entry.innerHTML = `
        <span class="log-time">${date.toLocaleString()}</span>
        <span class="log-level ${log.level}">${log.level}</span>
        <span class="log-message">${escapeHtml(String(log.message || ''))}</span>
    `;
    container.appendChild(entry);

    // Auto-scroll to bottom if enabled
    if (_logAutoScroll) {
        container.scrollTop = container.scrollHeight;
    }

    // Cap at 500 entries to avoid memory bloat
    while (container.children.length > 500) {
        container.removeChild(container.firstChild);
    }
}

function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function loadLogs() {
    showLoading('Loading logs...');
    try {
        let endpoint = '/api/logs?limit=200';
        if (currentLogLevel) {
            endpoint += `&level=${currentLogLevel}`;
        }
        const response = await apiCall(endpoint);
        const data     = await response.json();
        hideLoading();
        displayLogs(data.logs);
        // Start live stream after the initial load
        startLogStream();
    } catch (error) {
        hideLoading();
        showToast('Error loading logs', 'error');
        console.error(error);
    }
}

function displayLogs(logs) {
    const container = document.getElementById('logs-container');
    container.innerHTML = '';

    if (!logs || logs.length === 0) {
        container.innerHTML = '<p class="no-results">No logs found</p>';
        return;
    }

    logs.forEach(log => {
        const entry = document.createElement('div');
        entry.className = `log-entry ${log.level}`;
        const date = new Date(log.created_at);
        entry.innerHTML = `
            <span class="log-time">${date.toLocaleString()}</span>
            <span class="log-level ${log.level}">${log.level}</span>
            <span class="log-message">${escapeHtml(String(log.message || ''))}</span>
        `;
        container.appendChild(entry);
    });

    // Scroll to bottom on initial load
    container.scrollTop = container.scrollHeight;
}

// Toast notifications (proper implementation replacing alert)
function showToast(message, type = 'info') {
    // Remove any existing toast
    const existing = document.getElementById('toast-notification');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'toast-notification';
    const colors = { success: '#10b981', error: '#ef4444', warning: '#f59e0b', info: '#2563eb' };
    toast.style.cssText = `
        position:fixed;bottom:2rem;right:2rem;z-index:9999;
        background:${colors[type] || colors.info};color:white;
        padding:1rem 1.5rem;border-radius:8px;
        box-shadow:0 4px 12px rgba(0,0,0,0.4);
        font-size:0.95rem;max-width:400px;
        animation:slideIn 0.2s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// Bulk Add Movies
document.addEventListener('DOMContentLoaded', () => {

    // ── Library refresh ──────────────────────────────────────────────
    const libRefreshBtn = document.getElementById('btn-refresh-library');
    if (libRefreshBtn) {
        libRefreshBtn.addEventListener('click', async () => {
            if (!confirm('This will re-fetch IMDB/TMDB data for every movie in your library. Continue?')) return;
            libRefreshBtn.disabled = true;
            libRefreshBtn.textContent = '⏳ Refreshing...';
            try {
                const res  = await apiCall('/api/movies/refresh-library', { method: 'POST' });
                const data = await res.json();
                showToast(data.message, res.ok ? 'success' : 'error');
            } catch (err) {
                showToast('Error starting library refresh', 'error');
            } finally {
                libRefreshBtn.disabled = false;
                libRefreshBtn.textContent = '↻ Refresh All Movie Data';
            }
        });
    }

    const bulkBtn = document.getElementById('btn-bulk-add');
    if (bulkBtn) {
        bulkBtn.addEventListener('click', async () => {
            const names = document.getElementById('bulk-add-input').value.trim();
            if (!names) {
                showToast('Please enter at least one movie name', 'warning');
                return;
            }
            bulkBtn.disabled = true;
            bulkBtn.textContent = 'Adding...';
            try {
                const response = await apiCall('/api/movies/bulk-add', {
                    method: 'POST',
                    body: JSON.stringify({ movie_names: names })
                });
                const data = await response.json();
                document.getElementById('bulk-add-status').textContent =
                    `✓ ${data.count} movie(s) queued`;
                document.getElementById('bulk-add-input').value = '';
                showToast(data.message, 'success');
            } catch (err) {
                showToast('Bulk add failed', 'error');
                console.error(err);
            } finally {
                bulkBtn.disabled = false;
                bulkBtn.textContent = 'Add Movies';
            }
        });
    }

    // ── Radarr root-folder browser ───────────────────────────────────
    const browseFoldersBtn = document.getElementById('btn-browse-root-folders');
    if (browseFoldersBtn) {
        browseFoldersBtn.addEventListener('click', async () => {
            const pickerEl  = document.getElementById('radarr-folder-picker');
            const inputEl   = document.getElementById('setting-radarr-root-folder');
            browseFoldersBtn.disabled = true;
            browseFoldersBtn.textContent = '...';
            pickerEl.innerHTML = '';

            try {
                const res  = await apiCall('/api/radarr/root-folders');
                if (!res.ok) {
                    const err = await res.json();
                    showToast(err.detail || 'Could not fetch Radarr folders', 'error');
                    return;
                }
                const data = await res.json();
                const folders = data.folders || [];

                if (folders.length === 0) {
                    pickerEl.innerHTML =
                        '<p style="color:var(--warning-color);font-size:0.85rem;">No root folders found in Radarr. Configure one in Radarr → Settings → Media Management first.</p>';
                    pickerEl.style.display = 'block';
                    return;
                }

                // Build a list of clickable folder buttons
                pickerEl.style.display = 'block';
                pickerEl.innerHTML = '<p style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:0.4rem;">Click a folder to select it:</p>';
                folders.forEach(folder => {
                    const btn = document.createElement('button');
                    btn.className = 'btn-secondary';
                    btn.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:0.35rem;font-size:0.88rem;padding:7px 12px;';
                    btn.textContent = folder;
                    btn.addEventListener('click', () => {
                        inputEl.value = folder;
                        pickerEl.style.display = 'none';
                        showToast(`Root folder set to: ${folder}`, 'success');
                    });
                    pickerEl.appendChild(btn);
                });

            } catch (err) {
                showToast('Error fetching Radarr folders', 'error');
                console.error(err);
            } finally {
                browseFoldersBtn.disabled = false;
                browseFoldersBtn.textContent = 'Browse';
            }
        });
    }
    const findDomainBtn = document.getElementById('btn-find-domain');
    if (findDomainBtn) {
        findDomainBtn.addEventListener('click', async () => {
            const statusEl = document.getElementById('domain-status');
            statusEl.textContent = 'Searching... (Chrome window will open)';
            statusEl.className = 'status-indicator';
            findDomainBtn.disabled = true;
            try {
                const response = await apiCall('/api/settings/find-domain', { method: 'POST' });
                const data = await response.json();
                if (data.success && data.domain) {
                    document.getElementById('setting-full-domain').value = data.domain;
                    statusEl.textContent = `✓ Found: ${data.domain}`;
                    statusEl.classList.add('success');
                    showToast(`Domain found: ${data.domain} — click "Apply Domain" to update URLs.`, 'success');
                } else {
                    statusEl.textContent = 'Not found ✗';
                    statusEl.classList.add('error');
                    showToast('Could not find domain automatically. Try editing the field manually.', 'warning');
                }
            } catch (err) {
                statusEl.textContent = 'Error ✗';
                statusEl.classList.add('error');
                showToast('Domain search error', 'error');
                console.error(err);
            } finally {
                findDomainBtn.disabled = false;
                setTimeout(() => {
                    statusEl.textContent = '';
                    statusEl.className = 'status-indicator';
                }, 6000);
            }
        });
    }

    const applyDomainBtn = document.getElementById('btn-apply-domain');
    if (applyDomainBtn) {
        applyDomainBtn.addEventListener('click', async () => {
            const domain = document.getElementById('setting-full-domain').value.trim();
            if (!domain) {
                showToast('Please enter or find a domain first', 'warning');
                return;
            }
            const statusEl = document.getElementById('domain-status');
            applyDomainBtn.disabled = true;
            try {
                const params = new URLSearchParams({ domain, token: authToken });
                const response = await fetch(`${API_BASE}/api/settings/update-domain?${params}`, {
                    method: 'POST'
                });
                const data = await response.json();
                if (data.success) {
                    // Update the preview fields
                    const forumPreview = document.getElementById('domain-forum-url-preview');
                    const searchPreview = document.getElementById('domain-search-url-preview');
                    if (forumPreview) forumPreview.value = data.forum_url;
                    if (searchPreview) searchPreview.value = data.search_url;
                    // Also update the main settings fields
                    const forumInput = document.querySelector('[data-key="forum_url"]');
                    const searchInput = document.querySelector('[data-key="search_url"]');
                    if (forumInput) forumInput.value = data.forum_url;
                    if (searchInput) searchInput.value = data.search_url;
                    statusEl.textContent = '✓ Applied';
                    statusEl.classList.add('success');
                    showToast(`Domain updated to ${domain}`, 'success');
                } else {
                    showToast('Failed to apply domain', 'error');
                }
            } catch (err) {
                showToast('Error applying domain', 'error');
                console.error(err);
            } finally {
                applyDomainBtn.disabled = false;
                setTimeout(() => {
                    const statusEl = document.getElementById('domain-status');
                    statusEl.textContent = '';
                    statusEl.className = 'status-indicator';
                }, 4000);
            }
        });
    }
});

// Start the app
window.addEventListener('DOMContentLoaded', initApp);
