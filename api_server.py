#!/usr/bin/env python3
"""FastAPI server for printer G-code and Dynamixel servo control."""

from __future__ import annotations

import threading
import time
from typing import List, Optional

import serial
from serial.tools import list_ports
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dynamixel_sdk import PortHandler, PacketHandler, COMM_SUCCESS

app = FastAPI(title="Pipette Control API")

# --- Dynamixel settings (match existing scripts) ---
DXL_DEV_DEFAULT = "/dev/ttyACM0"
DXL_BAUD_DEFAULT = 1_000_000
DXL_ID_DEFAULT = 1

ADDR_TORQUE_ENABLE = 64
ADDR_OPERATING_MODE = 11
ADDR_PROFILE_ACCELERATION = 108
ADDR_PROFILE_VELOCITY = 112
ADDR_GOAL_POSITION = 116
ADDR_PRESENT_POSITION = 132

TICKS_PER_REV = 4096
DEGREES_PER_REV = 360.0

# --- Printer defaults ---
PRINTER_PORT_DEFAULT = "/dev/ttyUSB0"
PRINTER_BAUD_DEFAULT = 115200

printer_lock = threading.Lock()
servo_lock = threading.Lock()


# ----- Helper functions -----

def ticks_to_deg(ticks: int) -> float:
    return (ticks % TICKS_PER_REV) * (DEGREES_PER_REV / TICKS_PER_REV)


def dxl_write_1(packet, port, dxl_id, addr, value):
    res = packet.write1ByteTxRx(port, dxl_id, addr, value)
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


def dxl_read_1(packet, port, dxl_id, addr):
    val, comm, err = packet.read1ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def dxl_read_4(packet, port, dxl_id, addr):
    val, comm, err = packet.read4ByteTxRx(port, dxl_id, addr)
    return val, comm, err


def _open_printer(port: str, baud: int, timeout_s: float) -> serial.Serial:
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )
    except serial.SerialException as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open printer port {port}: {exc}")
    return ser


def _read_until_ok(ser: serial.Serial, deadline: float, out_lines: List[str]) -> bool:
    while time.monotonic() < deadline:
        line = ser.readline()
        if not line:
            continue
        text = line.decode(errors="replace").strip()
        if text:
            out_lines.append(text)
        lower = text.lower()
        if lower.startswith("ok") or lower.startswith("error"):
            return True
    return False


def _send_gcode(ser: serial.Serial, gcode: str, timeout_s: float) -> List[str]:
    lines = [ln.strip() for ln in gcode.splitlines() if ln.strip()]
    responses: List[str] = []
    for line in lines:
        ser.write((line + "\n").encode())
        ser.flush()
        _read_until_ok(ser, time.monotonic() + timeout_s, responses)
    return responses


def _wait_for_move_complete(ser: serial.Serial, timeout_s: float, responses: List[str]) -> bool:
    # M400 waits for all moves to finish in Marlin.
    ser.write(b"M400\n")
    ser.flush()
    return _read_until_ok(ser, time.monotonic() + timeout_s, responses)


# ----- Request/response models -----

class SendGcodeRequest(BaseModel):
    gcode: str = Field(..., description="G-code to send; can include multiple lines")
    port: str = Field(PRINTER_PORT_DEFAULT, description="Serial port for the printer")
    baud: int = Field(PRINTER_BAUD_DEFAULT, description="Printer baud rate")
    timeout_s: float = Field(5.0, description="Per-command read timeout in seconds")
    wait_for_move: bool = Field(True, description="Send M400 and wait for completion")


class SendGcodeResponse(BaseModel):
    port: str
    baud: int
    responses: List[str]
    move_completed: Optional[bool] = None


class ServoRequest(BaseModel):
    dev: str = Field(DXL_DEV_DEFAULT, description="Serial device")
    baud: int = Field(DXL_BAUD_DEFAULT, description="Baud rate")
    dxl_id: int = Field(DXL_ID_DEFAULT, description="Dynamixel ID")


class ServoMoveRequest(ServoRequest):
    goal_position: int = Field(..., description="Goal position in ticks")
    velocity: Optional[int] = Field(None, description="Profile velocity (optional)")
    acceleration: Optional[int] = Field(None, description="Profile acceleration (optional)")
    wait: bool = Field(True, description="Wait for servo to reach position")
    wait_timeout_s: float = Field(5.0, description="Max seconds to wait")
    tolerance_ticks: int = Field(10, description="Position tolerance in ticks")


class ServoPositionResponse(BaseModel):
    dxl_id: int
    present_position: int
    present_degrees: float


class ServoTorqueResponse(BaseModel):
    dxl_id: int
    torque_enabled: bool


# ----- Printer endpoints -----

@app.get("/printer/ports")
def list_printer_ports():
    ports = []
    for info in list_ports.comports():
        ports.append({
            "device": info.device,
            "description": info.description,
            "hwid": info.hwid,
        })
    return {"ports": ports}


