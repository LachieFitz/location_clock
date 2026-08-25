import os, re, time, json, argparse, getpass, shutil, math
from pathlib import Path
from datetime import datetime, timezone

# ----- deps -----
try:
    from pyicloud import PyiCloudService
except Exception:
    raise SystemExit("pyicloud not installed. Run: pip install -U pyicloud")
try:
    import keyring
except Exception:
    keyring = None

APP_NAME = "FindMyCLI"

# ----- state/paths -----
if os.name == "nt":
    STATE_DIR = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "FindMy"
else:
    STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "FindMy"
STATE_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_DIR = STATE_DIR / "pyicloud_cookies"
COOKIE_DIR.mkdir(parents=True, exist_ok=True)
APPLE_ID_FILE = STATE_DIR / "apple_id.txt"
ELSEWHERE_LABEL = "elsewhere"

# Geofences (device IDs, home/work/school coords) live in geofences.json next
# to this script, not in source — that file is gitignored since it's personal
# data. See geofences.example.json for the structure. Override with --labels-file.
DEFAULT_LABELS_FILE = Path(__file__).parent / "geofences.json"
EMPTY_GEOFENCE_CONFIG = {"by_device": {}, "default": {"labels": {}}}

# -------------------- credential helpers --------------------
def _read_saved_apple_id():
    try:
        return APPLE_ID_FILE.read_text(encoding="utf-8").strip() if APPLE_ID_FILE.exists() else None
    except Exception:
        return None

def _save_apple_id(username: str):
    try:
        APPLE_ID_FILE.write_text(username, encoding="utf-8")
    except Exception:
        pass

def _get_saved_password(username: str):
    if not keyring: return None
    try: return keyring.get_password(APP_NAME, username)
    except Exception: return None

def _save_password(username: str, password: str):
    if not keyring: return
    try:
        keyring.set_password(APP_NAME, username, password)
        print("[i] Saved password to Windows Credential Manager.")
    except Exception:
        print("[!] Could not save password to Credential Manager (continuing).")

def _clear_cookies():
    if COOKIE_DIR.exists():
        for p in COOKIE_DIR.iterdir():
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except Exception:
                pass
        print("[i] Cleared cached cookies.")

def _forget_everything():
    user = _read_saved_apple_id()
    if user and keyring:
        try:
            keyring.delete_password(APP_NAME, user)
            print(f"[i] Removed saved password for {user}.")
        except Exception:
            pass
    try:
        if APPLE_ID_FILE.exists():
            APPLE_ID_FILE.unlink()
            print("[i] Removed saved Apple ID.")
    except Exception:
        pass
    _clear_cookies()

def prompt_credentials(default_user=None):
    user = input(f"Apple ID (email){' ['+default_user+']' if default_user else ''}: ").strip() or (default_user or "")
    pw = getpass.getpass("Apple ID password: ")
    return user, pw

def login_pyicloud(preferred_user=None):
    # 1) try cookie reuse
    try:
        api = PyiCloudService("", "", cookie_directory=str(COOKIE_DIR))
        if not getattr(api, "requires_2fa", False) and not getattr(api, "requires_2sa", False):
            print("[i] Reusing trusted iCloud session from cookies.")
            return api
    except Exception:
        api = None

    username = preferred_user or _read_saved_apple_id() or os.environ.get("FM_APPLE_ID")
    password = _get_saved_password(username) if username else None
    if not username or not password:
        username, password = prompt_credentials(default_user=username)
        if username: _save_apple_id(username)
        if password: _save_password(username, password)

    api = PyiCloudService(username, password, cookie_directory=str(COOKIE_DIR))
    if getattr(api, "requires_2fa", False):
        print("[i] 2FA required. Check your devices.")
        code = input("Enter the 6-digit code: ").strip()
        if not api.validate_2fa_code(code): raise SystemExit("[!] Invalid 2FA code")
        if not getattr(api, "is_trusted_session", False): api.trust_session()
        print("[i] Trusted this session.")
    elif getattr(api, "requires_2sa", False):
        print("[i] Two-step auth required (legacy).")
        devs = api.trusted_devices
        if not devs: raise SystemExit("[!] No trusted devices for 2SA")
        idx = next((i for i, d in enumerate(devs) if d.get("phoneNumber")), 0)
        api.send_verification_code(devs[idx])
        code = input("Enter the verification code: ").strip()
        if not api.validate_verification_code(devs[idx], code): raise SystemExit("[!] Invalid 2SA code")
        print("[i] 2SA verified.")
    else:
        print("[i] Logged in without additional prompts.")
    return api

