import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg
from instagrapi import Client

from latest_post import (
    FLCLASH_CONFIG,
    MIHOMO,
    POSTS_FILE,
    find_working_node,
    load_nodes,
    proxy_url,
    read_posts,
    start_mihomo,
    stop_mihomo,
    write_posts,
)

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "downloads"
CREDENTIALS_FILE = ROOT / "credentials.txt"
CONFIG_FILE = ROOT / "config.txt"
CAPTIONS_FILE = ROOT / "captions.txt"
SESSION_FILE = ROOT / "session.json"
SESSION_ACCOUNT_FILE = ROOT / "session.account"
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
DEFAULT_MAX_UPLOADS_PER_DAY = 3
DEFAULT_CAPTION = (
    "#طنز #خنده #جوک #فان #خنده_دار #میم #کمدی #طنزفارسی #خنده‌دار #شوخی "
    "#ocean #اقیانوس #دریا"
)


def shortcode_from_path(video_path):
    name = video_path.stem
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name


def uploaded_shortcodes():
    return {
        post.get("shortcode")
        for post in read_posts()
        if post.get("upload") and post.get("shortcode")
    }


def find_pending_videos():
    if not DOWNLOADS_DIR.exists():
        return []
    done = uploaded_shortcodes()
    videos = [
        path
        for path in DOWNLOADS_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTS
        and shortcode_from_path(path) not in done
    ]
    return sorted(videos, key=lambda path: path.stat().st_mtime)


def find_latest_video():
    videos = find_pending_videos()
    if not videos:
        raise RuntimeError("no videos in downloads")
    return videos[-1]


def read_credentials_map():
    data = {}
    if not CREDENTIALS_FILE.exists():
        return data
    for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def write_credentials_map(data):
    order = ["username", "password", "code", "sessionid"]
    lines = []
    seen = set()
    for key in order:
        if key in data:
            lines.append(f"{key}:{data.get(key, '')}")
            seen.add(key)
    for key, value in data.items():
        if key not in seen:
            lines.append(f"{key}:{value}")
    CREDENTIALS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_credentials():
    data = read_credentials_map()
    if not data:
        raise RuntimeError(f"credentials file not found: {CREDENTIALS_FILE}")
    username = data.get("username", "").lstrip("@")
    password = data.get("password", "")
    code = data.get("code", "")
    sessionid = data.get("sessionid", "")
    if not username:
        raise RuntimeError("username required in credentials.txt")
    if not password and not sessionid:
        raise RuntimeError("password or sessionid required in credentials.txt")
    return username, password, code, sessionid


def save_sessionid(sessionid):
    sessionid = (sessionid or "").strip().strip('"').strip("'")
    if not sessionid:
        raise RuntimeError("sessionid empty")
    data = read_credentials_map()
    data["sessionid"] = sessionid
    if "username" not in data:
        data["username"] = ""
    if "password" not in data:
        data["password"] = ""
    if "code" not in data:
        data["code"] = ""
    write_credentials_map(data)
    clear_session()
    return True


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


def uploads_today(posts=None):
    today = datetime.now().date().isoformat()
    posts = posts if posts is not None else read_posts()
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
    client.set_app()
    client.set_country("US")
    client.set_locale("en_US")
    client.set_timezone_offset(-18000)

    def challenge_code_handler(username, choice):
        if challenge_handler:
            return challenge_handler(username, choice)
        if code:
            return code
        raise RuntimeError("challenge code required")

    client.challenge_code_handler = challenge_code_handler
    return client


def clear_session():
    SESSION_FILE.unlink(missing_ok=True)
    SESSION_ACCOUNT_FILE.unlink(missing_ok=True)


def save_session(client, username):
    client.dump_settings(SESSION_FILE)
    SESSION_ACCOUNT_FILE.write_text(username + "\n", encoding="utf-8")


def ensure_account(client, username):
    if client.username and client.username.lstrip("@") != username:
        clear_session()
        raise RuntimeError(
            f"logged in as @{client.username}, expected @{username}"
        )


def login(client, username, password, sessionid=""):
    saved = ""
    if SESSION_ACCOUNT_FILE.exists():
        saved = SESSION_ACCOUNT_FILE.read_text(encoding="utf-8").strip().lstrip("@")
    if SESSION_FILE.exists() and saved == username:
        try:
            client.load_settings(SESSION_FILE, override_app_version=True)
            client.set_app()
            if sessionid:
                client.login_by_sessionid(sessionid)
            elif password:
                client.login(username, password)
            else:
                client.get_timeline_feed()
            ensure_account(client, username)
            save_session(client, username)
            print(f"logged in: @{client.username or username}")
            return
        except Exception as error:
            print(f"session reuse failed: {error}")
            clear_session()
    else:
        if SESSION_FILE.exists() and saved != username:
            print(f"session reset: {saved or 'unknown'} -> {username}")
        clear_session()
    try:
        if sessionid:
            print("login via sessionid")
            client.login_by_sessionid(sessionid)
        else:
            client.login(username, password)
    except Exception as error:
        message = str(error)
        if "out of date" in message.lower() or "needs_upgrade" in message.lower():
            raise RuntimeError(
                "لاگین با پسورد توسط اینستاگرام بسته شد. "
                "sessionid مرورگر را در UI یا credentials.txt بگذار."
            ) from error
        raise
    ensure_account(client, username)
    save_session(client, username)
    print(f"logged in: @{client.username or username}")


def resolve_ffmpeg():
    local = ROOT / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.exists():
        return str(local)
    return imageio_ffmpeg.get_ffmpeg_exe()


def make_thumbnail(video_path):
    thumb = Path(str(video_path) + ".jpg")
    if thumb.exists():
        return thumb
    subprocess.check_call(
        [
            resolve_ffmpeg(),
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
    media_code = getattr(media, "code", None)
    if media_code:
        print(f"reel: https://www.instagram.com/reel/{media_code}/")
    return media


def mark_uploaded(video_path):
    shortcode = shortcode_from_path(video_path)
    posts = read_posts()
    found = False
    for post in posts:
        if post.get("shortcode") == shortcode:
            post["upload"] = True
            post["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if not found:
        raise RuntimeError(f"post not found for shortcode: {shortcode}")
    write_posts(posts)
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
        username, password, code, sessionid = load_credentials()
        nodes = load_nodes()
        print(f"loaded {len(nodes)} nodes from FlClash")
        proc, names = start_mihomo(nodes)
        working = find_working_node(names)
        print(f"using: {working}")
        client = make_client(code)
        login(client, username, password, sessionid=sessionid)
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
