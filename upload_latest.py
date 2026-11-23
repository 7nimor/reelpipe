import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
DEFAULT_UPLOAD_GAP_MINUTES = 90
DEFAULT_MAX_HASHTAGS = 5
DEFAULT_STORY_BOOST = True
DEFAULT_STORY_DELAY_SECONDS = 45
DEFAULT_FIRST_COMMENT = True
DEFAULT_CROP_WATERMARK = True
DEFAULT_WAIT_FOR_PEAK = True
DEFAULT_PEAK_HOURS = (18, 23)
DEFAULT_TIMEZONE = "Asia/Tehran"
DEFAULT_THUMB_AT_SECONDS = 1
DEFAULT_HOOKS = (
    "اینو تا آخر ببین 😂",
    "یکی بگه این عادیه؟",
    "سوتی روز 💀",
)
DEFAULT_CTAS = (
    "اینو برای دوستت بفرست",
    "سیوش کن بعدا دوباره ببین",
    "تو کامنت بگو چند بار دیدی",
)
DEFAULT_KEYWORDS = ("طنز فارسی", "میم", "خنده دار")
DEFAULT_TAGS = (
    "#طنز",
    "#خنده",
    "#جوک",
    "#فان",
    "#میم",
    "#کمدی",
    "#طنزفارسی",
)


def shortcode_from_path(video_path):
    name = Path(video_path).stem
    if name.endswith("_ready"):
        name = name[: -len("_ready")]
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
        and not path.stem.endswith("_ready")
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


def read_config_map():
    data = {}
    if not CONFIG_FILE.exists():
        return data
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def config_int(key, default, minimum=None):
    raw = read_config_map().get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None:
        return max(minimum, value)
    return value


def config_bool(key, default):
    raw = read_config_map().get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_max_uploads_per_day():
    return config_int("max_uploads_per_day", DEFAULT_MAX_UPLOADS_PER_DAY, minimum=1)


def load_upload_gap_minutes():
    return config_int("upload_gap_minutes", DEFAULT_UPLOAD_GAP_MINUTES, minimum=0)


def load_max_hashtags():
    return config_int("max_hashtags", DEFAULT_MAX_HASHTAGS, minimum=1)


def load_story_boost():
    return config_bool("story_boost", DEFAULT_STORY_BOOST)


def load_story_delay_seconds():
    return config_int("story_delay_seconds", DEFAULT_STORY_DELAY_SECONDS, minimum=0)


def load_first_comment():
    return config_bool("first_comment", DEFAULT_FIRST_COMMENT)


def load_crop_watermark():
    return config_bool("crop_watermark", DEFAULT_CROP_WATERMARK)


def load_wait_for_peak():
    return config_bool("wait_for_peak", DEFAULT_WAIT_FOR_PEAK)


def load_thumb_at_seconds():
    return config_int("thumb_at_seconds", DEFAULT_THUMB_AT_SECONDS, minimum=0)


