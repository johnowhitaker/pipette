#!/usr/bin/env python3
"""Read and report present position for a Dynamixel servo, leaving torque off."""

import argparse

from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

# Default connection settings (match wiggle.py)
DEV = "/dev/ttyACM0"
BAUD = 1_000_000
DXL_ID = 1

# Control table addresses (Protocol 2.0, X-series/XL330-style defaults)
ADDR_TORQUE_ENABLE = 64
ADDR_PRESENT_POSITION = 132

# Position units: assume 0..4095 maps to 0..360 degrees (adjust if needed)
TICKS_PER_REV = 4096
DEGREES_PER_REV = 360.0


def ticks_to_deg(ticks: int) -> float:
    return (ticks % TICKS_PER_REV) * (DEGREES_PER_REV / TICKS_PER_REV)


def dxl_write_1(packet, port, dxl_id, addr, value):
    res = packet.write1ByteTxRx(port, dxl_id, addr, value)
    # Some SDK bindings return (comm, err) instead of (_, comm, err).
    if len(res) == 2:
        comm, err = res
    else:
        _, comm, err = res
    return comm, err


def dxl_read_4(packet, port, dxl_id, addr):
    val, comm, err = packet.read4ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def main() -> int:
    parser = argparse.ArgumentParser(description="Read Dynamixel present position with torque off.")
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

    # Ensure torque is off and leave it off
    dxl_write_1(packet, port, args.id, ADDR_TORQUE_ENABLE, 0)

    pos, comm, err = dxl_read_4(packet, port, args.id, ADDR_PRESENT_POSITION)
    if comm != COMM_SUCCESS:
        print(f"ID {args.id}: read failed (comm={comm}, err={err})")
        port.closePort()
        return 1

    print(f"ID {args.id}: present_position={pos} ({ticks_to_deg(pos):.1f} deg)")

    port.closePort()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
