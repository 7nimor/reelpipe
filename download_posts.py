import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from curl_cffi import requests

from latest_post import (
    FLCLASH_CONFIG,
    MIHOMO,
    POSTS_FILE,
    find_working_node,
    load_nodes,
    proxy_url,
    start_mihomo,
    stop_mihomo,
)

DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"


def load_posts():
    if not POSTS_FILE.exists():
        raise RuntimeError(f"posts file not found: {POSTS_FILE}")
    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    if not posts:
        raise RuntimeError("no posts in posts.json")
    return posts


def unescape_url(value):
    return (
        value.encode("utf-8")
        .decode("unicode_escape")
        .encode("latin1")
        .decode("utf-8", errors="ignore")
    )


def guess_ext(media_url, kind):
    path = urlparse(media_url).path.lower()
    for ext in (".mp4", ".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".mp4" if kind == "video" else ".jpg"


def extract_media(page):
    media = []
    seen = set()

    def add(kind, url):
        url = html.unescape(url.replace("\\u0026", "&").replace("\\/", "/"))
        url = unquote(url)
        if "\\u" in url or "\\x" in url:
            try:
                url = unescape_url(url)
            except Exception:
                pass
        if not url.startswith("http") or url in seen:
            return
        seen.add(url)
        media.append((kind, url))

    for url in re.findall(r'<meta property="og:video" content="([^"]+)"', page):
        add("video", url)

    for url in re.findall(r'"video_url"\s*:\s*"([^"]+)"', page):
        add("video", url)

    for url in re.findall(
        r'"video_versions"\s*:\s*\[\s*\{[^}]*?"url"\s*:\s*"([^"]+)"',
        page,
    ):
        add("video", url)

    if not media:
        for url in re.findall(r'<meta property="og:image" content="([^"]+)"', page):
            add("image", url)

        for url in re.findall(r'"display_url"\s*:\s*"([^"]+)"', page):
            add("image", url)

    if not media:
        raise RuntimeError("no media found")
    return media


def fetch_post_html(url):
    response = requests.get(
        url,
        proxies={"http": proxy_url(), "https": proxy_url()},
        impersonate="chrome131",
        timeout=40,
    )
    response.raise_for_status()
    return response.text


def download_file(media_url, dest):
    response = requests.get(
        media_url,
        proxies={"http": proxy_url(), "https": proxy_url()},
        impersonate="chrome131",
        timeout=120,
    )
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def download_post(post):
    username = post["username"]
    shortcode = post["shortcode"]
    url = post["url"]
    if post.get("upload"):
        print(f"skip (uploaded): {username}/{shortcode}")
        return []
    out_dir = DOWNLOADS_DIR / username
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = [
        path
        for path in out_dir.glob(f"{shortcode}*")
        if path.suffix.lower() in {".mp4", ".mov", ".m4v"}
    ]
    if existing:
        print(f"skip (exists): {username}/{shortcode}")
        return existing

    print(f"fetching: {url}")
    page = fetch_post_html(url)
    media = [(kind, media_url) for kind, media_url in extract_media(page) if kind == "video"]
    if not media:
        print(f"skip (not video): {username}/{shortcode}")
        return []
    saved = []
    for index, (kind, media_url) in enumerate(media):
        ext = guess_ext(media_url, kind)
        if len(media) == 1:
            dest = out_dir / f"{shortcode}{ext}"
        else:
            dest = out_dir / f"{shortcode}_{index + 1}{ext}"
        print(f"downloading: {dest.name}")
        download_file(media_url, dest)
        saved.append(dest)
    return saved


def download_all_posts():
    posts = load_posts()
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    for post in posts:
        try:
            saved = download_post(post)
            for path in saved:
                print(path)
        except Exception as error:
            print(
                f"fail: {post.get('username')}/{post.get('shortcode')} ({error})",
                file=sys.stderr,
            )


def main():
    if not MIHOMO.exists():
        print(f"Error: mihomo not found at {MIHOMO}", file=sys.stderr)
        sys.exit(1)

    if not FLCLASH_CONFIG.exists():
        print(f"Error: FlClash config not found", file=sys.stderr)
        sys.exit(1)

    proc = None
    try:
        nodes = load_nodes()
        print(f"loaded {len(nodes)} nodes from FlClash")
        proc, names = start_mihomo(nodes)
        working = find_working_node(names)
        print(f"using: {working}")
        download_all_posts()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    finally:
        stop_mihomo(proc)


if __name__ == "__main__":
    main()
