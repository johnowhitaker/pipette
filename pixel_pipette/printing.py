"""Queue worker and calibrated liquid-drop sequence."""

from __future__ import annotations

import threading
import time
from typing import Any

from .hardware import HardwareController
from .store import Store, utc_now


class PrintConfigurationError(RuntimeError):
    pass


class PrintCancelled(RuntimeError):
    pass


class PrintEngine:
    def __init__(self, store: Store, hardware: HardwareController):
        self.store = store
        self.hardware = hardware
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.current_job_id: int | None = None

    @property
    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        return {"busy": self.busy, "current_job_id": self.current_job_id}

    def start_next(self) -> dict[str, Any] | None:
        with self._lock:
            if self.busy:
                raise RuntimeError("A piece is already printing")
            job = self.store.claim_next_job()
            if job is None:
                return None
            try:
                self.validate_configuration(job)
            except Exception as exc:
                self.store.update_job(
                    job["id"], status="failed", completed_at=utc_now(), error=str(exc)[:500]
                )
                raise
            self._cancel.clear()
            self.current_job_id = job["id"]
            self._thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="pipette-print")
            self._thread.start()
            return job

    def cancel(self) -> bool:
        if not self.busy:
            return False
        self._cancel.set()
        return True

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise PrintCancelled("Stopped by the operator")

    def _run(self, job: dict[str, Any]) -> None:
        try:
            self._print_job(job)
        except PrintCancelled as exc:
            try:
                self.hardware.quick_stop(self.store.get_config())
            except Exception:
                pass
            self.store.update_job(job["id"], status="cancelled", completed_at=utc_now(), error=str(exc))
        except Exception as exc:
            self.store.update_job(
                job["id"], status="failed", completed_at=utc_now(), error=str(exc)[:500]
            )
        else:
            self.store.update_job(job["id"], status="completed", completed_at=utc_now())
        finally:
            self.current_job_id = None

    @staticmethod
    def _require_number(value: Any, label: str) -> float:
        if value is None:
            raise PrintConfigurationError(f"Set {label} before printing")
        return float(value)

    def validate_configuration(
        self, job: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        config = self.store.get_config()
        paper = config["paper"]
        motion = config["motion"]
        servo = config["servo"]
        self._require_number(paper["bottom_right_x"], "the paper bottom-right X")
        self._require_number(paper["bottom_right_y"], "the paper bottom-right Y")
        self._require_number(paper["deposit_z"], "the paper deposit Z")
        travel_z = self._require_number(motion["travel_z"], "the safe travel Z")
        for field, label in (
            ("rest_position", "the servo rest position"),
            ("draw_position", "the servo draw position"),
            ("purge_position", "the servo purge position"),
        ):
            self._require_number(servo[field], label)

        colors = {color["id"]: color for color in self.store.list_colors()}
        used = {pixel for pixel in job["pixels"] if pixel}
        for color_id in used:
            color = colors.get(color_id)
            if color is None or not color["enabled"]:
                raise PrintConfigurationError(f"Color {color_id!r} is unavailable")
            for field, label in (
                ("x", "X"),
                ("y", "Y"),
                ("intake_z", "intake Z"),
                ("purge_z", "purge Z"),
            ):
                self._require_number(color[field], f"{color['name']} {label}")
            if travel_z < max(float(color["intake_z"]), float(color["purge_z"])):
                raise PrintConfigurationError(f"Safe travel Z must be above both {color['name']} well heights")
        if travel_z < float(paper["deposit_z"]):
            raise PrintConfigurationError("Safe travel Z must be above the paper deposit Z")
        return config, colors

    def _settle_servo(self, config: dict[str, Any], goal: int) -> None:
        self.hardware.move_servo(config, goal)
        time.sleep(max(0, int(config["servo"]["settle_ms"])) / 1000)

    def _print_job(self, job: dict[str, Any]) -> None:
        config, colors = self.validate_configuration(job)
        paper = config["paper"]
        motion = config["motion"]
        servo = config["servo"]
        travel_z = float(motion["travel_z"])
        rest = int(servo["rest_position"])
        draw = int(servo["draw_position"])
        purge = int(servo["purge_position"])
        grid_size = int(job["grid_size"])
        right = float(paper["bottom_right_x"])
        bottom = float(paper["bottom_right_y"])
        deposit_z = float(paper["deposit_z"])

        self._settle_servo(config, rest)
        self.hardware.move(config, z=travel_z)
        completed = 0
        for index, color_id in enumerate(job["pixels"]):
            if not color_id:
                continue
            self._check_cancelled()
            color = colors[color_id]
            row, column = divmod(index, grid_size)
            target_x = right * ((column + 0.5) / grid_size)
            target_y = bottom * ((row + 0.5) / grid_size)

            # Empty any unknown residual liquid before the first draw. Later drops
            # have already been purged at the end of the preceding cycle.
            self.hardware.move(config, z=travel_z)
            self.hardware.move(config, x=float(color["x"]), y=float(color["y"]))
            self.hardware.move(config, z=float(color["purge_z"]))
            if completed == 0:
                self._settle_servo(config, purge)
            self._settle_servo(config, draw)

            # Dip and release the plunger to aspirate one drop.
            self.hardware.move(config, z=float(color["intake_z"]))
            self._settle_servo(config, rest)

            # Travel high, lower over the pixel center, and dispense one drop.
            self.hardware.move(config, z=travel_z)
            self.hardware.move(config, x=target_x, y=target_y)
            self.hardware.move(config, z=deposit_z)
            self._settle_servo(config, draw)

            # Clear the remainder back over its own well before changing color.
            self.hardware.move(config, z=travel_z)
            self.hardware.move(config, x=float(color["x"]), y=float(color["y"]))
            self.hardware.move(config, z=float(color["purge_z"]))
            self._settle_servo(config, purge)
            self._settle_servo(config, rest)
            completed += 1
            self.store.update_job(job["id"], progress_current=completed)

        self.hardware.move(config, z=travel_z)
        self._settle_servo(config, rest)