@app.post("/printer/send_gcode", response_model=SendGcodeResponse)
def send_gcode(req: SendGcodeRequest):
    responses: List[str] = []
    with printer_lock:
        ser = _open_printer(req.port, req.baud, req.timeout_s)
        try:
            # Clear any startup chatter.
            ser.reset_input_buffer()
            responses.extend(_send_gcode(ser, req.gcode, req.timeout_s))
            move_completed = None
            if req.wait_for_move:
                move_completed = _wait_for_move_complete(ser, req.timeout_s, responses)
        finally:
            ser.close()
    return SendGcodeResponse(
        port=req.port,
        baud=req.baud,
        responses=responses,
        move_completed=move_completed,
    )


# ----- Servo endpoints -----

@app.post("/servo/disable_torque", response_model=ServoTorqueResponse)
def servo_disable_torque(req: ServoRequest):
    with servo_lock:
        port = PortHandler(req.dev)
        if not port.openPort():
            raise HTTPException(status_code=500, detail=f"Failed to open {req.dev}")
        if not port.setBaudRate(req.baud):
            port.closePort()
            raise HTTPException(status_code=500, detail=f"Failed to set baud {req.baud}")
        packet = PacketHandler(2.0)
        try:
            comm, err = dxl_write_1(packet, port, req.dxl_id, ADDR_TORQUE_ENABLE, 0)
            if comm != COMM_SUCCESS:
                raise HTTPException(status_code=500, detail=f"Torque off failed (comm={comm}, err={err})")
            val, rcomm, rerr = dxl_read_1(packet, port, req.dxl_id, ADDR_TORQUE_ENABLE)
            if rcomm != COMM_SUCCESS:
                raise HTTPException(status_code=500, detail=f"Torque read failed (comm={rcomm}, err={rerr})")
        finally:
            port.closePort()
    return ServoTorqueResponse(dxl_id=req.dxl_id, torque_enabled=bool(val))


@app.get("/servo/read_position", response_model=ServoPositionResponse)
def servo_read_position(dev: str = DXL_DEV_DEFAULT, baud: int = DXL_BAUD_DEFAULT, dxl_id: int = DXL_ID_DEFAULT):
    with servo_lock:
        port = PortHandler(dev)
        if not port.openPort():
            raise HTTPException(status_code=500, detail=f"Failed to open {dev}")
        if not port.setBaudRate(baud):
            port.closePort()
            raise HTTPException(status_code=500, detail=f"Failed to set baud {baud}")
        packet = PacketHandler(2.0)
        try:
            pos, comm, err = dxl_read_4(packet, port, dxl_id, ADDR_PRESENT_POSITION)
            if comm != COMM_SUCCESS:
                raise HTTPException(status_code=500, detail=f"Read failed (comm={comm}, err={err})")
        finally:
            port.closePort()
    return ServoPositionResponse(
        dxl_id=dxl_id,
        present_position=pos,
        present_degrees=ticks_to_deg(pos),
    )


@app.post("/servo/move", response_model=ServoPositionResponse)
def servo_move(req: ServoMoveRequest):
    with servo_lock:
        port = PortHandler(req.dev)
        if not port.openPort():
            raise HTTPException(status_code=500, detail=f"Failed to open {req.dev}")
        if not port.setBaudRate(req.baud):
            port.closePort()
            raise HTTPException(status_code=500, detail=f"Failed to set baud {req.baud}")
        packet = PacketHandler(2.0)
        try:
            # Best-effort gentle profile settings.
            if req.acceleration is not None:
                dxl_write_4(packet, port, req.dxl_id, ADDR_PROFILE_ACCELERATION, req.acceleration)
            if req.velocity is not None:
                dxl_write_4(packet, port, req.dxl_id, ADDR_PROFILE_VELOCITY, req.velocity)

            # Enable torque for motion.
            comm, err = dxl_write_1(packet, port, req.dxl_id, ADDR_TORQUE_ENABLE, 1)
            if comm != COMM_SUCCESS:
                raise HTTPException(status_code=500, detail=f"Torque on failed (comm={comm}, err={err})")

            comm, err = dxl_write_4(packet, port, req.dxl_id, ADDR_GOAL_POSITION, req.goal_position)
            if comm != COMM_SUCCESS:
                raise HTTPException(status_code=500, detail=f"Goal write failed (comm={comm}, err={err})")

            pos = req.goal_position
            if req.wait:
                deadline = time.monotonic() + req.wait_timeout_s
                while time.monotonic() < deadline:
                    pos, rcomm, rerr = dxl_read_4(packet, port, req.dxl_id, ADDR_PRESENT_POSITION)
                    if rcomm != COMM_SUCCESS:
                        raise HTTPException(status_code=500, detail=f"Read failed (comm={rcomm}, err={rerr})")
                    if abs(pos - req.goal_position) <= req.tolerance_ticks:
                        break
                    time.sleep(0.05)
        finally:
            port.closePort()

    return ServoPositionResponse(
        dxl_id=req.dxl_id,
        present_position=pos,
        present_degrees=ticks_to_deg(pos),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, log_level="info")
