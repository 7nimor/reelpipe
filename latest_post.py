import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml
from curl_cffi import requests

FLCLASH_CONFIG = Path.home() / ".local/share/com.follow.clash/config.yaml"
ROOT = Path(__file__).resolve().parent
MIHOMO = ROOT / "bin" / "mihomo"
PAGES_FILE = ROOT / "pages.txt"
POSTS_FILE = ROOT / "posts.json"
PROXY_PORT = 17890
API_PORT = 19090


def load_nodes():
    raw = yaml.safe_load(FLCLASH_CONFIG.read_text())
    nodes = []
    for item in raw.get("proxies") or []:
        if item.get("type") != "vless" or item.get("info"):
            continue
        node = dict(item)
        node["skip-cert-verify"] = True
        fingerprint = node.get("fingerprint")
        if isinstance(fingerprint, str) and len(fingerprint) > 16:
            node["client-fingerprint"] = "chrome"
            del node["fingerprint"]
        nodes.append(node)
    if not nodes:
        raise RuntimeError("no FlClash nodes found")
    return nodes


def start_mihomo(nodes):
    names = [node["name"] for node in nodes]
    conf = {
        "mixed-port": PROXY_PORT,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "error",
        "external-controller": f"127.0.0.1:{API_PORT}",
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "nameserver": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
            ],
        },
        "proxies": nodes,
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": names},
        ],
        "rules": ["MATCH,PROXY"],
    }
    workdir = Path(tempfile.mkdtemp(prefix="ig_mihomo_"))
    config_path = workdir / "config.yaml"
    config_path.write_text(yaml.safe_dump(conf, allow_unicode=True))
    proc = subprocess.Popen(
        [str(MIHOMO), "-f", str(config_path), "-d", str(workdir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("mihomo exited early")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{API_PORT}/version",
                timeout=1,
            ) as response:
                response.read()
            return proc, names
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("mihomo api not ready")


def api_put(path, body):
    data = json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        data=data,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def api_delete(path):
    request = urllib.request.Request(
        f"http://127.0.0.1:{API_PORT}{path}",
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except Exception:
        pass


def select_node(name):
    api_put("/proxies/" + urllib.parse.quote("PROXY"), {"name": name})
    api_delete("/connections")


def proxy_url():
    return f"http://127.0.0.1:{PROXY_PORT}"


def test_instagram():
    response = requests.get(
        "https://www.instagram.com/",
        proxies={"http": proxy_url(), "https": proxy_url()},
        impersonate="chrome131",
        timeout=18,
    )
    return response.status_code == 200


def find_working_node(names):
    for name in names:
        print(f"testing: {name}")
        try:
            select_node(name)
            time.sleep(0.2)
            if test_instagram():
                print(f"ok: {name}")
                return name
            print(f"fail: {name}")
        except Exception as error:
            print(f"fail: {name} ({error})")
    raise RuntimeError("no working vpn node")


def get_latest_post_url(username):
    username = username.lstrip("@")
    response = requests.get(
        f"https://www.instagram.com/{username}/",
        proxies={"http": proxy_url(), "https": proxy_url()},
        impersonate="chrome131",
        timeout=40,
    )
    response.raise_for_status()
    matches = list(
        dict.fromkeys(
            re.findall(r"/(p|reel)/([A-Za-z0-9_-]+)/", response.text)
        )
    )
    if not matches:
        raise RuntimeError("no posts found")
    kind, code = matches[0]
    return f"https://www.instagram.com/{kind}/{code}/"


def is_video_post(url):
    if "/reel/" in url:
        return True
    response = requests.get(
        url,
        proxies={"http": proxy_url(), "https": proxy_url()},
        impersonate="chrome131",
        timeout=40,
    )
    response.raise_for_status()
    page = response.text
    if re.search(r'<meta property="og:video" content="', page):
        return True
    if re.search(r'"video_url"\s*:\s*"', page):
        return True
    if re.search(r'"media_type"\s*:\s*2\b', page):
        return True
    return False


def load_pages():
    if not PAGES_FILE.exists():
        raise RuntimeError(f"pages file not found: {PAGES_FILE}")
    pages = []
    for line in PAGES_FILE.read_text(encoding="utf-8").splitlines():
        page = line.strip().lstrip("@")
        if page and not page.startswith("#"):
            pages.append(page)
    if not pages:
        raise RuntimeError("no pages in pages.txt")
    return pages


def read_posts():
    if not POSTS_FILE.exists():
        return []
    text = POSTS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise RuntimeError("posts.json must be a list")
    return data


def write_posts(posts):
    POSTS_FILE.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_post(username, url):
    username = username.lstrip("@")
    shortcode = url.rstrip("/").split("/")[-1]
    posts = read_posts()
    for post in posts:
        if post.get("shortcode") == shortcode:
            return None
    entry = {
        "username": username,
        "url": url,
        "shortcode": shortcode,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    posts.append(entry)
    write_posts(posts)
    return entry


def stop_mihomo(proc):
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def main():
    if not MIHOMO.exists():
        print(f"Error: mihomo not found at {MIHOMO}", file=sys.stderr)
        sys.exit(1)

    if not FLCLASH_CONFIG.exists():
        print(f"Error: FlClash config not found", file=sys.stderr)
        sys.exit(1)

    proc = None
    try:
        pages = load_pages()
        nodes = load_nodes()
        print(f"loaded {len(nodes)} nodes from FlClash")
        proc, names = start_mihomo(nodes)
        working = find_working_node(names)
        print(f"using: {working}")
        for page in pages:
            print(f"fetching: {page}")
            url = get_latest_post_url(page)
            if not is_video_post(url):
                print(f"skip (not video): {url}")
                continue
            entry = save_post(page, url)
            if entry is None:
                print(f"skip (duplicate): {url}")
            else:
                print(url)
        from download_posts import download_all_posts

        download_all_posts()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    finally:
        stop_mihomo(proc)


if __name__ == "__main__":
    main()
