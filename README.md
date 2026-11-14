# reelpipe

Fetch the latest Instagram reel, download it, and upload it to your page — in one run.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp credentials.example.txt credentials.txt
```

Put your Instagram username/password in `credentials.txt`, and source pages in `pages.txt`.

Needs FlClash config at `~/.local/share/com.follow.clash/config.yaml` and `bin/mihomo`.

## Run

```bash
./reelpipe.sh
```

Or open the desktop shortcut. UI: http://127.0.0.1:8765/
