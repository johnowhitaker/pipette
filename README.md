# Pipette Pixels

A Raspberry Pi web app for turning crowd-submitted pixel art into liquid-drop drawings with a 3D-printer-mounted micropipette.

The public page is a mobile-friendly pixel editor whose available canvas sizes are chosen by the operator. Submissions enter a persistent queue. The password-protected operator console handles printer and Dynamixel connections, paper calibration, color-well positions, pipette-plunger positions, and the print queue.

The hardware design uses a Dynamixel XL430 servo and control board, an Ender 3 V3 SE, a Raspberry Pi, and an adjustable 20–200 µL pipette. The mount CAD is [on Onshape](https://cad.onshape.com/documents/42cf135ce4b1a1a34746c4ee/w/bec91f2a6df1f3690807754b/e/98be9c73e6b42ac109d81153?renderMode=0&uiState=697f9facc65afbdb14e9a4b1).

## Safety model

The app starts in **simulation mode by default**. In simulation, every UI and print-flow operation runs against virtual hardware. Connected hardware is only used when `PIPETTE_HARDWARE=real` is explicitly set.

Opening the app never moves either device in the default manual mode. Printer and servo test buttons only read their current positions. Motion happens after an operator explicitly jogs an axis, tests a saved servo position, starts a queued print, presses the printer knob for a staged piece, or deliberately enables immediate auto-start mode.

Before a live print, verify the safe travel Z clears the paper, clips, wells, and every other obstacle. The Z buttons are intentionally separate from the XY jog pad.

## Run it on the Pi

The existing Pi virtual environment already contains the core hardware dependencies. To install or refresh everything:

```bash
cd ~/pipette
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before exposing the app. Change the password and session secret; leave `PIPETTE_HARDWARE=simulate` for a dry run or set it to `real` for the connected printer and servo.

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
nano .env
python3 api_server.py
```

The app loads `~/pipette/.env` automatically and prints `Pipette Pixels hardware mode: SIMULATE` or `REAL` before starting the server.

Open `http://rpi.local:8000/` for the public canvas and `http://rpi.local:8000/admin` for the operator console. The fallback admin password is `letmein123` if no environment value is set.

For HTTPS behind a Cloudflare Tunnel, set `PIPETTE_SECURE_COOKIE=1` after confirming the public hostname is HTTPS.

### Optional systemd service

```bash
sudo cp deploy/pipette-pixels.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pipette-pixels
sudo systemctl status pipette-pixels
```

## Operator setup flow

1. Open `/admin`, log in, and confirm whether the header says **Simulation** or **Live hardware**.
2. In **Connections**, save the serial settings and test the printer and servo. Defaults are `/dev/ttyUSB0` at 115200 baud and `/dev/ttyACM0` at 1,000,000 baud, Dynamixel ID 1.
3. Release the printer steppers. Manually position the pipette tip at the paper surface in the top-left corner. Press **Set current point as origin**. This sends `G92 X0 Y0 Z0` and locks the steppers.
4. Jog in X and Y to the paper's bottom-right corner and capture the current X/Y. This supports arbitrary rectangular offcuts as well as the usual 80 mm square.
5. Set the paper deposit Z and a safe travel Z that clears the entire setup. Enable the public canvas sizes—8×8 and 10×10 are good demo defaults; 12×12, 16×16, and custom sizes up to 32×32 are supported—and save.
6. Under **Liquids & servo**, release servo torque, position the plunger by hand, read it, and capture the rest, draw/dispense, and purge tick positions. The app uses non-blocking servo goals plus the configured settle delay.
7. For each enabled color, jog over its well and capture X/Y. Capture its intake Z (tip in liquid) and purge Z (tip safely over the well), then save the well.
8. Submit a test drawing from the public page and review it in **Print queue**. Start it manually for the first live test. Once everything is calibrated, choose the event queue mode described below.

## Queue start modes

The operator chooses how queued artwork starts under **Setup → Event settings**:

- **Manual** keeps the existing workflow: every submission waits for **Print next piece** in the admin console.
- **Printer knob** is the recommended booth workflow. The app stages the next drawing with a motion-free Marlin `M0` pause. A visitor loads fresh paper and presses the printer's knob; only that click begins the print. When it finishes, the next queued drawing is staged automatically.
- **Immediate** starts the next submission as soon as the machine is idle and chains through the queue. Use this only when an operator is managing paper continuously.

On the stock Ender 3 V3 SE firmware, the knob successfully resumes `M0`, but its display does not show the custom pause message. Put a small “Load paper, then press to print” label beside the knob. Cancelling a staged piece sends `M108` to clear the Marlin wait without moving an axis.

## Drop sequence

Each colored pixel is mapped to the center of its grid cell between the calibrated top-left origin and bottom-right corner. For every drop the app:

1. moves at safe travel Z to the color well;
2. pre-presses the plunger, lowers to that well's intake Z, and returns to rest to aspirate;
3. lifts to safe travel Z, moves to the pixel center, lowers to the paper deposit Z, presses to the draw/dispense position, lifts 1 mm, and briefly presses to purge position to release the drop from the tip;
4. lifts while still pressed, returns to that color's purge Z, and releases the servo to rest.

Printer moves use `M400` completion. Servo moves do not wait for an exact PID position; they use the operator-configured timing delay instead.

## Data and configuration

Calibration, colors, and jobs are stored in `data/pipette_pixels.sqlite3`. The `data/` directory and `.env` are excluded from Git, so pulling application updates does not overwrite event calibration or secrets.

Useful environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PIPETTE_HARDWARE` | `simulate` | `simulate` or `real` |
| `PIPETTE_ADMIN_PASSWORD` | `letmein123` | Operator-console password |
| `PIPETTE_SESSION_SECRET` | development fallback | Signs 12-hour login cookies |
| `PIPETTE_SECURE_COOKIE` | `0` | Set to `1` behind HTTPS |
| `PIPETTE_DATA_DIR` | `./data` | SQLite data directory |

## Development checks

Install the small HTTP test dependency, then run the queue, authentication, calibration, coordinate-mapping, and full simulated print-flow checks:

```bash
pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
```

The original `Pipette_Control_Dino.ipynb` remains as a hardware reference, but `api_server.py` now launches the integrated Pipette Pixels application.