def load_timezone_name():
    return read_config_map().get("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE


def load_peak_hours():
    raw = read_config_map().get("peak_hours", "")
    if not raw or "-" not in raw:
        return DEFAULT_PEAK_HOURS
    left, right = raw.split("-", 1)
    try:
        start = max(0, min(23, int(left.strip())))
        end = max(0, min(23, int(right.strip())))
    except ValueError:
        return DEFAULT_PEAK_HOURS
    if start > end:
        return DEFAULT_PEAK_HOURS
    return start, end


def now_in_timezone():
    name = load_timezone_name()
    if name.lower() == "local":
        return datetime.now().astimezone()
    try:
        return datetime.now(ZoneInfo(name))
    except Exception:
        return datetime.now(timezone(timedelta(hours=3, minutes=30)))


def uploads_today(posts=None):
    today = now_in_timezone().date().isoformat()
    posts = posts if posts is not None else read_posts()
    count = 0
    for post in posts:
        if not post.get("upload"):
            continue
        stamp = post.get("uploaded_at") or ""
        if stamp[:10] == today:
            count += 1
    return count


def last_uploaded_at(posts=None):
    posts = posts if posts is not None else read_posts()
    latest = None
    for post in posts:
        if not post.get("upload"):
            continue
        stamp = post.get("uploaded_at") or ""
        if not stamp:
            continue
        try:
            value = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if latest is None or value > latest:
            latest = value
    return latest


def wait_upload_gap():
    gap = load_upload_gap_minutes()
    if gap <= 0:
        return 0
    last = last_uploaded_at()
    if last is None:
        return 0
    elapsed = (datetime.now() - last).total_seconds()
    need = int(gap * 60 - elapsed)
    if need <= 0:
        return 0
    print(f"gap: waiting {need}s ({gap}m)")
    slept = 0
    while slept < need:
        chunk = min(60, need - slept)
        time.sleep(chunk)
        slept += chunk
    return need


def wait_for_peak_window():
    if not load_wait_for_peak():
        return 0
    start_h, end_h = load_peak_hours()
    total = 0
    while True:
        now = now_in_timezone()
        if start_h <= now.hour <= end_h:
            print(f"peak: in window {start_h}-{end_h} ({load_timezone_name()})")
            return total
        target = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
        if now.hour > end_h:
            target = target + timedelta(days=1)
        need = max(1, int((target - now).total_seconds()))
        print(f"peak: waiting {need}s until {start_h}:00 ({load_timezone_name()})")
        chunk = min(60, need)
        time.sleep(chunk)
        total += chunk


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


def prepare_video(video_path):
    video_path = Path(video_path)
    if not load_crop_watermark():
        return video_path
    out = video_path.with_name(video_path.stem + "_ready" + video_path.suffix)
    if out.exists() and out.stat().st_mtime >= video_path.stat().st_mtime:
        print(f"crop: reuse {out.name}")
        return out
    print(f"crop: watermark strip -> {out.name}")
    subprocess.check_call(
        [
            resolve_ffmpeg(),
            "-y",
            "-i",
            str(video_path),
            "-vf",
            "crop=iw:ih*0.92:0:0",
            "-c:a",
            "copy",
            str(out),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def make_thumbnail(video_path):
    thumb = Path(str(video_path) + ".jpg")
    if thumb.exists():
        return thumb
    at = load_thumb_at_seconds()
    subprocess.check_call(
        [
            resolve_ffmpeg(),
            "-y",
            "-i",
            str(video_path),
            "-ss",
            str(at),
            "-vframes",
            "1",
            str(thumb),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return thumb


def normalize_tag(token):
    token = token.strip()
    if not token or token.startswith("//"):
        return ""
    if not token.startswith("#"):
        token = "#" + token.lstrip("#")
    return token.replace(" ", "_")


def parse_caption_file():
    hooks = []
    ctas = []
    keywords = []
    tags = []
    if not CAPTIONS_FILE.exists():
        return (
            list(DEFAULT_HOOKS),
            list(DEFAULT_CTAS),
            list(DEFAULT_KEYWORDS),
            list(DEFAULT_TAGS),
        )
    for line in CAPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith(">"):
            text = line[1:].strip()
            if text:
                ctas.append(text)
            continue
        if line.startswith("~"):
            text = line[1:].strip()
            if text and text not in keywords:
                keywords.append(text)
            continue
        parts = line.split()
        only_tags = all(part.startswith("#") for part in parts)
        if only_tags:
            for part in parts:
                tag = normalize_tag(part)
                if tag and tag not in tags:
                    tags.append(tag)
            continue
        if line.startswith("#") and " " not in line:
            tag = normalize_tag(line)
            if tag and tag not in tags:
                tags.append(tag)
            continue
        hooks.append(line)
    if not hooks:
        hooks = list(DEFAULT_HOOKS)
    if not ctas:
        ctas = list(DEFAULT_CTAS)
    if not keywords:
        keywords = list(DEFAULT_KEYWORDS)
    if not tags:
        tags = list(DEFAULT_TAGS)
    return hooks, ctas, keywords, tags


def load_caption():
    hooks, ctas, keywords, tags = parse_caption_file()
    hook = random.choice(hooks)
    cta = random.choice(ctas)
    kw = random.choice(keywords) if keywords else ""
    limit = min(load_max_hashtags(), len(tags))
    picked = random.sample(tags, limit) if limit else []
    blocks = [hook, cta]
    if kw:
        blocks.append(kw)
    if picked:
        blocks.append(" ".join(picked))
    return "\n\n".join(blocks)


def media_id_of(media):
    media_id = getattr(media, "id", None)
    if media_id:
        return str(media_id)
    pk = getattr(media, "pk", None)
    if pk:
        return str(pk)
    return ""


def seed_first_comment(client, media):
    if not load_first_comment():
        print("comment: skipped")
        return None
    media_id = media_id_of(media)
    if not media_id:
        print("comment skip: no media id")
        return None
    _, ctas, _, _ = parse_caption_file()
    text = random.choice(ctas)
    print(f"comment: {text}")
    comment = client.media_comment(media_id, text)
    comment_pk = getattr(comment, "pk", None)
    if comment_pk:
        try:
            client.comment_pin(media_id, int(comment_pk))
            print(f"comment pinned: {comment_pk}")
        except Exception as error:
            print(f"comment pin skip: {error}")
    return comment


def boost_to_story(client, media):
    if not load_story_boost():
        print("story: skipped")
        return None
    delay = load_story_delay_seconds()
    if delay > 0:
        print(f"story: delay {delay}s")
        time.sleep(delay)
    media_id = media_id_of(media)
    if not media_id:
        print("story skip: no media id")
        return None
    print(f"story: sharing {media_id}")
    story = client.media_share_to_story(media_id)
    story_pk = getattr(story, "pk", None) or getattr(story, "id", None)
    if story_pk:
        print(f"story ok: {story_pk}")
    return story


def upload_video(client, video_path):
    ready = prepare_video(video_path)
    caption = load_caption()
    print(f"uploading: {ready}")
    print(f"caption: {caption}")
    thumb = make_thumbnail(ready)
    print(f"thumbnail: {thumb}")
    media = client.clip_upload(
        str(ready),
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
            post["uploaded_at"] = now_in_timezone().replace(tzinfo=None).isoformat(
                timespec="seconds"
            )
            found = True
            break
    if not found:
        raise RuntimeError(f"post not found for shortcode: {shortcode}")
    write_posts(posts)
    return shortcode


def cleanup_video(video_path):
    video_path = Path(video_path)
    ready = video_path.with_name(video_path.stem + "_ready" + video_path.suffix)
    paths = [
        video_path,
        ready,
        Path(str(video_path) + ".jpg"),
        Path(str(ready) + ".jpg"),
    ]
    for path in paths:
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
        wait_upload_gap()
        assert_upload_allowed()
        media = upload_video(client, video_path)
        media_code = getattr(media, "code", None) or getattr(media, "pk", None)
        print(f"ok: {media_code}")
        shortcode = mark_uploaded(video_path)
        print(f"marked: {shortcode}")
        seed_first_comment(client, media)
        boost_to_story(client, media)
        cleanup_video(video_path)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    finally:
        stop_mihomo(proc)


if __name__ == "__main__":
    main()