# -------------------- device + location helpers --------------------
def get_devices_stable(api):
    """Stable order (by device id) so your indexes don't shuffle."""
    try:
        devs = list(dict(api.devices).values())
    except Exception:
        devs = []
    if not devs:
        try:
            _ = api.iphone.location()
            devs = list(dict(api.devices).values())
        except Exception:
            pass
    try:
        devs.sort(key=lambda d: getattr(d, "id", ""))
    except Exception:
        pass
    return devs

def device_label(dev):
    try:
        s = dev.status() or {}
    except Exception:
        s = {}
    name  = s.get("name") or s.get("deviceDisplayName") or "Unknown"
    model = s.get("modelDisplayName") or s.get("deviceModel") or ""
    batt  = s.get("batteryLevel")
    if batt is not None:
        try: batt = round(float(batt) * 100)
        except Exception: pass
    return name, model, batt

def safe_service_refresh(api):
    try:
        svc = getattr(api, "iphone", None)
        if svc is not None and hasattr(svc, "refresh_client"):
            svc.refresh_client()
    except Exception:
        pass

def raw_refresh_client(api, should_locate=False, selected_device=None):
    base = None
    try:
        ws = getattr(api, "_base_login", {}).get("webservices", {})
        if "findme" in ws and "url" in ws["findme"]:
            base = ws["findme"]["url"]
    except Exception:
        pass
    if not base:
        try:
            svc = getattr(api, "iphone", None)
            base = getattr(svc, "_service_endpoint", None)
        except Exception:
            base = None
    if not base: return None
    url = base.rstrip("/") + "/fmipservice/client/web/refreshClient"
    payload = {"clientContext": {"fmly": True, "shouldLocate": bool(should_locate)}}
    if selected_device: payload["clientContext"]["selectedDevice"] = str(selected_device)
    headers = {"Content-Type": "application/json; charset=UTF-8"}
    try:
        r = api.session.post(url, headers=headers, data=json.dumps(payload))
        if r.status_code == 200: return r.json()
    except Exception:
        pass
    return None

def read_device_location(dev):
    """Works with both pyicloud variants: property (dict) or method returning dict."""
    loc_attr = getattr(dev, "location", None)
    if callable(loc_attr):
        try: return loc_attr()
        except TypeError: pass
    if isinstance(loc_attr, dict):
        return loc_attr
    return None

def fetch_location_hard(api, dev, attempts=12, sleep_s=1.5, nudge=False):
    last_err = None
    try:
        raw_refresh_client(api, should_locate=True, selected_device=getattr(dev, "id", None))
    except Exception:
        pass
    for i in range(1, attempts + 1):
        try:
            s = dev.status() or {}
            loc = s.get("location")
            if isinstance(loc, dict) and loc.get("latitude") and loc.get("longitude"):
                return ("status", loc)
        except Exception as e:
            last_err = e
        try:
            loc = read_device_location(dev)
            if isinstance(loc, dict) and loc.get("latitude") and loc.get("longitude"):
                return ("direct", loc)
        except Exception as e:
            last_err = e
        if i in (1,3,6,9,12):
            try: raw_refresh_client(api, should_locate=True, selected_device=getattr(dev, "id", None))
            except Exception as e: last_err = e
        if nudge and i in (1,7):
            try: dev.play_sound()
            except Exception as e: last_err = e
        safe_service_refresh(api)
        time.sleep(sleep_s)
    if last_err: print(f"[i] Last error seen: {last_err}")
    return (None, None)

def show_location(loc):
    lat = loc.get("latitude"); lon = loc.get("longitude")
    ts = loc.get("timeStamp") or loc.get("timestamp")
    when = datetime.fromtimestamp(ts/1000, tz=timezone.utc).astimezone() if ts else None
    acc = loc.get("horizontalAccuracy")
    print(f"  Latitude,Long.: {lat}, {lon}")
    if when: print(f"  When          : {when.isoformat(timespec='seconds')}")
    if acc is not None: print(f"  Accuracy (m)  : {acc}")

# -------------------- geofence / labeling --------------------
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def load_labels_config(path: str | None):
    p = Path(path) if path else DEFAULT_LABELS_FILE
    if not p.is_file():
        return EMPTY_GEOFENCE_CONFIG
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "by_device": data.get("by_device", {}),
            "default": {"labels": data.get("default", {}).get("labels", {})},
        }
    except Exception as e:
        raise SystemExit(f"[!] failed to parse labels file {p}: {e}")

