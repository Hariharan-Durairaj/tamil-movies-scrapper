# Forum HTML Formats & Scraper Fallbacks

This note documents the different HTML structures the scraper encounters on
1TamilMV-style (IPS / Invision Community) forums, and the fallback logic that
was added to handle them. All scraping lives in `backend/scraper.py`.

## Torrent link formats

Across the forum there are **three distinct ways** a movie post exposes its
download, and older posts do not always use the current format.

### Format 1 — `data-fileext="torrent"` (standard / current — works)

The current posts attach the `.torrent` file with an explicit
`data-fileext="torrent"` attribute:

```html
<a data-fileext="torrent" data-fileid="145373"
   href="https://www.1tamilmv.cards/applications/core/interface/file/attachment.php?id=145373&key=...">
   <span>[www.1TamilMV.buzz] - Bairavan (2025) Tamil HDRip - 720p - x264 - (AAC 2.0) - 1.2GB.mkv.torrent</span>
</a>
```

Parsed by `_parse_fileext_torrents()`. The `href` is the direct
`.torrent` download URL; the visible text gives the name, from which quality /
codec / rip type / size are parsed.

### Format 2 — magnet link (fallback — newly handled)

Some posts only have (or only successfully expose) a magnet link. The torrent
file is unavailable, but the `dn` (display name) parameter of the magnet
carries the full name including year, quality, codec, rip type and size:

```html
<a class="skyblue-button"
   href="magnet:?xt=urn:btih:ffad91...&dn=www.1TamilMV.buzz%20-%20Bairavan%20%282025%29%C2%A0Tamil%20HDRip%20-%20720p%20-%20x264%20-%20%28AAC%202.0%29%20-%201.2GB.mkv&tr=...">
   MAGNET
</a>
```

Parsed by `_parse_magnet_torrents()`:

* the `dn` is URL-decoded, the `www.<site>.<tld> -` prefix and the trailing
  `.mkv/.mp4/.avi` extension are stripped (`_clean_magnet_name()`),
* `parse_torrent_name()` extracts quality / codec / rip / size,
* `parse_movie_title_year()` extracts the title and year,
* the `xt=urn:btih:` hash is kept as the file id,
* the entry is flagged `is_magnet=True` and the magnet URL is stored as the
  actionable `torrent_url`.

Magnets are **not** downloadable files, so they are handed straight to
qBittorrent via `add_torrent_url()` instead of being downloaded with
`download_torrent()` (which now short-circuits on `magnet:` URLs).

### Format 3 — `ipsAttachLink` (older posts, fallback — newly handled)

Older posts render the attachment with `class="ipsAttachLink"` and a
`data-fileid`, but **without** `data-fileext`, so Format 1 misses them. A real
example (note the link's own text is only the quality *tail* — the title/year
live in the `<span>` on the line **before** the link):

```html
<span style="color:#000000;">ASURAGURU (2020) Tamil TRUE WEB-DL - 1080p - AVC - UNTOUCHED - (DD5.1 - 512Kbps) - 8.6GB - ESub :</span><br>
<a class="ipsAttachLink" data-fileid="48038"
   href="https://www.1tamilmv.cards/applications/core/interface/file/attachment.php?id=48038">
   <span>1080p - AVC - UNTOUCHED - (DD5.1 - 512Kbps) - 8.6GB - ESub.mp4.torrent</span>
</a>
```

Parsed by `_parse_ipsattachlink_torrents()`: it matches `a.ipsAttachLink` /
`a[data-fileid]` links whose `href` points at `attachment.php` (or `/file/`)
and are not already handled by Format 1. Because the link's own text lacks the
title/year, `_descriptive_name_before()` walks back to the nearest preceding
text containing a `(YYYY)` and uses that full line as the name (quality is
parsed from both the descriptive line and the link tail combined). The `href`
is a genuine downloadable `.torrent` file.

> **Note on ordering:** the example post above carries *both* a magnet and an
> `ipsAttachLink` torrent file. Per the configured order (below), the **magnet
> wins**, so such posts are stored as magnet links rather than downloading the
> attachment file. `ipsAttachLink` is only used when a post has no magnet.

## Fallback chain

`extract_all_torrents(url)` fetches the post **once** and tries the formats in
order, first hit wins:

```
1. data-fileext="torrent"   →  if none:
2. magnet links             →  if none:
3. ipsAttachLink
```

`get_movie_torrents()` in `backend/movie_processor.py` calls this and logs
which format succeeded (`source_format`), or a warning if none matched.

## Last-page / pagination detection

The total page count is read from the IPS pagination block. The last page
number appears in several places in the markup, e.g.:

```html
<ul class='ipsPagination' data-pages='97'
    data-ipsPagination-pages='97' data-ipsPagination-perPage='25'>
  <li class='ipsPagination_pageJump'>
    <a ...>Page 1 of 97 <i class='fa fa-caret-down'></i></a>
    <form ... data-role="pageJump">
      <input type='number' min='1' max='97' name='page'>
    </form>
  </li>
</ul>
```

`get_total_pages()` reads them in this priority order:

1. `data-pages` attribute,
2. `data-ipsPagination-pages` (BeautifulSoup lowercases it to
   `data-ipspagination-pages`),
3. the page-jump form's `<input max="97">` attribute,
4. **(new fallback)** the visible `Page 1 of 97` jump-label text.

If none are present the forum is treated as a single page.
