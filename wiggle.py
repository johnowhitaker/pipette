#!/usr/bin/env python3
"""Safe, interactive wiggle for Dynamixel protocol 2.0 servos.

- Reads and prints present position.
- Moves +/- 10 degrees slowly, one at a time.
- Disables torque at the end.

No motion occurs until you type YES at the prompt.
"""

import math
import sys
import time

from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

# --- User-tunable settings ---
DEV = "/dev/ttyACM0"
BAUD = 1_000_000
IDS = [1, 2]

# Control table addresses (Protocol 2.0, X-series/XL330-style defaults)
ADDR_TORQUE_ENABLE = 64
ADDR_OPERATING_MODE = 11
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132

# Position units: assume 0..4095 maps to 0..360 degrees (adjust if needed)
TICKS_PER_REV = 4096
DEGREES_PER_REV = 360.0

# Motion settings
DELTA_DEG = 10.0
PROFILE_VELOCITY = 20   # low and slow; units are model-specific
PROFILE_ACCELERATION = 5
MOVE_SETTLE_S = 0.6

# --- Helpers ---

def ticks_to_deg(ticks: int) -> float:
    return (ticks % TICKS_PER_REV) * (DEGREES_PER_REV / TICKS_PER_REV)


def deg_to_ticks(deg: float) -> int:
    return int(round((deg / DEGREES_PER_REV) * TICKS_PER_REV))


def dxl_write_1(packet, port, dxl_id, addr, value):
    res = packet.write1ByteTxRx(port, dxl_id, addr, value)
    # Some SDK bindings return (comm, err) instead of (_, comm, err).
    if len(res) == 2:
        comm, err = res
    else:
        _, comm, err = res
    return comm, err


def dxl_write_4(packet, port, dxl_id, addr, value):
    res = packet.write4ByteTxRx(port, dxl_id, addr, value)
    if len(res) == 2:
        comm, err = res
    else:
        _, comm, err = res
    return comm, err


def dxl_read_4(packet, port, dxl_id, addr):
    val, comm, err = packet.read4ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def dxl_read_1(packet, port, dxl_id, addr):
    val, comm, err = packet.read1ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def main() -> int:
    port = PortHandler(DEV)
    if not port.openPort():
        print(f"Failed to open {DEV}")
        return 1
    if not port.setBaudRate(BAUD):
        print(f"Failed to set baud {BAUD}")
        port.closePort()
        return 1

    packet = PacketHandler(2.0)

    print("Connected. Reading positions...")
    positions = {}
    modes = {}
    for dxl_id in IDS:
        pos, comm, err = dxl_read_4(packet, port, dxl_id, ADDR_PRESENT_POSITION)
        if comm != COMM_SUCCESS:
            print(f"ID {dxl_id}: read failed (comm={comm}, err={err})")
            continue
        positions[dxl_id] = pos
        mode, mcomm, merr = dxl_read_1(packet, port, dxl_id, ADDR_OPERATING_MODE)
        if mcomm == COMM_SUCCESS:
            modes[dxl_id] = mode
        print(f"ID {dxl_id}: present_position={pos} ({ticks_to_deg(pos):.1f} deg)")

    if not positions:
        print("No positions read; aborting.")
        port.closePort()
        return 1

    if "--yes" not in sys.argv:
        print("\nThis script WILL MOVE the servos by +/- 10 degrees.")
        print("Type YES to proceed, or anything else to exit: ")
        if sys.stdin.readline().strip() != "YES":
            print("Aborted. No motion performed.")
            port.closePort()
            return 0

    # Safety: only proceed if servos are in position control (mode 3) unless forced.
    non_position = [dxl_id for dxl_id, mode in modes.items() if mode != 3]
    if non_position and "--force" not in sys.argv:
        print(f"Warning: IDs not in position mode (3): {non_position}")
        print("Type FORCE to continue anyway, or anything else to exit: ")
        if sys.stdin.readline().strip() != "FORCE":
            print("Aborted. No motion performed.")
            port.closePort()
            return 0

    for dxl_id in IDS:
        if dxl_id not in positions:
            continue

        # Ensure torque is off before configuration
        dxl_write_1(packet, port, dxl_id, ADDR_TORQUE_ENABLE, 0)

        # Try to set gentle profile values (ignore errors; some models may not support)
        dxl_write_4(packet, port, dxl_id, ADDR_PROFILE_ACCELERATION, PROFILE_ACCELERATION)
        dxl_write_4(packet, port, dxl_id, ADDR_PROFILE_VELOCITY, PROFILE_VELOCITY)

        # Enable torque
        dxl_write_1(packet, port, dxl_id, ADDR_TORQUE_ENABLE, 1)

        base = positions[dxl_id]
        delta_ticks = deg_to_ticks(DELTA_DEG)
        goals = [base + delta_ticks, base - delta_ticks, base]

        print(f"\nID {dxl_id}: moving +/-, then back to start")
        for goal in goals:
            # Clamp to 0..(TICKS_PER_REV-1) by wrapping
            goal_wrapped = goal % TICKS_PER_REV
            dxl_write_4(packet, port, dxl_id, ADDR_GOAL_POSITION, goal_wrapped)
            time.sleep(MOVE_SETTLE_S)

        # Disable torque after this servo
        dxl_write_1(packet, port, dxl_id, ADDR_TORQUE_ENABLE, 0)

    port.closePort()
    print("Done. Torque disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