def classify(device_id: str, lat: float, lon: float, labels_cfg):
    """Return (label, distance_m, radius_m) for the closest matching fence (if any)."""
    best = None  # (label, dist, radius)
    # device-specific first
    dev_cfg = labels_cfg.get("by_device", {}).get(device_id, {}).get("labels", {})
    for label, g in dev_cfg.items():
        dist = haversine_m(lat, lon, g["lat"], g["lon"])
        if dist <= float(g["radius_m"]):
            if best is None or dist < best[1]:
                best = (label, dist, float(g["radius_m"]))
    # then defaults
    if best is None:
        for label, g in labels_cfg.get("default", {}).get("labels", {}).items():
            dist = haversine_m(lat, lon, g["lat"], g["lon"])
            if dist <= float(g["radius_m"]):
                if best is None or dist < best[1]:
                    best = (label, dist, float(g["radius_m"]))
    return best  # or None

# -------------------- CLI --------------------
def main():
    ap = argparse.ArgumentParser(description="Fetch device locations via iCloud Find Devices + per-person geofencing.")
    ap.add_argument("--device", "-d", help="Device index or comma-separated (e.g., 4 or 4,7,15). Use --show/--show-ids.")
    ap.add_argument("--attempts", type=int, default=12, help="Retry attempts (default 12)")
    ap.add_argument("--sleep", type=float, default=1.5, help="Seconds between retries (default 1.5)")
    ap.add_argument("--nudge", action="store_true", help="Play a sound on the device to provoke a fresh location (audible).")
    ap.add_argument("--show", action="store_true", help="Show current index→device mapping (name/model/battery).")
    ap.add_argument("--show-ids", action="store_true", help="Show index→device mapping including device IDs (for geofences).")
    ap.add_argument("--apple-id", help="Override Apple ID for this run (will store in Credential Manager).")
    ap.add_argument("--clear-cookies", action="store_true", help="Delete cached cookies before login.")
    ap.add_argument("--forget", action="store_true", help="Forget saved Apple ID & password and cookies, then exit.")
    ap.add_argument("--labels-file", help="Path to JSON geofence config (default: geofences.json next to this script; see geofences.example.json).")
    args = ap.parse_args()

    if args.forget:
        _forget_everything(); return
    if args.clear_cookies:
        _clear_cookies()

    preferred_user = args.apple_id or _read_saved_apple_id()
    api = login_pyicloud(preferred_user=preferred_user)

    devices = get_devices_stable(api)
    if not devices:
        raise SystemExit("[!] iCloud returned no devices.")

    if args.show or args.show_ids or not args.device:
        print("\n[i] Current device index map (stable order by device ID):")
        for i, d in enumerate(devices):
            name, model, batt = device_label(d)
            btxt = f"{batt}%" if batt is not None else "?"
            if args.show_ids:
                print(f"  {i:>3}. {name} — {model} — battery: {btxt} — id: {getattr(d,'id','')}")
            else:
                print(f"  {i:>3}. {name} — {model} — battery: {btxt}")
        if not args.device:
            return

    # parse indexes
    try:
        idxs = [int(x) for x in re.split(r"[,\s]+", args.device.strip()) if x.strip()]
    except Exception:
        raise SystemExit("[!] Invalid --device list. Example: --device 4 or --device 4,7,15")

    labels_cfg = load_labels_config(args.labels_file)

    for idx in idxs:
        if idx < 0 or idx >= len(devices):
            print(f"[!] Index {idx} out of range (0..{len(devices)-1}). Skipping.")
            continue
        dev = devices[idx]
        name, model, batt = device_label(dev)
        btxt = f"{batt}%" if batt is not None else "?"
        print(f"\n[i] Locating #{idx}: {name} — {model} — battery: {btxt}")

        _, loc = fetch_location_hard(api, dev, attempts=args.attempts, sleep_s=args.sleep, nudge=args.nudge)
        if not loc:
            print(f"[!] No coordinates for index {idx} ({name}) right now.")
            continue

        show_location(loc)

        # ---- classification
        lat = float(loc.get("latitude")); lon = float(loc.get("longitude"))
        dev_id = str(getattr(dev, "id", ""))
        result = classify(dev_id, lat, lon, labels_cfg)
        if result:
            label, dist, radius = result
            print(f"[✓] Label       : {label.upper()} ({int(dist)}m ≤ {int(radius)}m)")
        else:
            print(f"[✓] Label       : {ELSEWHERE_LABEL}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Cancelled.")
    except Exception:
        print("[fatal] unhandled exception:")
        raise
