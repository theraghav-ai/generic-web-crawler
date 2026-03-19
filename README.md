# Generic Web Crawler

A production-ready headless web crawler that renders JavaScript-heavy websites with Playwright and exports clean Markdown snapshots. Designed for crawling modern SPAs, dynamic content, and sites behind WAFs.

## Features

- **Headless Chromium** via Playwright with stealth anti-detection
- **BFS crawl** with configurable depth and page limits
- **robots.txt** compliance (with optional bypass via `--ignore-robots`)
- **Sitemap discovery** from `robots.txt` and `/sitemap.xml`
- **Rate limiting** with jitter and retry with exponential backoff
- **Content hashing** to skip unchanged pages across runs
- **Checkpoint & resume** — interrupted crawls pick up where they left off
- **Incremental cache** — subsequent runs rebuild full snapshots without re-crawling
- **Dynamic content expansion** — clicks tabs, accordions, modals; scrolls for lazy-loaded content
- **Proxy rotation** and **cookie seeding** support
- **WAF detection** (Cloudflare, etc.) with automatic fallback
- **Structured data extraction** — JSON-LD, Open Graph, FAQ schema
- **Markdown export** — consolidated snapshot with table of contents

## Requirements

- Python 3.10+
- A Chromium-compatible OS (Linux, macOS, Windows)

## Quick Start

### Option 1: Local Setup

```bash
# Clone the repository
git clone https://github.com/<your-username>/generic-web-crawler.git
cd generic-web-crawler

# Install dependencies and Playwright Chromium
./manage.sh setup

# Crawl a website
./manage.sh crawl https://www.python.org/ --max-pages 20 --max-depth 2
```

### Option 2: Docker

```bash
# Clone the repository
git clone https://github.com/<your-username>/generic-web-crawler.git
cd generic-web-crawler

# Build and run
docker compose run --rm crawler crawl https://www.python.org/ --max-pages 20 --max-depth 2
```

### Option 3: Python CLI

```bash
# After setup
python main.py crawl https://www.python.org/ --max-pages 20 --max-depth 2
```

## Usage

### Crawl a Site

```bash
./manage.sh crawl <url> [options]
```

| Option | Description |
|---|---|
| `--max-pages N` | Maximum number of pages to crawl (default: 100) |
| `--max-depth N` | Maximum link depth from seed (default: 2) |
| `--delay SECONDS` | Delay between requests in seconds (default: 1.0) |
| `--timeout SECONDS` | Request timeout in seconds (default: 30) |
| `--wait-ms MS` | Page load wait in milliseconds (default: 2000) |
| `--min-text-length N` | Minimum text length to consider a page valid (default: 80) |
| `--seed-url URL` | Additional seed URL (repeatable) |
| `--allowed-domain DOMAIN` | Additional allowed domain (repeatable) |
| `--allowed-prefix PATH` | Allowed URL path prefix (repeatable) |
| `--blocked-pattern PAT` | URL pattern to block (repeatable) |
| `--no-sitemap` | Disable sitemap discovery |
| `--no-dynamic-expand` | Disable dynamic content expansion |
| `--ignore-robots` | Ignore robots.txt restrictions |
| `--no-path-filter` | Crawl entire domain regardless of base URL path |
| `--proxy URL` | Proxy server URL (repeatable for rotation) |
| `--cookie-file PATH` | Path to JSON cookie file for session seeding |
| `--save-cookies PATH` | Save session cookies after crawl |

### Check Status

```bash
./manage.sh status <url>
```

### View Logs

```bash
./manage.sh logs
```

## Examples

```bash
# Basic crawl
./manage.sh crawl https://docs.python.org/3/ --max-pages 50

# Broad domain crawl ignoring path restrictions
./manage.sh crawl https://example.com/blog/ --no-path-filter --max-pages 200

# Crawl behind a proxy with robots.txt bypass
./manage.sh crawl https://example.com/ --proxy http://proxy:8080 --ignore-robots

# Crawl with cookie authentication
./manage.sh crawl https://example.com/ --cookie-file cookies.json --save-cookies session.json

# Fast shallow crawl
./manage.sh crawl https://example.com/ --max-depth 1 --max-pages 10 --delay 0.5
```

