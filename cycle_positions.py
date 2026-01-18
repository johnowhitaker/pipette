#!/usr/bin/env python3
"""Cycle a Dynamixel between two goal positions until interrupted."""

import argparse
import time

from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

# Default connection settings (match wiggle.py)
DEV = "/dev/ttyACM0"
BAUD = 1_000_000
DXL_ID = 1

# Control table addresses (Protocol 2.0, X-series/XL330-style defaults)
ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Cycle between two positions until Ctrl-C.")
    parser.add_argument("--dev", default=DEV, help=f"Serial device (default: {DEV})")
    parser.add_argument("--baud", type=int, default=BAUD, help=f"Baud rate (default: {BAUD})")
    parser.add_argument("--id", type=int, default=DXL_ID, help=f"Dynamixel ID (default: {DXL_ID})")
    parser.add_argument("--pos-a", type=int, default=1192, help="First goal position (ticks)")
    parser.add_argument("--pos-b", type=int, default=1131, help="Second goal position (ticks)")
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds to wait at each position")
    parser.add_argument("--velocity", type=int, default=20, help="Profile velocity (optional)")
    parser.add_argument("--accel", type=int, default=5, help="Profile acceleration (optional)")
    args = parser.parse_args()

    port = PortHandler(args.dev)
    if not port.openPort():
        print(f"Failed to open {args.dev}")
        return 1
    if not port.setBaudRate(args.baud):
        print(f"Failed to set baud {args.baud}")
        port.closePort()
        return 1

    packet = PacketHandler(2.0)

    # Best-effort gentle profile settings.
    dxl_write_4(packet, port, args.id, ADDR_PROFILE_ACCELERATION, args.accel)
    dxl_write_4(packet, port, args.id, ADDR_PROFILE_VELOCITY, args.velocity)

    # Enable torque for motion.
    comm, err = dxl_write_1(packet, port, args.id, ADDR_TORQUE_ENABLE, 1)
    if comm != COMM_SUCCESS:
        print(f"ID {args.id}: torque on failed (comm={comm}, err={err})")
        port.closePort()
        return 1

    positions = [args.pos_a, args.pos_b]
    idx = 0
    try:
        print("Cycling positions. Press Ctrl-C to stop.")
        while True:
            goal = positions[idx % 2]
            dxl_write_4(packet, port, args.id, ADDR_GOAL_POSITION, goal)
            time.sleep(args.wait)
            idx += 1
    except KeyboardInterrupt:
        print("\nStopping. Disabling torque...")
    finally:
        dxl_write_1(packet, port, args.id, ADDR_TORQUE_ENABLE, 0)
        port.closePort()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
