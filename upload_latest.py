import json
import subprocess
import sys
from pathlib import Path

from instagrapi import Client

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

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "downloads"
CREDENTIALS_FILE = ROOT / "credentials.txt"
SESSION_FILE = ROOT / "session.json"
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def shortcode_from_path(video_path):
    name = video_path.stem
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


def uploaded_shortcodes():
    if not POSTS_FILE.exists():
        return set()
    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    return {
        post.get("shortcode")
        for post in posts
        if post.get("upload") and post.get("shortcode")
    }


def find_latest_video():
    if not DOWNLOADS_DIR.exists():
        raise RuntimeError(f"downloads not found: {DOWNLOADS_DIR}")
    done = uploaded_shortcodes()
    videos = [
        path
        for path in DOWNLOADS_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTS
        and shortcode_from_path(path) not in done
    ]
    if not videos:
        raise RuntimeError("no videos in downloads")
    return max(videos, key=lambda path: path.stat().st_mtime)


def load_credentials():
    if not CREDENTIALS_FILE.exists():
        raise RuntimeError(f"credentials file not found: {CREDENTIALS_FILE}")
    data = {}
    for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    username = data.get("username", "").lstrip("@")
    password = data.get("password", "")
    code = data.get("code", "")
    if not username or not password:
        raise RuntimeError("username and password required in credentials.txt")
    return username, password, code


def make_client(code=""):
    client = Client()
    client.set_proxy(proxy_url())
    client.delay_range = [1, 3]
    if code:

        def challenge_code_handler(username, choice):
            return code

        client.challenge_code_handler = challenge_code_handler
    return client


def login(client, username, password):
    if SESSION_FILE.exists():
        try:
            client.load_settings(SESSION_FILE)
            client.login(username, password)
            client.get_timeline_feed()
            return
        except Exception:
            pass
    client.login(username, password)
    client.dump_settings(SESSION_FILE)


def make_thumbnail(video_path):
    thumb = Path(str(video_path) + ".jpg")
    if thumb.exists():
        return thumb
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-ss",
            "0",
            "-vframes",
            "1",
            str(thumb),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return thumb


def upload_video(client, video_path):
    print(f"uploading: {video_path}")
    thumb = make_thumbnail(video_path)
    print(f"thumbnail: {thumb}")
    media = client.clip_upload(str(video_path), caption="", thumbnail=str(thumb))
    return media


def mark_uploaded(video_path):
    shortcode = shortcode_from_path(video_path)
    if not POSTS_FILE.exists():
        raise RuntimeError(f"posts file not found: {POSTS_FILE}")
    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    found = False
    for post in posts:
        if post.get("shortcode") == shortcode:
            post["upload"] = True
            found = True
            break
    if not found:
        raise RuntimeError(f"post not found for shortcode: {shortcode}")
    POSTS_FILE.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return shortcode


def cleanup_video(video_path):
    thumb = Path(str(video_path) + ".jpg")
    for path in (video_path, thumb):
        if path.exists():
            path.unlink()
            print(f"deleted: {path}")


def main():
    if not MIHOMO.exists():
        print(f"Error: mihomo not found at {MIHOMO}", file=sys.stderr)
        sys.exit(1)

    if not FLCLASH_CONFIG.exists():
        print(f"Error: FlClash config not found", file=sys.stderr)
        sys.exit(1)

    proc = None
    try:
        video_path = find_latest_video()
        print(f"latest: {video_path}")
        username, password, code = load_credentials()
        nodes = load_nodes()
        print(f"loaded {len(nodes)} nodes from FlClash")
        proc, names = start_mihomo(nodes)
        working = find_working_node(names)
        print(f"using: {working}")
        client = make_client(code)
        login(client, username, password)
        media = upload_video(client, video_path)
        media_code = getattr(media, "code", None) or getattr(media, "pk", None)
        print(f"ok: {media_code}")
        shortcode = mark_uploaded(video_path)
        print(f"marked: {shortcode}")
        cleanup_video(video_path)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    finally:
        stop_mihomo(proc)


if __name__ == "__main__":
    main()
