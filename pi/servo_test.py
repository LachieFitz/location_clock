"""
Manual servo control for wiring/calibration - no iCloud involved at all.

Examples:
    python servo_test.py --who dad --angle 90       # move one servo, hold, exit
    python servo_test.py --pin 12 --angle 0
    python servo_test.py --who mum --sweep          # sweep 0->180->0 to check range/wiring
    python servo_test.py --who stella --off         # stop sending pulses, let it relax
    python servo_test.py                             # interactive: pick pin/angle repeatedly
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import servo


def move(pi, pin, angle):
    pw = servo.angle_to_pulsewidth(angle)
    pi.set_servo_pulsewidth(pin, pw)
    print(f"[i] pin {pin} -> {angle}deg (pulsewidth {pw}us)")


def sweep(pi, pin):
    for angle in list(range(0, 181, 10)) + list(range(180, -1, -10)):
        move(pi, pin, angle)
        time.sleep(0.15)


def resolve_pin(args):
    if args.pin is not None:
        return args.pin
    if args.who:
        if args.who not in servo.SERVO_PINS:
            raise SystemExit(f"[!] Unknown --who {args.who!r}. Options: {list(servo.SERVO_PINS)}")
        return servo.SERVO_PINS[args.who]
    return None


def interactive(pi):
    print("Interactive servo test. Pin options:", servo.SERVO_PINS)
    print("Enter: '<pin> <angle>'  e.g. '12 90'   |  'off <pin>' to release  |  'q' to quit\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line or line.lower() == "q":
            break
        parts = line.split()
        try:
            if parts[0].lower() == "off" and len(parts) == 2:
                pin = int(parts[1])
                pi.set_servo_pulsewidth(pin, 0)
                print(f"[i] pin {pin} off")
                continue
            pin, angle = int(parts[0]), float(parts[1])
            move(pi, pin, angle)
        except (ValueError, IndexError):
            print("[!] Couldn't parse that. Example: '12 90' or 'off 12'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Manual servo control for wiring/calibration.")
    ap.add_argument("--pin", type=int, help="BCM pin number, e.g. 12")
    ap.add_argument("--who", choices=list(servo.SERVO_PINS), help="Look up pin by person name instead of --pin")
    ap.add_argument("--angle", type=float, help="Angle in degrees (0-180). Moves once and holds.")
    ap.add_argument("--sweep", action="store_true", help="Sweep 0->180->0 slowly, useful for checking range/wiring")
    ap.add_argument("--off", action="store_true", help="Stop sending pulses (let the servo relax) and exit")
    args = ap.parse_args()

    pi = servo.connect()
    try:
        pin = resolve_pin(args)
        if pin is None:
            interactive(pi)
        elif args.off:
            pi.set_servo_pulsewidth(pin, 0)
            print(f"[i] pin {pin} off")
        elif args.sweep:
            sweep(pi, pin)
        elif args.angle is not None:
            move(pi, pin, args.angle)
        else:
            raise SystemExit("[!] Give --angle, --sweep, or --off along with --pin/--who.")
    finally:
        pi.stop()
