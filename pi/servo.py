"""Shared servo config/helpers for clock_daemon.py and servo_test.py."""
try:
    import pigpio
except ImportError:
    pigpio = None

SERVO_PINS = {"dad": 12, "lachlan": 13, "mum": 18, "stella": 19}  # BCM pin numbers
PULSE_MIN_US = 500   # pulsewidth at 0 degrees
PULSE_MAX_US = 2500  # pulsewidth at 180 degrees


def angle_to_pulsewidth(angle_deg):
    angle_deg = max(0, min(180, angle_deg))
    return int(PULSE_MIN_US + (angle_deg / 180) * (PULSE_MAX_US - PULSE_MIN_US))


def connect():
    if pigpio is None:
        raise SystemExit("[!] pigpio not installed. Run: pip install pigpio")
    pi = pigpio.pi()
    if not pi.connected:
        raise SystemExit("[!] Could not connect to pigpiod. Run: sudo pigpiod")
    return pi
