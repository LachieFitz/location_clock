"""
Runs on the Raspberry Pi. Polls iCloud for the 4 hardcoded family members'
locations (reusing the login/geofence logic from ../main.py) and drives one
micro servo per person on GPIO 12/13/18/19 via lgpio, pointing each servo
at one of 4 discrete positions: home / secondary (work or school) / elsewhere
/ travelling (moving fast between places).

No daemon needed - lgpio talks to /dev/gpiochip0 directly.
"""
import sys
import time
import argparse
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as fm  # noqa: E402  (../main.py -- login, fetch, classify, geofences)
import servo  # noqa: E402  (pin mapping + lgpio helpers, shared with servo_test.py)

# -------------------- config (tune once the clock face is in front of you) --------------------
SERVO_PINS = servo.SERVO_PINS
STATE_ANGLES = {"home": 0, "secondary": 60, "elsewhere": 120, "travelling": 180}  # degrees, evenly spaced placeholder
POLL_INTERVAL_S = 60
FETCH_ATTEMPTS = 6   # trimmed from main.py's default of 12 so 4 devices comfortably fit in POLL_INTERVAL_S
FETCH_SLEEP_S = 1.0
STARTUP_STATE = "travelling"  # where every hand parks on boot, before the first poll lands
TRAVEL_S = 0.8             # time allowed for a hand to physically reach its new angle
RELAX_AFTER_MOVE = True    # stop pulsing once parked: quiet + cool. Set False if hands drift.

# "Travelling" detection: compare the newest fix against the oldest one still in
# the history window. Both thresholds must be met - displacement alone trips on
# GPS jitter, speed alone trips when two fixes land microseconds apart.
HISTORY_LEN = 3            # how many recent fixes to keep per person
TRAVEL_SPEED_MPS = 4.0     # ~14 km/h. Above walking pace, below every car trip.
TRAVEL_MIN_MOVE_M = 250.0  # must have actually covered ground, not just jittered
# ================================================================================================


def bucket_for(label):
    """Map a geofence label onto one of the fixed clock positions."""
    if label == "home":
        return "home"
    if label == fm.ELSEWHERE_LABEL:
        return "elsewhere"
    return "secondary"


def is_travelling(history):
    """True if this person has covered real ground, fast, across the window.

    history: deque of (epoch_seconds, lat, lon), oldest first.
    """
    if len(history) < 2:
        return False, 0.0
    t_old, lat_old, lon_old = history[0]
    t_new, lat_new, lon_new = history[-1]
    elapsed = t_new - t_old
    if elapsed <= 0:  # same stale fix repeated - tells us nothing
        return False, 0.0
    displacement = fm.haversine_m(lat_old, lon_old, lat_new, lon_new)
    speed = displacement / elapsed
    return (displacement >= TRAVEL_MIN_MOVE_M and speed >= TRAVEL_SPEED_MPS), speed


def build_name_to_id(labels_cfg):
    name_to_id = {v.get("name"): dev_id for dev_id, v in labels_cfg.get("by_device", {}).items() if v.get("name")}
    missing = [name for name in SERVO_PINS if name not in name_to_id]
    if missing:
        raise SystemExit(f"[!] geofences.json has no device entry with \"name\" set for: {missing}")
    return name_to_id


def drive(gpio, pin, angle):
    """Move one hand and, optionally, let it relax once it has arrived."""
    servo.set_angle(gpio, pin, angle)
    if RELAX_AFTER_MOVE:
        time.sleep(TRAVEL_S)
        servo.set_pulsewidth(gpio, pin, 0)


def park_all(gpio):
    """Put every hand at a known angle at boot, so the clock is never showing
    a stale position while the first poll is still running."""
    angle = STATE_ANGLES[STARTUP_STATE]
    print(f"[i] Parking all hands at {STARTUP_STATE} ({angle}deg) while we fetch locations...")
    for pin in SERVO_PINS.values():
        drive(gpio, pin, angle)


def poll_once(api, labels_cfg, name_to_id, gpio, dry_run, last_bucket, history):
    try:
        devices_by_id = dict(api.devices)
    except Exception as e:
        print(f"[!] Failed to list devices: {e}")
        return

    for name, pin in SERVO_PINS.items():
        dev_id = name_to_id[name]
        dev = devices_by_id.get(dev_id)

        loc = None
        if dev is not None:
            _, loc = fm.fetch_location_hard(api, dev, attempts=FETCH_ATTEMPTS, sleep_s=FETCH_SLEEP_S)

        if not loc:
            # No fix this cycle. Don't invent a position - leave the hand
            # showing the last thing we actually knew.
            print(f"[i] {name:8s} no fix        -- holding at {last_bucket.get(name, '?')}")
            continue

        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        ts_ms = loc.get("timeStamp") or loc.get("timestamp")
        fix_time = (ts_ms / 1000.0) if ts_ms else time.time()

        hist = history.setdefault(name, deque(maxlen=HISTORY_LEN))
        if not hist or hist[-1][0] != fix_time:  # ignore a repeated stale fix
            hist.append((fix_time, lat, lon))

        moving, speed = is_travelling(hist)
        if moving:
            bucket = "travelling"
            detail = f"{speed * 3.6:.0f}km/h"
        else:
            result = fm.classify(dev_id, lat, lon, labels_cfg)
            label = result[0] if result else fm.ELSEWHERE_LABEL
            bucket = bucket_for(label)
            detail = label

        angle = STATE_ANGLES[bucket]
        changed = last_bucket.get(name) != bucket
        arrow = "->" if changed else "=="
        print(f"[i] {name:8s} {detail:14s} {arrow} {bucket:10s} {angle}deg (pin {pin})")

        if not dry_run and changed:
            drive(gpio, pin, angle)
        last_bucket[name] = bucket


def run(dry_run=False, once=False, hold=5.0):
    labels_cfg = fm.load_labels_config(None)
    name_to_id = build_name_to_id(labels_cfg)

    gpio = servo.connect() if not dry_run else None
    last_bucket = {}
    history = {}

    try:
        if gpio is not None:
            park_all(gpio)

        api = fm.login_pyicloud()

        while True:
            cycle_start = time.time()
            poll_once(api, labels_cfg, name_to_id, gpio, dry_run, last_bucket, history)
            if once:
                # lgpio pulses die with this process, so give the servos time to
                # actually reach the commanded position before we exit.
                if gpio is not None:
                    print(f"[i] holding {hold}s so the servos can travel...")
                    time.sleep(hold)
                return
            elapsed = time.time() - cycle_start
            time.sleep(max(0, POLL_INTERVAL_S - elapsed))
    except KeyboardInterrupt:
        print("\n[!] Stopping.")
    finally:
        if gpio is not None:
            servo.disconnect(gpio)  # stops pulsing and frees the pins


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Location-clock servo state machine.")
    ap.add_argument("--dry-run", action="store_true", help="Skip GPIO/hardware; just print what each servo would do. Works off the Pi.")
    ap.add_argument("--once", action="store_true", help="Run a single poll cycle then exit, instead of looping forever.")
    ap.add_argument("--hold", type=float, default=5.0, help="With --once, seconds to keep pulsing before exiting (default 5).")
    args = ap.parse_args()
    run(dry_run=args.dry_run, once=args.once, hold=args.hold)