## Output Structure

After a crawl, outputs are organized per site:

```
data/
├── snapshots/
│   └── example-com-2026-03-19.md      # Consolidated markdown snapshot
└── sites/
    └── example-com/
        ├── latest_pages.json           # Full page content (JSON)
        ├── crawl_state.json            # Page state tracking (hash, etag)
        ├── page_content_cache.json     # Incremental content cache
        ├── checkpoint.json             # Resume data for interrupted crawls
        └── last_run.json               # Run metadata summary
```

The markdown snapshot contains a table of contents, per-page content with URLs, titles, and any extracted structured data (JSON-LD, Open Graph, FAQs).

## Configuration

Configuration is resolved in order: **CLI arguments > environment variables > defaults**.

Copy the example environment file to get started:

```bash
cp .env.example .env
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CRAWL_BASE_URL` | *(required)* | Target website URL |
| `CRAWL_MAX_DEPTH` | `2` | Maximum crawl depth |
| `CRAWL_MAX_PAGES` | `100` | Maximum pages to crawl |
| `CRAWL_REQUEST_DELAY` | `1.0` | Seconds between requests |
| `CRAWL_REQUEST_TIMEOUT` | `30` | Request timeout in seconds |
| `CRAWL_PAGE_LOAD_WAIT_MS` | `2000` | Wait for JS rendering (ms) |
| `CRAWL_MIN_TEXT_LENGTH` | `80` | Minimum text to accept a page |
| `CRAWL_USE_SITEMAP` | `true` | Discover URLs from sitemaps |
| `CRAWL_EXPAND_DYNAMIC` | `true` | Expand tabs, accordions, modals |
| `CRAWL_IGNORE_ROBOTS` | `false` | Bypass robots.txt |
| `CRAWL_NO_PATH_FILTER` | `false` | Crawl entire domain |
| `CRAWL_ALLOWED_DOMAINS` | *(from URL)* | Comma-separated allowed domains |
| `CRAWL_ALLOWED_PATH_PREFIXES` | *(from URL)* | Comma-separated path prefixes |
| `SEED_URLS` | *(from URL)* | Comma-separated seed URLs |
| `CRAWL_PROXY_LIST` | | Comma-separated proxy URLs |
| `CRAWL_COOKIE_FILE` | | Path to cookie JSON file |
| `CRAWL_CHECKPOINT_INTERVAL` | `10` | Pages between checkpoints |
| `LOG_LEVEL` | `INFO` | Logging level |

## Architecture

```
main.py              CLI entry point and pipeline orchestration
config.py            Configuration loading and validation
crawler/
  spider.py          Core crawl engine (Playwright + fallback HTTP)
  url_manager.py     BFS queue, URL filtering, deduplication
  robots_handler.py  robots.txt parsing and compliance
  sitemap_parser.py  Sitemap discovery and URL extraction
  state_manager.py   Crawl state, checkpoints, content cache
  structured_data.py JSON-LD, Open Graph, FAQ extraction
converter/
  html_to_markdown.py  HTML cleanup and Markdown export
```

## How It Works

1. **Seed** — The crawler starts from the base URL (and any sitemap URLs if enabled)
2. **Fetch** — Each page is rendered in headless Chromium with stealth scripts; falls back to plain HTTP if blocked
3. **Extract** — HTML content, links, and structured data are extracted; dynamic elements (tabs, accordions) are expanded
4. **Filter** — URLs are deduplicated, domain-checked, and filtered by path prefix and blocked patterns
5. **Queue** — New URLs are added to the BFS queue up to the configured depth
6. **Cache** — Content hashes track changes; unchanged pages are skipped on re-crawl
7. **Checkpoint** — State is saved periodically so interrupted crawls can resume
8. **Export** — All cached pages are merged into a consolidated Markdown snapshot

## Docker

### Build

```bash
docker compose build
```

### Run a Crawl

```bash
docker compose run --rm crawler crawl https://www.python.org/ --max-pages 20
```

### Use Environment Variables

```bash
cp .env.example .env
# Edit .env with your settings
docker compose run --rm crawler crawl
```

Crawl data is persisted in a Docker volume mapped to `./data/`.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.