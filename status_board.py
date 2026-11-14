from latest_post import PAGES_FILE, load_pages, read_posts
from upload_latest import read_credentials_map, upload_quota


def load_account():
    data = read_credentials_map()
    return data.get("username", "").lstrip("@")


def has_sessionid():
    data = read_credentials_map()
    return bool(data.get("sessionid", "").strip())


def board():
    pages = []
    try:
        pages = load_pages()
    except Exception:
        if PAGES_FILE.exists():
            pages = [
                line.strip().lstrip("@")
                for line in PAGES_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
    posts = read_posts()
    pending = []
    uploaded = []
    for post in posts:
        item = {
            "username": post.get("username", ""),
            "shortcode": post.get("shortcode", ""),
            "url": post.get("url", ""),
            "fetched_at": post.get("fetched_at", ""),
        }
        if post.get("upload"):
            uploaded.append(item)
        else:
            pending.append(item)
    return {
        "account": load_account(),
        "pages": pages,
        "pending": pending,
        "uploaded": uploaded,
        "quota": upload_quota(),
        "sessionid_set": has_sessionid(),
    }
