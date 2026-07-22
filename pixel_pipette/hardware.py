"""Hardware access with a motion-free simulator used by default."""

from __future__ import annotations

import re
import threading
import time
from typing import Any


class HardwareError(RuntimeError):
    pass


POSITION_RE = re.compile(r"\b([XYZ]):\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
MOVE_RE = re.compile(r"\b([XYZ])\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)


class HardwareController:
    def __init__(self, mode: str = "simulate"):
        self.mode = mode if mode in {"simulate", "real"} else "simulate"
        self.lock = threading.RLock()
        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._servo_position = 1100
        self._steppers_enabled = False
        self.command_log: list[str] = []

    @property
    def simulated(self) -> bool:
        return self.mode != "real"

    @staticmethod
    def _device_config(config: dict[str, Any]) -> dict[str, Any]:
        return config["devices"]

    def list_ports(self) -> list[dict[str, str]]:
        if self.simulated:
            return [
                {"device": "/dev/ttyUSB0", "description": "Simulated printer", "hwid": "SIM"},
                {"device": "/dev/ttyACM0", "description": "Simulated servo", "hwid": "SIM"},
            ]
        try:
            from serial.tools import list_ports
        except ImportError as exc:
            raise HardwareError("pyserial is not installed") from exc
        return [
            {"device": item.device, "description": item.description, "hwid": item.hwid}
            for item in list_ports.comports()
        ]

    def send_gcode(self, config: dict[str, Any], gcode: str, wait: bool = True) -> list[str]:
        with self.lock:
            if self.simulated:
                return self._simulate_gcode(gcode)
            try:
                import serial
            except ImportError as exc:
                raise HardwareError("pyserial is not installed") from exc

            devices = self._device_config(config)
            timeout_s = float(config["motion"]["command_timeout_s"])
            try:
                connection = serial.Serial(
                    port=devices["printer_port"],
                    baudrate=int(devices["printer_baud"]),
                    timeout=1.0,
                    write_timeout=timeout_s,
                )
            except Exception as exc:
                raise HardwareError(f"Could not open printer at {devices['printer_port']}: {exc}") from exc

            responses: list[str] = []
            try:
                connection.reset_input_buffer()
                for command in [line.strip() for line in gcode.splitlines() if line.strip()]:
                    connection.write((command + "\n").encode())
                    connection.flush()
                    self._read_until_ack(connection, time.monotonic() + timeout_s, responses)
                if wait:
                    connection.write(b"M400\n")
                    connection.flush()
                    self._read_until_ack(connection, time.monotonic() + timeout_s, responses)
            finally:
                connection.close()
            self.command_log.append(gcode)
            return responses

    @staticmethod
    def _read_until_ack(connection: Any, deadline: float, responses: list[str]) -> None:
        while time.monotonic() < deadline:
            raw = connection.readline()
            if not raw:
                continue
            text = raw.decode(errors="replace").strip()
            if text:
                responses.append(text)
            if text.lower().startswith("error"):
                raise HardwareError(f"Printer reported: {text}")
            if text.lower().startswith("ok"):
                return
        raise HardwareError("Printer did not acknowledge the command before the timeout")

    def _simulate_gcode(self, gcode: str) -> list[str]:
        relative = False
        responses: list[str] = []
        for command in [line.strip() for line in gcode.splitlines() if line.strip()]:
            upper = command.upper()
            self.command_log.append(command)
            if upper.startswith("G91"):
                relative = True
            elif upper.startswith("G90"):
                relative = False
            elif upper.startswith("G92"):
                for axis, value in MOVE_RE.findall(upper):
                    self._position[axis.lower()] = float(value)
            elif upper.startswith(("G0 ", "G1 ")):
                for axis, value in MOVE_RE.findall(upper):
                    key = axis.lower()
                    number = float(value)
                    self._position[key] = self._position[key] + number if relative else number
            elif upper.startswith("M84"):
                self._steppers_enabled = False
            elif upper.startswith("M17"):
                self._steppers_enabled = True
            elif upper.startswith("M114"):
                responses.append(
                    f"X:{self._position['x']:.3f} Y:{self._position['y']:.3f} Z:{self._position['z']:.3f}"
                )
            responses.append("ok")
        return responses

    def read_position(self, config: dict[str, Any]) -> dict[str, float]:
        if self.simulated:
            return dict(self._position)
        responses = self.send_gcode(config, "M114", wait=False)
        position: dict[str, float] = {}
        for line in responses:
            # Marlin may append raw step counts after "Count"; only the first
            # coordinate triplet is the position in millimetres.
            coordinate_text = line.split("Count", 1)[0]
            for axis, value in POSITION_RE.findall(coordinate_text):
                position.setdefault(axis.lower(), float(value))
        if not all(axis in position for axis in ("x", "y", "z")):
            raise HardwareError(f"Could not parse printer position from: {' | '.join(responses)}")
        return position

    def test_printer(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"connected": True, "position": self.read_position(config), "simulated": self.simulated}

    def disable_steppers(self, config: dict[str, Any]) -> None:
        self.send_gcode(config, "M84", wait=False)

    def enable_steppers(self, config: dict[str, Any]) -> None:
        self.send_gcode(config, "M17", wait=False)

    def set_origin(self, config: dict[str, Any]) -> dict[str, float]:
        # Lock the axes after the operator has manually placed the head.
        self.send_gcode(config, "G92 X0 Y0 Z0\nM17", wait=False)
        return self.read_position(config)

    def jog(self, config: dict[str, Any], axis: str, distance: float) -> dict[str, float]:
        axis = axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise HardwareError("Jog axis must be X, Y, or Z")
        feed = float(config["motion"]["z_feed"] if axis == "Z" else config["motion"]["jog_feed"])
        self.send_gcode(config, f"G91\nG1 {axis}{distance:.3f} F{feed:.0f}\nG90", wait=True)
        return self.read_position(config)

    def move(
        self,
        config: dict[str, Any],
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
    ) -> None:
        parts = ["G1"]
        if x is not None:
            parts.append(f"X{x:.3f}")
        if y is not None:
            parts.append(f"Y{y:.3f}")
        if z is not None:
            parts.append(f"Z{z:.3f}")
        feed = (
            config["motion"]["z_feed"]
            if z is not None and x is None and y is None
            else config["motion"]["xy_feed"]
        )
        parts.append(f"F{float(feed):.0f}")
        self.send_gcode(config, " ".join(parts), wait=True)

    @staticmethod
    def _dxl_write_1(packet: Any, port: Any, dxl_id: int, address: int, value: int) -> tuple[int, int]:
        result = packet.write1ByteTxRx(port, dxl_id, address, value)
        return (result[-2], result[-1])

    @staticmethod
    def _dxl_write_4(packet: Any, port: Any, dxl_id: int, address: int, value: int) -> tuple[int, int]:
        result = packet.write4ByteTxRx(port, dxl_id, address, value)
        return (result[-2], result[-1])

    def _with_servo(self, config: dict[str, Any], callback: Any) -> Any:
        try:
            from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler
        except ImportError as exc:
            raise HardwareError("dynamixel-sdk is not installed") from exc
        devices = self._device_config(config)
        port = PortHandler(devices["servo_port"])
        if not port.openPort():
            raise HardwareError(f"Could not open servo at {devices['servo_port']}")
        try:
            if not port.setBaudRate(int(devices["servo_baud"])):
                raise HardwareError(f"Could not set servo baud to {devices['servo_baud']}")
            return callback(PacketHandler(2.0), port, int(devices["servo_id"]), COMM_SUCCESS)
        finally:
            port.closePort()

    def read_servo_position(self, config: dict[str, Any]) -> int:
        with self.lock:
            if self.simulated:
                return self._servo_position

            def read(packet: Any, port: Any, dxl_id: int, success: int) -> int:
                position, comm, error = packet.read4ByteTxRx(port, dxl_id, 132)
                if comm != success or error:
                    raise HardwareError(f"Servo position read failed (comm={comm}, error={error})")
                return int(position)

            return self._with_servo(config, read)

    def test_servo(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"connected": True, "position": self.read_servo_position(config), "simulated": self.simulated}

    def move_servo(self, config: dict[str, Any], goal: int) -> int:
        with self.lock:
            if self.simulated:
                self._servo_position = int(goal)
                self.command_log.append(f"SERVO {goal}")
                return self._servo_position

            def move(packet: Any, port: Any, dxl_id: int, success: int) -> int:
                servo = config["servo"]
                for address, value in ((108, servo["acceleration"]), (112, servo["velocity"])):
                    comm, error = self._dxl_write_4(packet, port, dxl_id, address, int(value))
                    if comm != success or error:
                        raise HardwareError(f"Servo profile write failed (comm={comm}, error={error})")
                comm, error = self._dxl_write_1(packet, port, dxl_id, 64, 1)
                if comm != success or error:
                    raise HardwareError(f"Servo torque enable failed (comm={comm}, error={error})")
                comm, error = self._dxl_write_4(packet, port, dxl_id, 116, int(goal))
                if comm != success or error:
                    raise HardwareError(f"Servo move failed (comm={comm}, error={error})")
                return int(goal)

            result = self._with_servo(config, move)
            self.command_log.append(f"SERVO {goal}")
            return result

    def disable_servo_torque(self, config: dict[str, Any]) -> None:
        with self.lock:
            if self.simulated:
                self.command_log.append("SERVO TORQUE OFF")
                return

            def disable(packet: Any, port: Any, dxl_id: int, success: int) -> None:
                comm, error = self._dxl_write_1(packet, port, dxl_id, 64, 0)
                if comm != success or error:
                    raise HardwareError(f"Servo torque disable failed (comm={comm}, error={error})")

            self._with_servo(config, disable)

    def quick_stop(self, config: dict[str, Any]) -> None:
        self.send_gcode(config, "M410", wait=False)
