"""Shared servo config/helpers for clock_daemon.py and servo_test.py.

Uses lgpio (talks directly to /dev/gpiochip0 via the kernel's GPIO character
device interface) rather than pigpio - no background daemon needed.
"""
try:
    import lgpio
except ImportError:
    lgpio = None

SERVO_PINS = {"dad": 12, "lachlan": 13, "mum": 18, "stella": 19}  # BCM pin numbers
PULSE_MIN_US = 500   # pulsewidth at 0 degrees
PULSE_MAX_US = 2500  # pulsewidth at 180 degrees
GPIO_CHIP = 0  # /dev/gpiochip0 - header GPIOs on Pi 3/4. (Pi 5 moves these to chip 4.)


def angle_to_pulsewidth(angle_deg):
    angle_deg = max(0, min(180, angle_deg))
    return int(PULSE_MIN_US + (angle_deg / 180) * (PULSE_MAX_US - PULSE_MIN_US))


def connect():
    """Open the GPIO chip. Returns a handle to pass into set_pulsewidth/disconnect."""
    if lgpio is None:
        raise SystemExit("[!] lgpio not installed. Run: pip install lgpio  (or: sudo apt install python3-lgpio)")
    try:
        return lgpio.gpiochip_open(GPIO_CHIP)
    except Exception as e:
        raise SystemExit(f"[!] Could not open /dev/gpiochip{GPIO_CHIP}: {e}")


def set_pulsewidth(handle, pin, pulsewidth_us):
    """pulsewidth_us=0 stops sending pulses (servo relaxes)."""
    result = lgpio.tx_servo(handle, pin, int(pulsewidth_us))
    if result < 0:
        print(f"[!] lgpio.tx_servo(pin={pin}, us={pulsewidth_us}) returned error code {result}")
    return result


def disconnect(handle):
    lgpio.gpiochip_close(handle)
