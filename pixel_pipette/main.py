"""FastAPI entry point for the public pixel editor and operator console."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .hardware import HardwareController, HardwareError
from .printing import PrintEngine
from .store import Store


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"


class Submission(BaseModel):
    artist_name: str = Field(default="", max_length=40)
    grid_size: int = Field(ge=4, le=32)
    pixels: list[str | None]

    @field_validator("artist_name")
    @classmethod
    def tidy_name(cls, value: str) -> str:
        return " ".join(value.strip().split())


class LoginRequest(BaseModel):
    password: str


class JogRequest(BaseModel):
    axis: Literal["X", "Y", "Z"]
    distance: float = Field(ge=-25, le=25)


class ServoMoveRequest(BaseModel):
    goal_position: int = Field(ge=0, le=4095)


class DevicesConfig(BaseModel):
    printer_port: str = Field(min_length=1, max_length=120)
    printer_baud: int = Field(ge=1200, le=4_000_000)
    servo_port: str = Field(min_length=1, max_length=120)
    servo_baud: int = Field(ge=1200, le=4_000_000)
    servo_id: int = Field(ge=0, le=252)


class PaperConfig(BaseModel):
    bottom_right_x: float | None = Field(default=None, ge=-500, le=500)
    bottom_right_y: float | None = Field(default=None, ge=-500, le=500)
    deposit_z: float = Field(ge=-20, le=300)


class MotionConfig(BaseModel):
    travel_z: float = Field(ge=-20, le=300)
    xy_feed: int = Field(ge=1, le=20_000)
    z_feed: int = Field(ge=1, le=5_000)
    jog_feed: int = Field(ge=1, le=10_000)
    command_timeout_s: float = Field(ge=1, le=120)


class ServoConfig(BaseModel):
    rest_position: int | None = Field(default=None, ge=0, le=4095)
    draw_position: int | None = Field(default=None, ge=0, le=4095)
    purge_position: int | None = Field(default=None, ge=0, le=4095)
    velocity: int = Field(ge=0, le=32_767)
    acceleration: int = Field(ge=0, le=32_767)
    settle_ms: int = Field(ge=50, le=5000)


class ConfigUpdate(BaseModel):
    grid_sizes: list[int] = Field(min_length=1, max_length=12)
    accepting_submissions: bool
    learn_more_url: str = Field(max_length=300)
    devices: DevicesConfig
    paper: PaperConfig
    motion: MotionConfig
    servo: ServoConfig

    @field_validator("grid_sizes")
    @classmethod
    def valid_grid_sizes(cls, values: list[int]) -> list[int]:
        sizes = sorted(set(values))
        if any(size < 4 or size > 32 for size in sizes):
            raise ValueError("Canvas sizes must be between 4 and 32 pixels")
        return sizes


class ColorInput(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    x: float | None = Field(default=None, ge=-500, le=500)
    y: float | None = Field(default=None, ge=-500, le=500)
    intake_z: float | None = Field(default=None, ge=-20, le=300)
    purge_z: float | None = Field(default=None, ge=-20, le=300)
    enabled: bool = True


class RateLimiter:
    def __init__(self, limit: int = 6, window_s: int = 60):
        self.limit = limit
        self.window_s = window_s
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self.requests[key]
        while bucket and bucket[0] < now - self.window_s:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


def create_app(
    database_path: str | Path | None = None,
    hardware_mode: str | None = None,
    admin_password: str | None = None,
) -> FastAPI:
    data_dir = Path(os.environ.get("PIPETTE_DATA_DIR", PACKAGE_DIR.parent / "data"))
    db_path = Path(database_path or data_dir / "pipette_pixels.sqlite3")
    mode = hardware_mode or os.environ.get("PIPETTE_HARDWARE", "simulate").lower()
    password = admin_password or os.environ.get("PIPETTE_ADMIN_PASSWORD", "letmein123")
    secret = os.environ.get("PIPETTE_SESSION_SECRET", "pipette-pixels-change-me")

    store = Store(db_path)
    store.recover_interrupted_jobs()
    hardware = HardwareController(mode)
    engine = PrintEngine(store, hardware)
    limiter = RateLimiter()
    app = FastAPI(title="Pipette Pixels", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.hardware = hardware
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def make_token() -> str:
        expires = str(int(time.time()) + 12 * 60 * 60)
        signature = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def valid_token(token: str | None) -> bool:
        if not token or "." not in token:
            return False
        expires, signature = token.split(".", 1)
        if not expires.isdigit() or int(expires) < time.time():
            return False
        expected = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def require_admin(session: str | None) -> None:
        if not valid_token(session):
            raise HTTPException(status_code=401, detail="Admin login required")

    def ensure_idle() -> None:
        if engine.busy:
            raise HTTPException(status_code=409, detail="Controls are locked while a piece is printing")

    def hardware_call(callback: Any) -> Any:
        try:
            return callback()
        except HardwareError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def public_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "hardware_mode": hardware.mode, "printing": engine.busy}

    @app.get("/api/public/config")
    def public_config() -> dict[str, Any]:
        config = store.get_config()
        colors = [
            {"id": color["id"], "name": color["name"], "hex": color["hex"]}
            for color in store.list_colors(enabled_only=True)
        ]
        return {
            "grid_sizes": config["grid_sizes"],
            "colors": colors,
            "queue_count": store.queue_count(),
            "accepting_submissions": config["accepting_submissions"],
            "learn_more_url": config["learn_more_url"],
        }

    @app.post("/api/submissions", status_code=201)
    def submit_piece(submission: Submission, request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            raise HTTPException(status_code=429, detail="Please wait a minute before submitting again")
        config = store.get_config()
        if not config["accepting_submissions"]:
            raise HTTPException(status_code=503, detail="Submissions are paused right now")
        if submission.grid_size not in config["grid_sizes"]:
            raise HTTPException(
                status_code=409,
                detail="That canvas size is no longer available; refresh and try again",
            )
        if len(submission.pixels) != submission.grid_size**2:
            raise HTTPException(status_code=422, detail="The pixel grid has the wrong size")
        allowed = {color["id"] for color in store.list_colors(enabled_only=True)}
        if any(pixel is not None and pixel not in allowed for pixel in submission.pixels):
            raise HTTPException(status_code=422, detail="The drawing uses an unavailable color")
        if not any(submission.pixels):
            raise HTTPException(status_code=422, detail="Add at least one pixel before submitting")
        if store.queue_count() >= 100:
            raise HTTPException(status_code=503, detail="The queue is full; please try again later")
        job = store.create_job(submission.artist_name, submission.grid_size, submission.pixels)
        return {"id": job["id"], "queue_count": store.queue_count()}

    @app.post("/api/admin/login")
    def login(payload: LoginRequest) -> Response:
        if not hmac.compare_digest(payload.password, password):
            raise HTTPException(status_code=401, detail="That password is not right")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "pipette_session",
            make_token(),
            httponly=True,
            samesite="strict",
            secure=os.environ.get("PIPETTE_SECURE_COOKIE", "0") == "1",
            max_age=12 * 60 * 60,
        )
        return response

    @app.post("/api/admin/logout")
    def logout() -> Response:
        response = JSONResponse({"ok": True})
        response.delete_cookie("pipette_session")
        return response

    @app.get("/api/admin/state")
    def admin_state(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        return {
            "config": store.get_config(),
            "colors": store.list_colors(),
            "jobs": store.list_jobs(),
            "engine": engine.status(),
            "hardware_mode": hardware.mode,
        }

    @app.put("/api/admin/config")
    def update_config(
        payload: ConfigUpdate, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, Any]:
        require_admin(pipette_session)
        return store.update_config(payload.model_dump())

    @app.post("/api/admin/colors", status_code=201)
    def create_color(
        payload: ColorInput, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, Any]:
        require_admin(pipette_session)
        return store.create_color(payload.model_dump())

    @app.put("/api/admin/colors/{color_id}")
    def update_color(
        color_id: str, payload: ColorInput, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, Any]:
        require_admin(pipette_session)
        color = store.update_color(color_id, payload.model_dump())
        if color is None:
            raise HTTPException(status_code=404, detail="Color not found")
        return color

    @app.delete("/api/admin/colors/{color_id}")
    def delete_color(
        color_id: str, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, bool]:
        require_admin(pipette_session)
        if not store.delete_color(color_id):
            raise HTTPException(status_code=404, detail="Color not found")
        return {"ok": True}

    @app.get("/api/admin/hardware/ports")
    def ports(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        return {"ports": hardware_call(hardware.list_ports)}

    @app.get("/api/admin/hardware/position")
    def printer_position(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        return hardware_call(lambda: hardware.read_position(store.get_config()))

    @app.get("/api/admin/hardware/servo-position")
    def servo_position(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        return {"position": hardware_call(lambda: hardware.read_servo_position(store.get_config()))}

    @app.post("/api/admin/hardware/test-printer")
    def test_printer(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        ensure_idle()
        return hardware_call(lambda: hardware.test_printer(store.get_config()))

    @app.post("/api/admin/hardware/test-servo")
    def test_servo(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        ensure_idle()
        return hardware_call(lambda: hardware.test_servo(store.get_config()))

    @app.post("/api/admin/hardware/disable-steppers")
    def disable_steppers(pipette_session: str | None = Cookie(default=None)) -> dict[str, bool]:
        require_admin(pipette_session)
        ensure_idle()
        hardware_call(lambda: hardware.disable_steppers(store.get_config()))
        return {"ok": True}

    @app.post("/api/admin/hardware/enable-steppers")
    def enable_steppers(pipette_session: str | None = Cookie(default=None)) -> dict[str, bool]:
        require_admin(pipette_session)
        ensure_idle()
        hardware_call(lambda: hardware.enable_steppers(store.get_config()))
        return {"ok": True}

    @app.post("/api/admin/hardware/set-origin")
    def set_origin(pipette_session: str | None = Cookie(default=None)) -> dict[str, float]:
        require_admin(pipette_session)
        ensure_idle()
        return hardware_call(lambda: hardware.set_origin(store.get_config()))

    @app.post("/api/admin/hardware/jog")
    def jog(
        payload: JogRequest, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, float]:
        require_admin(pipette_session)
        ensure_idle()
        return hardware_call(lambda: hardware.jog(store.get_config(), payload.axis, payload.distance))

    @app.post("/api/admin/hardware/servo-move")
    def move_servo(
        payload: ServoMoveRequest, pipette_session: str | None = Cookie(default=None)
    ) -> dict[str, int]:
        require_admin(pipette_session)
        ensure_idle()
        return {
            "position": hardware_call(
                lambda: hardware.move_servo(store.get_config(), payload.goal_position)
            )
        }

    @app.post("/api/admin/hardware/servo-disable")
    def disable_servo(pipette_session: str | None = Cookie(default=None)) -> dict[str, bool]:
        require_admin(pipette_session)
        ensure_idle()
        hardware_call(lambda: hardware.disable_servo_torque(store.get_config()))
        return {"ok": True}

    @app.delete("/api/admin/jobs/{job_id}")
    def remove_job(job_id: int, pipette_session: str | None = Cookie(default=None)) -> dict[str, bool]:
        require_admin(pipette_session)
        if not store.remove_queued_job(job_id):
            raise HTTPException(status_code=409, detail="Only queued pieces can be removed")
        return {"ok": True}

    @app.post("/api/admin/jobs/print-next")
    def print_next(pipette_session: str | None = Cookie(default=None)) -> dict[str, Any]:
        require_admin(pipette_session)
        try:
            job = engine.start_next()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="The queue is empty")
        return {"ok": True, "job_id": job["id"]}

    @app.post("/api/admin/jobs/cancel-current")
    def cancel_current(pipette_session: str | None = Cookie(default=None)) -> dict[str, bool]:
        require_admin(pipette_session)
        if not engine.cancel():
            raise HTTPException(status_code=409, detail="Nothing is printing")
        return {"ok": True}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("pixel_pipette.main:app", host="0.0.0.0", port=8000, reload=False)
