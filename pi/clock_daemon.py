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
STATE_ANGLES = {"home": 180, "secondary": 135, "elsewhere": 90, "travelling": 45}
POLL_INTERVAL_S = 60
FETCH_ATTEMPTS = 6   # trimmed from main.py's default of 12 so 4 devices comfortably fit in POLL_INTERVAL_S
FETCH_SLEEP_S = 1.0

STARTUP_STATE = "travelling"  # where every hand parks on boot, before the first poll lands
SETTLE_S = 0.5   # time for a hand to physically arrive. Then we cut the signal.
RELAX_AFTER_MOVE = True
# ^ Once a hand has arrived, stop pulsing. A servo that is still being driven
# hunts around the target and grinds/buzzes; with the signal removed it just
# sits there. Set False only if you find hands sagging out of position.

# "Travelling" detection: compare the newest fix against the oldest one still in
# the history window. Both thresholds must be met - displacement alone trips on
# GPS jitter, speed alone trips when two fixes land microseconds apart.
HISTORY_LEN = 3            # how many recent fixes to keep per person
TRAVEL_SPEED_MPS = 4.0     # ~14 km/h. Above walking pace, below every car trip.
TRAVEL_MIN_MOVE_M = 250.0  # must have actually covered ground, not just jittered
# ================================================================================================

REQUIRED_STATES = ("home", "secondary", "elsewhere", "travelling")


def check_config():
    """Print the settings actually in force, and fail early on a mismatch.

    The path matters: if you edited a different copy of clock_daemon.py than
    the one being executed, the angles printed here won't match your edit -
    and that, not the servo, is the bug.
    """
    print(f"[i] config from : {Path(__file__).resolve()}")
    print(f"[i] servo pins  : {SERVO_PINS}")
    print(f"[i] state angles: {STATE_ANGLES}")
    missing = [s for s in REQUIRED_STATES if s not in STATE_ANGLES]
    if missing:
        raise SystemExit(f"[!] STATE_ANGLES has no angle for: {missing}. "
                         f"It currently has: {sorted(STATE_ANGLES)}")
    if STARTUP_STATE not in STATE_ANGLES:
        raise SystemExit(f"[!] STARTUP_STATE {STARTUP_STATE!r} has no angle in STATE_ANGLES.")


def fence_for(labels_cfg, dev_id, label):
    """Find the geofence dict a label came from (device-specific first)."""
    by_dev = labels_cfg.get("by_device", {}).get(dev_id, {}).get("labels", {})
    if label in by_dev:
        return by_dev[label]
    return labels_cfg.get("default", {}).get("labels", {}).get(label, {})


def bucket_for(label, fence=None):
    """Map a geofence label onto one of the fixed clock positions.

    A fence may name its position explicitly with a "state" key, which is how
    you get several different places to drive the same hand position:

        "nanna": {"lat": .., "lon": .., "radius_m": 200, "state": "home"}

    Without a "state", the label name decides: "home" -> home,
    "elsewhere" -> elsewhere, anything else -> secondary. So extra work/school
    style places need no "state" at all.
    """
    declared = (fence or {}).get("state")
    if declared:
        return declared
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


def validate_states(labels_cfg):
    """Catch a typo'd "state" in geofences.json now, not on the poll where
    that person finally walks into that fence."""
    bad = []
    sections = [("default", labels_cfg.get("default", {}).get("labels", {}))]
    for dev_id, cfg in labels_cfg.get("by_device", {}).items():
        sections.append((cfg.get("name", dev_id[:8]), cfg.get("labels", {})))
    for who, labels in sections:
        for label, fence in labels.items():
            state = fence.get("state")
            if state and state not in STATE_ANGLES:
                bad.append(f"{who}/{label}: state={state!r}")
    if bad:
        raise SystemExit("[!] geofences.json uses states with no angle defined: "
                         + "; ".join(bad) + f". Valid: {sorted(STATE_ANGLES)}")


def build_name_to_id(labels_cfg):
    name_to_id = {v.get("name"): dev_id for dev_id, v in labels_cfg.get("by_device", {}).items() if v.get("name")}
    missing = [name for name in SERVO_PINS if name not in name_to_id]
    if missing:
        raise SystemExit(f"[!] geofences.json has no device entry with \"name\" set for: {missing}")
    return name_to_id


def drive(gpio, pin, angle):
    """Move one hand, let it arrive, then stop driving it so it doesn't grind."""
    servo.set_angle(gpio, pin, angle)
    if RELAX_AFTER_MOVE:
        time.sleep(SETTLE_S)
        servo.set_pulsewidth(gpio, pin, 0)


def park_all(gpio):
    """Put every hand at a known angle at boot, so the clock isn't showing a
    stale position for the minute or so the first poll takes."""
    angle = STATE_ANGLES[STARTUP_STATE]
    print(f"[i] Parking all hands at {STARTUP_STATE} ({angle}deg) while we fetch locations...")
    for pin in SERVO_PINS.values():
        drive(gpio, pin, angle)


def selftest(gpio):
    """Walk every hand through all four state angles - no iCloud involved."""
    for name, pin in SERVO_PINS.items():
        print(f"\n[i] {name} (pin {pin})")
        for state in REQUIRED_STATES:
            angle = STATE_ANGLES[state]
            print(f"    {state:11s} -> {angle}deg")
            drive(gpio, pin, angle)
            time.sleep(0.4)
    print("\n[i] Self-test done.")


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
            print(f"[i] {name:8s} no fix         -- holding at {last_bucket.get(name, '?')}")
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
            bucket = bucket_for(label, fence_for(labels_cfg, dev_id, label))
            detail = label

        angle = STATE_ANGLES[bucket]
        changed = last_bucket.get(name) != bucket
        arrow = "->" if changed else "=="
        print(f"[i] {name:8s} {detail:14s} {arrow} {bucket:10s} {angle}deg (pin {pin})")

        # Only drive on an actual state change - re-commanding an already
        # correct hand every minute is what makes them grind for no reason.
        if not dry_run and changed:
            drive(gpio, pin, angle)
        last_bucket[name] = bucket


def run(dry_run=False, once=False, run_selftest=False):
    check_config()
    labels_cfg = fm.load_labels_config(None)
    validate_states(labels_cfg)
    name_to_id = build_name_to_id(labels_cfg)

    gpio = servo.connect() if not dry_run else None
    last_bucket = {}
    history = {}

    try:
        if run_selftest:
            if gpio is None:
                raise SystemExit("[!] --selftest needs real hardware; drop --dry-run.")
            selftest(gpio)
            return

        if gpio is not None:
            park_all(gpio)

        api = fm.login_pyicloud()

        while True:
            cycle_start = time.time()
            poll_once(api, labels_cfg, name_to_id, gpio, dry_run, last_bucket, history)
            if once:
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
    ap.add_argument("--selftest", action="store_true", help="Walk every hand through all four state angles. No iCloud.")
    args = ap.parse_args()
    run(dry_run=args.dry_run, once=args.once, run_selftest=args.selftest)
