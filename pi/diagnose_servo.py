"""
Servo fault-isolation ladder. Run this and watch the servo during each step.

Each step tests exactly ONE additional layer, so whichever step first fails to
move the servo tells you which layer is broken. Answer y/n honestly - the point
is to localise the fault, not to pass.

    python diagnose_servo.py --pin 12
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import servo

try:
    import lgpio
except ImportError:
    raise SystemExit("[!] lgpio not importable. Is the venv made with --system-site-packages?")


def ask(question):
    try:
        return input(f"    >>> {question} [y/n]: ").strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        sys.exit("\naborted")


def main(pin):
    print(f"\n=== Servo diagnostic ladder on BCM pin {pin} ===")
    print("Watch the servo horn during each step.\n")

    handle = lgpio.gpiochip_open(servo.GPIO_CHIP)
    results = {}

    try:
        # ---- Step 1: can we claim the line at all? --------------------------
        print("[1] Claiming pin as output...")
        rc = lgpio.gpio_claim_output(handle, pin)
        print(f"    gpio_claim_output -> {rc}")
        if rc < 0:
            print("    FAIL: cannot claim the pin. Something else holds it, or permissions.")
            return
        print("    OK\n")

        # ---- Step 2: raw digital toggle (known-good baseline) ---------------
        print("[2] Toggling pin high/low 3x (1s each). This is the path already proven to reach the motor.")
        for i in range(6):
            lgpio.gpio_write(handle, pin, i % 2)
            time.sleep(0.5)
        lgpio.gpio_write(handle, pin, 0)
        results["toggle"] = ask("Did you see ANY twitch/movement/buzz from the servo?")
        print()

        # ---- Step 3: hand-rolled pulse train, bypassing tx_servo ------------
        print("[3] Bit-banging a real servo pulse train by hand (NO tx_servo).")
        print("    ~1000us (one extreme) for 2s...")
        servo.bitbang(handle, pin, 1000, seconds=2.0)
        print("    ~2000us (other extreme) for 2s...")
        servo.bitbang(handle, pin, 2000, seconds=2.0)
        lgpio.gpio_write(handle, pin, 0)
        results["bitbang"] = ask("Did the servo move between the two positions?")
        print()

        # ---- Step 4: lgpio's own tx_servo, held alive ----------------------
        print("[4] Using lgpio.tx_servo (what the project code uses), holding the process alive.")
        print("    1000us for 3s...")
        rc1 = lgpio.tx_servo(handle, pin, 1000, servo.SERVO_FREQ_HZ)
        print(f"    tx_servo -> {rc1}")
        time.sleep(3)
        print("    2000us for 3s...")
        rc2 = lgpio.tx_servo(handle, pin, 2000, servo.SERVO_FREQ_HZ)
        print(f"    tx_servo -> {rc2}")
        time.sleep(3)
        lgpio.tx_servo(handle, pin, 0)
        results["tx_servo"] = ask("Did the servo move between the two positions?")
        print()

    finally:
        try:
            lgpio.tx_servo(handle, pin, 0)
            lgpio.gpio_free(handle, pin)
        except Exception:
            pass
        lgpio.gpiochip_close(handle)

    # ---- verdict -------------------------------------------------------
    print("=" * 60)
    print("RESULT:")
    if results.get("tx_servo"):
        print("  tx_servo works. The library is fine - the bug was in how the")
        print("  project code called it (not claiming the pin, or exiting before")
        print("  pulses were sent). Both are fixed in servo.py now.")
    elif results.get("bitbang"):
        print("  Hand-rolled pulses move the servo, but lgpio.tx_servo does not.")
        print("  Wiring/power/pin/permissions are all PROVEN GOOD. The fault is")
        print("  specific to tx_servo on this lgpio build.")
        print("  -> Fix: drive the servos with the bit-bang path instead of tx_servo.")
    elif results.get("toggle"):
        print("  The pin reaches the motor electrically, but no pulse train moves it.")
        print("  Suspect the servo itself, or that the driver board is passing the")
        print("  signal but the servo's 5V rail cannot supply enough current to move")
        print("  under load. Check: servo powered from the 5V supply (NOT the Pi's")
        print("  3.3v/5v pin), and Pi GND tied to the supply GND (common ground).")
    else:
        print("  No response at any layer. This is electrical, not software:")
        print("  - signal wire on physical pin 32 for BCM 12?")
        print("  - common ground between Pi and the servo's 5V supply?")
        print("  - 5V actually present at the servo connector under load?")
        print("  - try a known-good servo")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Isolate where servo control is failing.")
    ap.add_argument("--pin", type=int, default=12, help="BCM pin to test (default 12)")
    args = ap.parse_args()
    main(args.pin)
