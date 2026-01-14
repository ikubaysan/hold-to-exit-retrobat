import sys
import time
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import pygame

# -------------------------
# CONFIG
# -------------------------
PROCESS_NAMES_TO_KILL = [
    "TeknoParrotUi.exe",
    "retroarch.exe",
    "fbneo64.exe",
]

HOLD_SECONDS = 3.0
POLL_HZ = 60  # loop frequency (keep modest for CPU)


# -------------------------
# DATA TYPES
# -------------------------
@dataclass(frozen=True)
class ButtonInput:
    joystick_id: int
    button_index: int

    def __str__(self) -> str:
        return f"Joy{self.joystick_id}:Btn{self.button_index}"


# -------------------------
# PROCESS CONTROL
# -------------------------
def _run_tasklist_csv() -> str:
    # /FO CSV makes parsing more reliable, /NH removes header line
    # Note: tasklist is built-in on Windows.
    cp = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    return cp.stdout


def is_process_running(process_name: str) -> bool:
    output = _run_tasklist_csv()
    # Each line is like: "Image Name","PID","Session Name","Session#","Mem Usage"
    # We just search for the exact "process_name" token.
    needle = f"\"{process_name}\""
    return needle.lower() in output.lower()


def kill_process_by_name(process_name: str) -> bool:
    """
    Returns True if we *attempted* to kill something (i.e., it looked running),
    False if it wasn't found.
    """
    if not is_process_running(process_name):
        print(f"[kill] Not running: {process_name}")
        return False

    print(f"[kill] Attempting to kill: {process_name}")
    cp = subprocess.run(
        ["taskkill", "/IM", process_name, "/F"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )

    if cp.returncode == 0:
        print(f"[kill] Killed: {process_name}")
    else:
        print(f"[kill] taskkill failed for {process_name} (code {cp.returncode})")
        if cp.stdout.strip():
            print(f"[kill] stdout: {cp.stdout.strip()}")
        if cp.stderr.strip():
            print(f"[kill] stderr: {cp.stderr.strip()}")

    return True


def on_combo_held_action() -> None:
    print("[action] Combo held long enough. Killing configured processes if found...")
    for name in PROCESS_NAMES_TO_KILL:
        try:
            kill_process_by_name(name)
        except Exception as e:
            print(f"[action] ERROR killing {name}: {e}")
    print("[action] Done.\n")


# -------------------------
# CONTROLLER INPUT
# -------------------------
def init_pygame_and_joysticks() -> Dict[int, pygame.joystick.Joystick]:
    pygame.init()
    pygame.joystick.init()

    joysticks: Dict[int, pygame.joystick.Joystick] = {}
    count = pygame.joystick.get_count()
    print(f"[init] Detected {count} controller(s).")

    for i in range(count):
        js = pygame.joystick.Joystick(i)
        js.init()
        joysticks[i] = js
        print(f"[init] Joy{i}: name='{js.get_name()}', buttons={js.get_numbuttons()}")

    if count == 0:
        print("[init] No controllers detected. You can plug one in and restart.")

    return joysticks


def pump_events_nonblocking() -> None:
    # Keeps pygame's internal state updated and processes OS events.
    # We intentionally do NOT block in event.wait().
    pygame.event.pump()


def read_current_pressed_buttons(joysticks: Dict[int, pygame.joystick.Joystick]) -> Set[ButtonInput]:
    pressed: Set[ButtonInput] = set()
    for jid, js in joysticks.items():
        btn_count = js.get_numbuttons()
        for b in range(btn_count):
            if js.get_button(b):
                pressed.add(ButtonInput(jid, b))
    return pressed


def button_name_hint(btn: ButtonInput) -> str:
    # Pygame only gives index. We can at least show controller name.
    # (Different controllers map face buttons differently.)
    return str(btn)




def collect_combo_inputs(joysticks: Dict[int, pygame.joystick.Joystick]) -> Set[ButtonInput]:
    import threading

    print("\n[setup] Press any buttons on any controller to add them to the combo.")
    print("[setup] Press ENTER in this console when you're done selecting.\n")

    chosen: Set[ButtonInput] = set()
    last_printed: Set[ButtonInput] = set()

    done_event = threading.Event()

    def wait_for_enter():
        # Blocks until user presses Enter, but runs in a daemon thread
        try:
            input()
            done_event.set()
        except Exception:
            # If stdin is unavailable, just never set; main loop still Ctrl+C-able
            pass

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    print("[setup] Waiting for button presses... (Press ENTER to finish)")

    try:
        while not done_event.is_set():
            pump_events_nonblocking()

            pressed_now = read_current_pressed_buttons(joysticks)
            new_presses = pressed_now - chosen
            if new_presses:
                for btn in sorted(new_presses, key=lambda x: (x.joystick_id, x.button_index)):
                    chosen.add(btn)
                    print(f"[setup] Added: {button_name_hint(btn)}")

            if chosen != last_printed:
                last_printed = set(chosen)
                if chosen:
                    pretty = ", ".join(
                        button_name_hint(b)
                        for b in sorted(chosen, key=lambda x: (x.joystick_id, x.button_index))
                    )
                    print(f"[setup] Current combo set: {pretty}")
                else:
                    print("[setup] Current combo set: (none)")

            time.sleep(1.0 / POLL_HZ)

    except KeyboardInterrupt:
        print("\n[setup] Ctrl+C detected during setup. Exiting cleanly.")
        raise

    print("[setup] ENTER pressed. Finishing selection.\n")

    if not chosen:
        print("[setup] WARNING: You didn't select any buttons. Monitoring will never trigger.")
    else:
        pretty = ", ".join(
            button_name_hint(b)
            for b in sorted(chosen, key=lambda x: (x.joystick_id, x.button_index))
        )
        print(f"[setup] Final chosen combo: {pretty}\n")

    return chosen


def monitor_combo_forever(joysticks: Dict[int, pygame.joystick.Joystick], combo: Set[ButtonInput]) -> None:
    print(f"[monitor] Monitoring for combo hold: {HOLD_SECONDS:.1f}s")
    print("[monitor] Press Ctrl+C to exit.\n")

    hold_start: float | None = None
    triggered = False  # prevents repeat spam while still holding

    while True:
        pump_events_nonblocking()
        pressed_now = read_current_pressed_buttons(joysticks)

        if combo and combo.issubset(pressed_now):
            if hold_start is None:
                hold_start = time.monotonic()
                triggered = False
                print(f"[monitor] Combo pressed. Starting hold timer...")
            else:
                elapsed = time.monotonic() - hold_start
                # Be verbose but not insane: print at ~4 Hz while holding
                if int(elapsed * 4) != int((elapsed - (1.0 / POLL_HZ)) * 4):
                    print(f"[monitor] Holding... {elapsed:.2f}/{HOLD_SECONDS:.2f}s")

                if (not triggered) and elapsed >= HOLD_SECONDS:
                    print(f"[monitor] Held for {elapsed:.2f}s (>= {HOLD_SECONDS:.2f}s). Triggering action!")
                    on_combo_held_action()
                    triggered = True
        else:
            if hold_start is not None:
                print("[monitor] Combo released/reset.")
            hold_start = None
            triggered = False

        time.sleep(1.0 / POLL_HZ)


def main() -> int:
    joysticks = init_pygame_and_joysticks()
    if not joysticks:
        print("[main] No controllers available. Exiting.")
        return 1

    combo = collect_combo_inputs(joysticks)

    try:
        monitor_combo_forever(joysticks, combo)
    except KeyboardInterrupt:
        print("\n[main] Ctrl+C received. Shutting down cleanly...")
    finally:
        try:
            pygame.joystick.quit()
            pygame.quit()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
