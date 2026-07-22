#!/usr/bin/env python3
"""Compatibility launcher for the Pipette Pixels app."""

from pixel_pipette.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    print(f"Pipette Pixels hardware mode: {app.state.hardware.mode.upper()}", flush=True)
    uvicorn.run("pixel_pipette.main:app", host="0.0.0.0", port=8000, log_level="info")
