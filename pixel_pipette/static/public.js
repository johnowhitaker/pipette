const $ = (selector) => document.querySelector(selector);

const appState = {
  gridSize: 8,
  colors: [],
  pixels: [],
  selected: null,
  drawing: false,
};

function messageFrom(data, fallback = "Something went wrong. Please try again.") {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join("; ");
  return fallback;
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(messageFrom(data));
  return data;
}

function colorHex(colorId) {
  return appState.colors.find((color) => color.id === colorId)?.hex || "#fffdf7";
}

function renderPalette() {
  const palette = $("#palette");
  palette.replaceChildren();
  const options = [{ id: null, name: "Erase", hex: "#f5efe2", eraser: true }, ...appState.colors];
  options.forEach((color, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `palette-button${color.eraser ? " eraser" : ""}`;
    button.dataset.color = color.id || "";
    button.setAttribute("aria-label", color.name);
    button.setAttribute("aria-pressed", String(appState.selected === color.id));
    if (appState.selected === color.id) button.classList.add("selected");
    const swatch = document.createElement("span");
    swatch.className = "palette-swatch";
    swatch.style.setProperty("--swatch", color.hex);
    const name = document.createElement("span");
    name.textContent = color.name;
    button.append(swatch, name);
    button.addEventListener("click", () => {
      appState.selected = color.id;
      renderPalette();
    });
    palette.append(button);
    if (index === 1 && appState.selected === undefined) appState.selected = color.id;
  });
}

function paintCell(index) {
  if (index < 0 || index >= appState.pixels.length) return;
  appState.pixels[index] = appState.selected;
  const cell = document.querySelector(`.pixel-cell[data-index="${index}"]`);
  if (cell) {
    cell.style.setProperty("--cell-color", colorHex(appState.selected));
    cell.dataset.color = appState.selected || "";
  }
}

function renderGrid() {
  const grid = $("#pixel-grid");
  grid.style.setProperty("--grid-size", appState.gridSize);
  grid.replaceChildren();
  appState.pixels.forEach((colorId, index) => {
    const cell = document.createElement("button");
    const row = Math.floor(index / appState.gridSize) + 1;
    const column = (index % appState.gridSize) + 1;
    cell.type = "button";
    cell.className = "pixel-cell";
    cell.dataset.index = index;
    cell.dataset.color = colorId || "";
    cell.setAttribute("role", "gridcell");
    cell.setAttribute("aria-label", `Row ${row}, column ${column}`);
    cell.style.setProperty("--cell-color", colorHex(colorId));
    cell.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      appState.drawing = true;
      cell.setPointerCapture?.(event.pointerId);
      paintCell(index);
    });
    cell.addEventListener("pointerenter", () => {
      if (appState.drawing) paintCell(index);
    });
    grid.append(cell);
  });
}

function clearGrid() {
  appState.pixels.fill(null);
  renderGrid();
}

async function loadConfig() {
  try {
    const config = await request("/api/public/config");
    appState.gridSize = config.grid_size;
    appState.colors = config.colors;
    appState.selected = config.colors[0]?.id ?? null;
    appState.pixels = Array(appState.gridSize ** 2).fill(null);
    $("#queue-count").textContent = config.queue_count;
    $("#canvas-size-label").textContent = `${appState.gridSize} × ${appState.gridSize} canvas`;
    $("#learn-more-link").href = config.learn_more_url;
    $("#submit-button").disabled = !config.accepting_submissions || config.colors.length === 0;
    if (!config.accepting_submissions) $("#form-message").textContent = "Submissions are paused for a moment.";
    if (config.colors.length === 0) $("#form-message").textContent = "The operator is still loading the colors.";
    renderPalette();
    renderGrid();
  } catch (error) {
    $("#form-message").textContent = error.message;
    $("#submit-button").disabled = true;
  }
}

document.addEventListener("pointerup", () => { appState.drawing = false; });
document.addEventListener("pointercancel", () => { appState.drawing = false; });

$("#pixel-grid").addEventListener("pointermove", (event) => {
  if (!appState.drawing) return;
  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest(".pixel-cell");
  if (target) paintCell(Number(target.dataset.index));
});

$("#clear-button").addEventListener("click", clearGrid);

$("#submit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#submit-button");
  const message = $("#form-message");
  button.disabled = true;
  message.textContent = "";
  try {
    const result = await request("/api/submissions", {
      method: "POST",
      body: JSON.stringify({
        artist_name: $("#artist-name").value,
        grid_size: appState.gridSize,
        pixels: appState.pixels,
      }),
    });
    $("#queue-count").textContent = result.queue_count;
    $("#success-copy").textContent = `Your piece is #${result.id}. Keep an eye on the robot to see it become real.`;
    $("#success-dialog").showModal();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#draw-another").addEventListener("click", () => {
  $("#success-dialog").close();
  $("#artist-name").value = "";
  clearGrid();
});

loadConfig();
