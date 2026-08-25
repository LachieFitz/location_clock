"""
Runs on the Raspberry Pi. Polls iCloud for the 4 hardcoded family members'
locations (reusing the login/geofence logic from ../main.py) and drives one
micro servo per person on GPIO 12/13/18/19 via pigpio, pointing each servo
at one of 4 discrete positions: home / secondary (work or school) / elsewhere
/ unknown (no fix yet).

Requires the pigpio daemon running on the Pi: sudo pigpiod
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as fm  # noqa: E402  (../main.py -- login, fetch, classify, geofences)
import servo  # noqa: E402  (pin mapping + pigpio helpers, shared with servo_test.py)

# -------------------- config (tune once the clock face is in front of you) --------------------
SERVO_PINS = servo.SERVO_PINS
STATE_ANGLES = {"home": 0, "secondary": 60, "elsewhere": 120, "unknown": 180}  # degrees, evenly spaced placeholder
POLL_INTERVAL_S = 60
FETCH_ATTEMPTS = 6   # trimmed from main.py's default of 12 so 4 devices comfortably fit in POLL_INTERVAL_S
FETCH_SLEEP_S = 1.0
# ================================================================================================


def bucket_for(label):
    if label is None:
        return "unknown"
    if label == "home":
        return "home"
    if label == fm.ELSEWHERE_LABEL:
        return "elsewhere"
    return "secondary"


def build_name_to_id(labels_cfg):
    name_to_id = {v.get("name"): dev_id for dev_id, v in labels_cfg.get("by_device", {}).items() if v.get("name")}
    missing = [name for name in SERVO_PINS if name not in name_to_id]
    if missing:
        raise SystemExit(f"[!] geofences.json has no device entry with \"name\" set for: {missing}")
    return name_to_id


def poll_once(api, labels_cfg, name_to_id, pi, dry_run):
    try:
        devices_by_id = dict(api.devices)
    except Exception as e:
        print(f"[!] Failed to list devices: {e}")
        return

    for name, pin in SERVO_PINS.items():
        dev_id = name_to_id[name]
        dev = devices_by_id.get(dev_id)
        label = None
        if dev is not None:
            _, loc = fm.fetch_location_hard(api, dev, attempts=FETCH_ATTEMPTS, sleep_s=FETCH_SLEEP_S)
            if loc:
                lat = float(loc["latitude"])
                lon = float(loc["longitude"])
                result = fm.classify(dev_id, lat, lon, labels_cfg)
                label = result[0] if result else fm.ELSEWHERE_LABEL

        bucket = bucket_for(label)
        angle = STATE_ANGLES[bucket]
        print(f"[i] {name:8s} label={label!r:12s} -> {bucket:9s} -> {angle}deg (pin {pin})")

        if not dry_run:
            pi.set_servo_pulsewidth(pin, servo.angle_to_pulsewidth(angle))


def run(dry_run=False, once=False):
    labels_cfg = fm.load_labels_config(None)
    name_to_id = build_name_to_id(labels_cfg)

    pi = servo.connect() if not dry_run else None

    api = fm.login_pyicloud()

    try:
        while True:
            cycle_start = time.time()
            poll_once(api, labels_cfg, name_to_id, pi, dry_run)
            if once:
                return
            elapsed = time.time() - cycle_start
            time.sleep(max(0, POLL_INTERVAL_S - elapsed))
    except KeyboardInterrupt:
        print("\n[!] Stopping.")
    finally:
        if pi is not None:
            for pin in SERVO_PINS.values():
                pi.set_servo_pulsewidth(pin, 0)  # stop sending signal, let servo relax
            pi.stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Location-clock servo state machine.")
    ap.add_argument("--dry-run", action="store_true", help="Skip pigpio/hardware; just print what each servo would do. Works off the Pi.")
    ap.add_argument("--once", action="store_true", help="Run a single poll cycle then exit, instead of looping forever.")
    args = ap.parse_args()
    run(dry_run=args.dry_run, once=args.once)
