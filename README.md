# Pipette Dynamixel Control (Waveshare)

This repo contains a minimal setup for talking to two Dynamixel servos connected to a Waveshare board via `/dev/ttyACM0`. We discovered two servos at **IDs 1 and 2**, and **ID 1** is the one to use going forward.

## Hardware Summary
- Controller: Waveshare board (USB serial on `/dev/ttyACM0`)
- Servos: Dynamixel protocol 2.0 devices
- Known IDs: `1` and `2`
- Preferred ID: `1`
- Baud rate: `1,000,000`

## Python Environment
A local virtual environment is used to avoid system Python changes.

```bash
python3 -m venv /home/johno/pipette/.venv
/home/johno/pipette/.venv/bin/pip install dynamixel-sdk
```

## Quick Test: Wiggle
The `wiggle.py` script reads the current position and then moves +10° / -10° / back to start, one servo at a time, with torque disabled at the end.

```bash
/home/johno/pipette/.venv/bin/python /home/johno/pipette/wiggle.py --yes
```

Optional flags:
- `--yes` skips the motion confirmation prompt.
- `--force` bypasses the position-mode check.

## Read Position (Torque Off)
The `read_position.py` script reads present position for a single servo and explicitly leaves torque disabled.

```bash
/home/johno/pipette/.venv/bin/python /home/johno/pipette/read_position.py
```

Optional flags:
- `--id` set Dynamixel ID (default 1).
- `--dev` set serial device (default `/dev/ttyACM0`).
- `--baud` set baud rate (default `1_000_000`).

## Torque Off
The `torque_off.py` script explicitly disables torque and reads back the torque state.

```bash
/home/johno/pipette/.venv/bin/python /home/johno/pipette/torque_off.py
```

Optional flags:
- `--id` set Dynamixel ID (default 1).
- `--dev` set serial device (default `/dev/ttyACM0`).
- `--baud` set baud rate (default `1_000_000`).

## Cycle Between Two Positions
The `cycle_positions.py` script alternates between two goal positions until interrupted. On Ctrl-C, it disables torque.

```bash
/home/johno/pipette/.venv/bin/python /home/johno/pipette/cycle_positions.py
```

Defaults:
- `--pos-a 1192` and `--pos-b 1131` (from recent readings)
- `--wait 1.0` seconds
- gentle profile: `--velocity 20`, `--accel 5`

Optional flags:
- `--pos-a`, `--pos-b` set goal positions (ticks).
- `--wait` time to wait at each position (seconds).
- `--velocity`, `--accel` set profile values.
- `--id`, `--dev`, `--baud` set target ID and connection settings.

## Example: Read Position and Move ID 1
Below is a minimal, self-contained example that reads the current position and then moves to an absolute goal position. It assumes **Protocol 2.0** and a 0–4095 position range (360° per rev). Adjust control table addresses or scaling if your model differs.

```python
#!/usr/bin/env python3
import time
from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

DEV = "/dev/ttyACM0"
BAUD = 1_000_000
DXL_ID = 1

ADDR_TORQUE_ENABLE = 64
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132

TICKS_PER_REV = 4096
DEGREES_PER_REV = 360.0

def ticks_to_deg(ticks: int) -> float:
    return (ticks % TICKS_PER_REV) * (DEGREES_PER_REV / TICKS_PER_REV)

port = PortHandler(DEV)
if not port.openPort():
    raise SystemExit(f"Failed to open {DEV}")
if not port.setBaudRate(BAUD):
    raise SystemExit(f"Failed to set baud {BAUD}")

packet = PacketHandler(2.0)

# Read present position
pos, comm, err = packet.read4ByteTxRx(port, DXL_ID, ADDR_PRESENT_POSITION)
if comm != COMM_SUCCESS:
    raise SystemExit(f"Read failed (comm={comm}, err={err})")
print(f"Present: {pos} ticks ({ticks_to_deg(pos):.1f} deg)")

# Configure gentle motion
packet.write4ByteTxRx(port, DXL_ID, ADDR_PROFILE_ACCELERATION, 5)
packet.write4ByteTxRx(port, DXL_ID, ADDR_PROFILE_VELOCITY, 20)

# Enable torque, move, then disable torque
packet.write1ByteTxRx(port, DXL_ID, ADDR_TORQUE_ENABLE, 1)
packet.write4ByteTxRx(port, DXL_ID, ADDR_GOAL_POSITION, 2048)  # ~180 deg

# Allow time to move
time.sleep(1.0)

packet.write1ByteTxRx(port, DXL_ID, ADDR_TORQUE_ENABLE, 0)
port.closePort()
```

## Notes
- The Dynamixel SDK return values can differ by binding/version. In `wiggle.py`, we handle both 2‑tuple and 3‑tuple return signatures for write calls.
- If you ever need to scan for IDs again, use a **read-only** ping scan at 1,000,000 baud.
- This setup intentionally keeps movement explicit to avoid accidental motion.
