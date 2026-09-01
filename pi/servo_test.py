"""
Manual servo control for wiring/calibration - no iCloud involved at all.

NOTE: lgpio generates the pulse train inside THIS process. When the script
exits, pulsing stops. So --angle holds for --hold seconds (default 5) rather
than exiting immediately, otherwise the servo never receives a pulse at all.

Examples:
    python servo_test.py --who dad --angle 90         # move, hold 5s, release
    python servo_test.py --pin 12 --angle 0 --hold 30
    python servo_test.py --who mum --sweep            # sweep 0->180->0
    python servo_test.py --who dad --bitbang 2000     # bypass tx_servo entirely (diagnostic)
    python servo_test.py                              # interactive: pulses held until you quit
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import servo


def move(gpio, pin, angle):
    pw = servo.angle_to_pulsewidth(angle)
    servo.set_pulsewidth(gpio, pin, pw)
    print(f"[i] pin {pin} -> {angle}deg (pulsewidth {pw}us)")


def sweep(gpio, pin):
    for angle in list(range(0, 181, 10)) + list(range(180, -1, -10)):
        move(gpio, pin, angle)
        time.sleep(0.15)


def resolve_pin(args):
    if args.pin is not None:
        return args.pin
    if args.who:
        return servo.SERVO_PINS[args.who]
    return None


def interactive(gpio):
    print("Interactive servo test. Pin options:", servo.SERVO_PINS)
    print("Enter: '<pin> <angle>'  e.g. '12 90'   |  'off <pin>' to release  |  'q' to quit")
    print("(pulses are held while this stays open - quitting releases every servo)\n")
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
                servo.set_pulsewidth(gpio, pin, 0)
                print(f"[i] pin {pin} off")
                continue
            pin, angle = int(parts[0]), float(parts[1])
            move(gpio, pin, angle)
        except (ValueError, IndexError):
            print("[!] Couldn't parse that. Example: '12 90' or 'off 12'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Manual servo control for wiring/calibration.")
    ap.add_argument("--pin", type=int, help="BCM pin number, e.g. 12")
    ap.add_argument("--who", choices=list(servo.SERVO_PINS), help="Look up pin by person name instead of --pin")
    ap.add_argument("--angle", type=float, help="Angle in degrees (0-180).")
    ap.add_argument("--hold", type=float, default=5.0, help="Seconds to keep pulsing after moving (default 5). lgpio stops when this process exits.")
    ap.add_argument("--sweep", action="store_true", help="Sweep 0->180->0 slowly, useful for checking range/wiring")
    ap.add_argument("--bitbang", type=float, metavar="US", help="Diagnostic: hand-generate the pulse train at this pulsewidth (us), bypassing tx_servo entirely.")
    ap.add_argument("--off", action="store_true", help="Stop sending pulses (let the servo relax) and exit")
    args = ap.parse_args()

    pin = resolve_pin(args)
    gpio = servo.connect(pins=[pin] if pin is not None else None)
    try:
        if pin is None:
            interactive(gpio)
        elif args.off:
            servo.set_pulsewidth(gpio, pin, 0)
            print(f"[i] pin {pin} off")
        elif args.bitbang is not None:
            print(f"[i] bit-banging {args.bitbang}us pulses on pin {pin} for {args.hold}s (tx_servo NOT used)")
            servo.bitbang(gpio, pin, args.bitbang, seconds=args.hold)
        elif args.sweep:
            sweep(gpio, pin)
        elif args.angle is not None:
            move(gpio, pin, args.angle)
            print(f"[i] holding for {args.hold}s (Ctrl-C to stop early)...")
            try:
                time.sleep(args.hold)
            except KeyboardInterrupt:
                pass
        else:
            raise SystemExit("[!] Give --angle, --sweep, --bitbang, or --off along with --pin/--who.")
    finally:
        servo.disconnect(gpio, pins=[pin] if pin is not None else None)
