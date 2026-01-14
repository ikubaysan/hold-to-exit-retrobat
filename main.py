import time
import subprocess
from dataclasses import dataclass
from typing import Dict, Set, Optional

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
POLL_HZ = 60  # loop frequency
ACTION_COOLDOWN_SECONDS = 5.0  # prevents rapid re-triggering while still holding


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
    needle = f"\"{process_name}\""
    return needle.lower() in output.lower()


def kill_process_by_name(process_name: str) -> bool:
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


def on_hold_action(trigger_btn: ButtonInput) -> None:
    print(f"[action] Triggered by holding {trigger_btn} for {HOLD_SECONDS:.2f}s. Killing configured processes if found...")
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
    pygame.event.pump()


def read_current_pressed_buttons(joysticks: Dict[int, pygame.joystick.Joystick]) -> Set[ButtonInput]:
    pressed: Set[ButtonInput] = set()
    for jid, js in joysticks.items():
        btn_count = js.get_numbuttons()
        for b in range(btn_count):
            if js.get_button(b):
                pressed.add(ButtonInput(jid, b))
    return pressed


def collect_buttons_to_trigger(joysticks: Dict[int, pygame.joystick.Joystick]) -> Set[ButtonInput]:
    import threading

    print("\n[setup] Press any buttons on any controller to add them as individual triggers.")
    print("[setup] OR logic: holding ANY chosen button for the hold duration will trigger the action.")
    print("[setup] Press ENTER in this console when you're done selecting.\n")

    chosen: Set[ButtonInput] = set()
    last_printed: Set[ButtonInput] = set()

    done_event = threading.Event()

    def wait_for_enter():
        try:
            input()
            done_event.set()
        except Exception:
            pass

    threading.Thread(target=wait_for_enter, daemon=True).start()

    print("[setup] Waiting for button presses... (Press ENTER to finish)")

    try:
        while not done_event.is_set():
            pump_events_nonblocking()

            pressed_now = read_current_pressed_buttons(joysticks)
            new_presses = pressed_now - chosen
            if new_presses:
                for btn in sorted(new_presses, key=lambda x: (x.joystick_id, x.button_index)):
                    chosen.add(btn)
                    print(f"[setup] Added trigger button: {btn}")

            if chosen != last_printed:
                last_printed = set(chosen)
                if chosen:
                    pretty = ", ".join(str(b) for b in sorted(chosen, key=lambda x: (x.joystick_id, x.button_index)))
                    print(f"[setup] Current trigger set: {pretty}")
                else:
                    print("[setup] Current trigger set: (none)")

            time.sleep(1.0 / POLL_HZ)

    except KeyboardInterrupt:
        print("\n[setup] Ctrl+C detected during setup. Exiting cleanly.")
        raise

    print("[setup] ENTER pressed. Finishing selection.\n")

    if not chosen:
        print("[setup] WARNING: You didn't select any buttons. Monitoring will never trigger.")
    else:
        pretty = ", ".join(str(b) for b in sorted(chosen, key=lambda x: (x.joystick_id, x.button_index)))
        print(f"[setup] Final trigger buttons: {pretty}\n")

    return chosen


def monitor_triggers_forever(joysticks: Dict[int, pygame.joystick.Joystick], triggers: Set[ButtonInput]) -> None:
    print(f"[monitor] OR-mode monitoring: hold ANY chosen button for {HOLD_SECONDS:.1f}s to trigger.")
    print(f"[monitor] Cooldown after trigger: {ACTION_COOLDOWN_SECONDS:.1f}s")
    print("[monitor] Press Ctrl+C to exit.\n")

    # For each trigger button: when did we start holding it (monotonic time)?
    hold_start_by_btn: Dict[ButtonInput, float] = {}

    # Per-button cooldown timestamp: next time this button is allowed to trigger
    next_allowed_trigger_by_btn: Dict[ButtonInput, float] = {}

    # For logging throttling (avoid spam)
    last_hold_log_bucket_by_btn: Dict[ButtonInput, int] = {}

    while True:
        pump_events_nonblocking()
        now = time.monotonic()
        pressed_now = read_current_pressed_buttons(joysticks)

        # Update each trigger button independently
        for btn in triggers:
            is_pressed = btn in pressed_now

            if is_pressed:
                if btn not in hold_start_by_btn:
                    hold_start_by_btn[btn] = now
                    last_hold_log_bucket_by_btn.pop(btn, None)
                    print(f"[monitor] {btn} pressed. Starting hold timer...")

                elapsed = now - hold_start_by_btn[btn]

                # Log at ~4Hz while holding (bucketed)
                bucket = int(elapsed * 4)
                if last_hold_log_bucket_by_btn.get(btn) != bucket:
                    last_hold_log_bucket_by_btn[btn] = bucket
                    print(f"[monitor] Holding {btn}... {elapsed:.2f}/{HOLD_SECONDS:.2f}s")

                # Trigger if held long enough and not in cooldown
                next_allowed = next_allowed_trigger_by_btn.get(btn, 0.0)
                if elapsed >= HOLD_SECONDS and now >= next_allowed:
                    print(f"[monitor] {btn} held for {elapsed:.2f}s (>= {HOLD_SECONDS:.2f}s). Triggering action!")
                    on_hold_action(btn)
                    next_allowed_trigger_by_btn[btn] = now + ACTION_COOLDOWN_SECONDS

            else:
                if btn in hold_start_by_btn:
                    # Button released -> reset timer
                    print(f"[monitor] {btn} released/reset.")
                    hold_start_by_btn.pop(btn, None)
                    last_hold_log_bucket_by_btn.pop(btn, None)

        time.sleep(1.0 / POLL_HZ)


def main() -> int:
    joysticks = init_pygame_and_joysticks()
    if not joysticks:
        print("[main] No controllers available. Exiting.")
        return 1

    triggers = collect_buttons_to_trigger(joysticks)

    try:
        monitor_triggers_forever(joysticks, triggers)
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
