#!/usr/bin/env python3
"""Disable torque on a Dynamixel servo and report the result."""

import argparse

from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

# Default connection settings (match wiggle.py)
DEV = "/dev/ttyACM0"
BAUD = 1_000_000
DXL_ID = 1

# Control table addresses (Protocol 2.0, X-series/XL330-style defaults)
ADDR_TORQUE_ENABLE = 64


def dxl_write_1(packet, port, dxl_id, addr, value):
    res = packet.write1ByteTxRx(port, dxl_id, addr, value)
    # Some SDK bindings return (comm, err) instead of (_, comm, err).
    if len(res) == 2:
        comm, err = res
    else:
        _, comm, err = res
    return comm, err


def dxl_read_1(packet, port, dxl_id, addr):
    val, comm, err = packet.read1ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def main() -> int:
    parser = argparse.ArgumentParser(description="Disable Dynamixel torque.")
    parser.add_argument("--dev", default=DEV, help=f"Serial device (default: {DEV})")
    parser.add_argument("--baud", type=int, default=BAUD, help=f"Baud rate (default: {BAUD})")
    parser.add_argument("--id", type=int, default=DXL_ID, help=f"Dynamixel ID (default: {DXL_ID})")
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

    comm, err = dxl_write_1(packet, port, args.id, ADDR_TORQUE_ENABLE, 0)
    if comm != COMM_SUCCESS:
        print(f"ID {args.id}: torque off write failed (comm={comm}, err={err})")
        port.closePort()
        return 1

    val, rcomm, rerr = dxl_read_1(packet, port, args.id, ADDR_TORQUE_ENABLE)
    if rcomm != COMM_SUCCESS:
        print(f"ID {args.id}: torque read failed (comm={rcomm}, err={rerr})")
        port.closePort()
        return 1

    state = "OFF" if val == 0 else "ON"
    print(f"ID {args.id}: torque={state} (readback={val})")

    port.closePort()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
