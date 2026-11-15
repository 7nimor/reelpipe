import sys
from contextlib import redirect_stdout

from download_posts import download_all_posts
from latest_post import (
    FLCLASH_CONFIG,
    MIHOMO,
    find_working_node,
    get_latest_post_url,
    is_video_post,
    load_nodes,
    load_pages,
    save_post,
    start_mihomo,
    stop_mihomo,
)
from upload_latest import (
    assert_upload_allowed,
    boost_to_story,
    cleanup_video,
    find_pending_videos,
    load_credentials,
    login,
    make_client,
    mark_uploaded,
    seed_first_comment,
    upload_quota,
    upload_video,
    wait_for_peak_window,
    wait_upload_gap,
)


class LogStream:
    def __init__(self, emit):
        self.emit = emit
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                self.emit(line)

    def flush(self):
        if self._buf.strip():
            self.emit(self._buf.rstrip())
            self._buf = ""


def run_pipeline(emit=print, challenge_handler=None):
    if not MIHOMO.exists():
        raise RuntimeError(f"mihomo not found at {MIHOMO}")
    if not FLCLASH_CONFIG.exists():
        raise RuntimeError("FlClash config not found")

    proc = None
    stream = LogStream(emit)
    uploaded = []
    try:
        with redirect_stdout(stream):
            pages = load_pages()
            username, password, code, sessionid = load_credentials()
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
            download_all_posts()
            quota = upload_quota()
            print(f"quota: {quota['used']}/{quota['limit']} today")
            if not quota["allowed"]:
                print("skip upload: daily limit reached")
                return None
            videos = find_pending_videos()
            if not videos:
                print("nothing to upload")
                return None
            client = make_client(code=code, challenge_handler=challenge_handler)
            login(client, username, password, sessionid=sessionid)
            for video_path in videos:
                quota = upload_quota()
                print(f"quota: {quota['used']}/{quota['limit']} today")
                if not quota["allowed"]:
                    print("skip upload: daily limit reached")
                    break
                print(f"latest: {video_path}")
                assert_upload_allowed()
                wait_for_peak_window()
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
                uploaded.append(media_code)
            return uploaded[-1] if uploaded else None
    finally:
        stream.flush()
        stop_mihomo(proc)


def main():
    try:
        run_pipeline()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
