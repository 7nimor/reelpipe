from latest_post import PAGES_FILE, load_pages, read_posts
from upload_latest import CREDENTIALS_FILE, upload_quota


def load_account():
    if not CREDENTIALS_FILE.exists():
        return ""
    for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "username":
            return value.strip().lstrip("@")
    return ""


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
    }
