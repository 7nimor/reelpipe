import json
import subprocess
import sys
from datetime import datetime
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
CONFIG_FILE = ROOT / "config.txt"
CAPTIONS_FILE = ROOT / "captions.txt"
SESSION_FILE = ROOT / "session.json"
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
DEFAULT_MAX_UPLOADS_PER_DAY = 3
DEFAULT_CAPTION = (
    "#طنز #خنده #جوک #فان #خنده_دار #میم #کمدی #طنزفارسی #خنده‌دار #شوخی"
)


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


def load_max_uploads_per_day():
    if not CONFIG_FILE.exists():
        return DEFAULT_MAX_UPLOADS_PER_DAY
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "max_uploads_per_day":
            try:
                return max(1, int(value.strip()))
            except ValueError:
                return DEFAULT_MAX_UPLOADS_PER_DAY
    return DEFAULT_MAX_UPLOADS_PER_DAY


def load_posts_data():
    if not POSTS_FILE.exists():
        return []
    return json.loads(POSTS_FILE.read_text(encoding="utf-8"))


def uploads_today(posts=None):
    today = datetime.now().date().isoformat()
    posts = posts if posts is not None else load_posts_data()
    count = 0
    for post in posts:
        if not post.get("upload"):
            continue
        stamp = post.get("uploaded_at") or ""
        if stamp[:10] == today:
            count += 1
    return count


def upload_quota():
    limit = load_max_uploads_per_day()
    used = uploads_today()
    left = max(0, limit - used)
    return {
        "limit": limit,
        "used": used,
        "left": left,
        "allowed": left > 0,
    }


def assert_upload_allowed():
    quota = upload_quota()
    if not quota["allowed"]:
        raise RuntimeError(
            f"daily upload limit reached ({quota['used']}/{quota['limit']})"
        )
    return quota


def make_client(code="", challenge_handler=None):
    client = Client()
    client.set_proxy(proxy_url())
    client.delay_range = [1, 3]

    def challenge_code_handler(username, choice):
        if challenge_handler:
            return challenge_handler(username, choice)
        if code:
            return code
        raise RuntimeError("challenge code required")

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


def load_caption():
    if not CAPTIONS_FILE.exists():
        return DEFAULT_CAPTION
    lines = []
    for line in CAPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            lines.append(line)
    if not lines:
        return DEFAULT_CAPTION
    return "\n".join(lines)


def upload_video(client, video_path):
    caption = load_caption()
    print(f"uploading: {video_path}")
    print(f"caption: {caption}")
    thumb = make_thumbnail(video_path)
    print(f"thumbnail: {thumb}")
    media = client.clip_upload(
        str(video_path),
        caption=caption,
        thumbnail=str(thumb),
    )
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
            post["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
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
        quota = assert_upload_allowed()
        print(f"quota: {quota['used']}/{quota['limit']} today")
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
        assert_upload_allowed()
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
