const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

let state = null;
let currentPosition = null;
let currentServoPosition = null;
let toastTimer = null;

function messageFrom(data, fallback = "Something went wrong") {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((item) => item.msg).join("; ");
  return fallback;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(messageFrom(data));
    error.status = response.status;
    throw error;
  }
  return data;
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = "toast"; }, 3500);
}

function numberValue(selector, nullable = false) {
  const raw = $(selector).value.trim();
  if (nullable && raw === "") return null;
  return Number(raw);
}

function formatNumber(value) {
  return value === null || value === undefined ? "" : String(value);
}

function configFromForm() {
  return {
    grid_sizes: $$('[data-admin-grid-size]')
      .filter((input) => input.checked)
      .map((input) => Number(input.value)),
    accepting_submissions: $("#accepting-submissions").checked,
    learn_more_url: $("#learn-more-url").value.trim(),
    devices: {
      printer_port: $("#printer-port").value.trim(),
      printer_baud: numberValue("#printer-baud"),
      servo_port: $("#servo-port").value.trim(),
      servo_baud: numberValue("#servo-baud"),
      servo_id: numberValue("#servo-id"),
    },
    paper: {
      bottom_right_x: numberValue("#paper-x", true),
      bottom_right_y: numberValue("#paper-y", true),
      deposit_z: numberValue("#deposit-z"),
    },
    motion: {
      travel_z: numberValue("#travel-z"),
      xy_feed: numberValue("#xy-feed"),
      z_feed: numberValue("#z-feed"),
      jog_feed: numberValue("#jog-feed"),
      command_timeout_s: numberValue("#command-timeout"),
    },
    servo: {
      rest_position: numberValue("#servo-rest", true),
      draw_position: numberValue("#servo-draw", true),
      purge_position: numberValue("#servo-purge", true),
      velocity: numberValue("#servo-velocity"),
      acceleration: numberValue("#servo-acceleration"),
      settle_ms: numberValue("#servo-settle"),
    },
  };
}

function renderAdminGridSizes(enabledSizes) {
  const root = $("#admin-grid-sizes");
  const choices = [...new Set([8, 10, 12, 16, ...enabledSizes])].sort((a, b) => a - b);
  root.replaceChildren();
  choices.forEach((size) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = size;
    input.dataset.adminGridSize = "";
    input.checked = enabledSizes.includes(size);
    const text = document.createElement("span");
    text.textContent = `${size} × ${size}`;
    label.append(input, text);
    root.append(label);
  });
}

function fillConfig(config) {
  renderAdminGridSizes(config.grid_sizes);
  $("#accepting-submissions").checked = config.accepting_submissions;
  $("#learn-more-url").value = config.learn_more_url;
  $("#printer-port").value = config.devices.printer_port;
  $("#printer-baud").value = config.devices.printer_baud;
  $("#servo-port").value = config.devices.servo_port;
  $("#servo-baud").value = config.devices.servo_baud;
  $("#servo-id").value = config.devices.servo_id;
  $("#paper-x").value = formatNumber(config.paper.bottom_right_x);
  $("#paper-y").value = formatNumber(config.paper.bottom_right_y);
  $("#deposit-z").value = config.paper.deposit_z;
  $("#travel-z").value = config.motion.travel_z;
  $("#xy-feed").value = config.motion.xy_feed;
  $("#z-feed").value = config.motion.z_feed;
  $("#jog-feed").value = config.motion.jog_feed;
  $("#command-timeout").value = config.motion.command_timeout_s;
  $("#servo-rest").value = formatNumber(config.servo.rest_position);
  $("#servo-draw").value = formatNumber(config.servo.draw_position);
  $("#servo-purge").value = formatNumber(config.servo.purge_position);
  $("#servo-velocity").value = config.servo.velocity;
  $("#servo-acceleration").value = config.servo.acceleration;
  $("#servo-settle").value = config.servo.settle_ms;
}

function updatePosition(position) {
  currentPosition = position;
  const values = [["X", position.x], ["Y", position.y], ["Z", position.z]];
  $("#position-readout").replaceChildren(...values.map(([axis, value]) => {
    const span = document.createElement("span");
    span.textContent = `${axis} ${Number(value).toFixed(2)}`;
    return span;
  }));
}

async function refreshPosition() {
  try {
    updatePosition(await api("/api/admin/hardware/position"));
  } catch (error) {
    currentPosition = null;
    toast(error.message, true);
  }
}

async function readServo() {
  try {
    const result = await api("/api/admin/hardware/servo-position");
    currentServoPosition = result.position;
    $("#servo-current").textContent = result.position;
    return result.position;
  } catch (error) {
    currentServoPosition = null;
    toast(error.message, true);
    return null;
  }
}

function miniGrid(job) {
  const grid = document.createElement("div");
  grid.className = "mini-grid";
  grid.style.setProperty("--mini-size", job.grid_size);
  const colorMap = Object.fromEntries(state.colors.map((color) => [color.id, color.hex]));
  job.pixels.forEach((pixel) => {
    const square = document.createElement("i");
    square.className = "mini-pixel";
    if (pixel) square.style.background = colorMap[pixel] || "#333";
    grid.append(square);
  });
  return grid;
}

function renderQueue() {
  const queued = state.jobs.filter((job) => job.status === "queued").sort((a, b) => a.id - b.id);
  const active = state.jobs.find((job) => job.status === "printing");
  const history = state.jobs.filter((job) => !["queued", "printing"].includes(job.status)).slice(0, 20);
  $("#tab-queue-count").textContent = queued.length;
  $("#queue-heading").textContent = queued.length ? `${queued.length} piece${queued.length === 1 ? "" : "s"} waiting` : "No pieces waiting";
  $("#print-next").disabled = queued.length === 0 || state.engine.busy;
  $("#cancel-print").hidden = !state.engine.busy;

  const activeRoot = $("#active-print");
  activeRoot.replaceChildren();
  if (active) {
    const card = document.createElement("article");
    card.className = "active-job";
    card.append(miniGrid(active));
    const details = document.createElement("div");
    const percent = active.progress_total ? Math.round(active.progress_current / active.progress_total * 100) : 0;
    const heading = document.createElement("h3");
    heading.textContent = `Printing #${active.id}${active.artist_name ? ` · ${active.artist_name}` : ""}`;
    const copy = document.createElement("p");
    copy.textContent = `${active.progress_current} of ${active.progress_total} liquid pixels placed`;
    const track = document.createElement("div");
    track.className = "progress-track";
    const fill = document.createElement("i");
    fill.style.width = `${percent}%`;
    track.append(fill);
    details.append(heading, copy, track);
    card.append(details);
    activeRoot.append(card);
  }

  const queueRoot = $("#queue-list");
  queueRoot.replaceChildren();
  if (!queued.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Submitted drawings will appear here.";
    queueRoot.append(empty);
  } else {
    queued.forEach((job, index) => {
      const card = document.createElement("article");
      card.className = "job-card";
      card.append(miniGrid(job));
      const meta = document.createElement("div");
      meta.className = "job-meta";
      const heading = document.createElement("h3");
      heading.textContent = `#${job.id} · ${index === 0 ? "Up next" : `Position ${index + 1}`}`;
      const artist = document.createElement("p");
      artist.textContent = job.artist_name || "Anonymous artist";
      const count = document.createElement("p");
      count.textContent = `${job.progress_total} colored pixels`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Remove from queue";
      remove.addEventListener("click", () => removeJob(job.id));
      meta.append(heading, artist, count, remove);
      card.append(meta);
      queueRoot.append(card);
    });
  }

  const historyRoot = $("#history-list");
  historyRoot.replaceChildren();
  if (!history.length) {
    const empty = document.createElement("div");
    empty.className = "inline-note";
    empty.textContent = "Completed and stopped prints will be listed here.";
    historyRoot.append(empty);
  } else {
    history.forEach((job) => {
      const row = document.createElement("div");
      row.className = "history-row";
      const status = document.createElement("span");
      status.className = `history-status ${job.status}`;
      status.textContent = job.status;
      const text = document.createElement("span");
      text.textContent = `#${job.id}${job.artist_name ? ` · ${job.artist_name}` : ""}${job.error ? ` — ${job.error}` : ""}`;
      const time = document.createElement("time");
      time.textContent = job.completed_at ? new Date(job.completed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
      row.append(status, text, time);
      historyRoot.append(row);
    });
  }
}

function colorPayload(card) {
  const read = (name, nullable = false) => {
    const value = $(`[data-field="${name}"]`, card).value.trim();
    return nullable && value === "" ? null : Number(value);
  };
  return {
    name: $("[data-field='name']", card).value.trim(),
    hex: $("[data-field='hex']", card).value,
    x: read("x", true),
    y: read("y", true),
    intake_z: read("intake_z", true),
    purge_z: read("purge_z", true),
    enabled: $("[data-field='enabled']", card).checked,
  };
}

function field(label, name, value, step = "0.1") {
  const wrapper = document.createElement("label");
  wrapper.textContent = label;
  const input = document.createElement("input");
  input.type = "number";
  input.step = step;
  input.dataset.field = name;
  input.value = formatNumber(value);
  wrapper.append(input);
  return wrapper;
}

function renderWells() {
  const root = $("#well-list");
  root.replaceChildren();
  state.colors.forEach((color) => {
    const card = document.createElement("article");
    card.className = "well-card";
    card.dataset.colorId = color.id;

    const identity = document.createElement("div");
    identity.className = "well-identity";
    const picker = document.createElement("input");
    picker.type = "color";
    picker.value = color.hex;
    picker.dataset.field = "hex";
    picker.setAttribute("aria-label", "Color");
    const name = document.createElement("input");
    name.value = color.name;
    name.dataset.field = "name";
    name.setAttribute("aria-label", "Color name");
    identity.append(picker, name);

    const xField = field("Well X", "x", color.x, "0.001");
    const yField = field("Well Y", "y", color.y, "0.001");
    const intakeField = field("Intake Z", "intake_z", color.intake_z);
    const purgeField = field("Purge Z", "purge_z", color.purge_z);
    [xField, yField].forEach((wrapper) => {
      const controls = document.createElement("span");
      controls.className = "well-capture";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Use current";
      button.dataset.capture = "xy";
      controls.append(button);
      wrapper.append(controls);
    });
    [intakeField, purgeField].forEach((wrapper) => {
      const controls = document.createElement("span");
      controls.className = "well-capture";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Use current Z";
      button.dataset.capture = "z";
      controls.append(button);
      wrapper.append(controls);
    });

    const actions = document.createElement("div");
    actions.className = "well-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "well-save";
    save.textContent = "Save well";
    save.dataset.saveColor = color.id;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "well-delete";
    remove.textContent = "Delete";
    remove.dataset.deleteColor = color.id;
    actions.append(save, remove);

    const enabled = document.createElement("label");
    enabled.className = "well-enabled";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = color.enabled;
    checkbox.dataset.field = "enabled";
    const enabledText = document.createElement("span");
    enabledText.textContent = "Available on the public palette";
    enabled.append(checkbox, enabledText);

    card.append(identity, xField, yField, intakeField, purgeField, actions, enabled);
    root.append(card);
  });
}

async function refreshState({ populate = false } = {}) {
  try {
    state = await api("/api/admin/state");
    if (populate) {
      fillConfig(state.config);
      renderWells();
      const live = state.hardware_mode === "real";
      $("#mode-badge").textContent = live ? "Live hardware" : "Simulation";
      $("#mode-badge").classList.toggle("live", live);
      $("#simulation-banner").classList.toggle("live", live);
      $("#simulation-banner strong").textContent = live ? "Live hardware" : "Simulation mode";
      $("#simulation-banner span").textContent = live
        ? "Controls on this page can move the printer and pipette. Keep the work area clear."
        : "Controls update a virtual machine. No connected hardware will move.";
    }
    renderQueue();
    return true;
  } catch (error) {
    if (error.status === 401) {
      $("#console-view").hidden = true;
      $("#login-view").hidden = false;
      return false;
    }
    toast(error.message, true);
    return false;
  }
}

async function saveConfig(message = "Setup saved") {
  try {
    state.config = await api("/api/admin/config", { method: "PUT", body: JSON.stringify(configFromForm()) });
    toast(message);
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

async function hardwareAction(path, successMessage) {
  try {
    const result = await api(path, { method: "POST", body: "{}" });
    if (successMessage) toast(successMessage);
    return result;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

async function removeJob(id) {
  if (!confirm(`Remove piece #${id} from the queue?`)) return;
  try {
    await api(`/api/admin/jobs/${id}`, { method: "DELETE" });
    toast(`Piece #${id} removed`);
    await refreshState();
  } catch (error) { toast(error.message, true); }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#login-message");
  message.textContent = "";
  try {
    await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    $("#password").value = "";
    $("#login-view").hidden = true;
    $("#console-view").hidden = false;
    await refreshState({ populate: true });
    await refreshPosition();
  } catch (error) { message.textContent = error.message; }
});

$("#logout-button").addEventListener("click", async () => {
  await api("/api/admin/logout", { method: "POST", body: "{}" });
  location.reload();
});

$$('[data-tab]').forEach((button) => button.addEventListener("click", () => {
  $$('[data-tab]').forEach((item) => item.classList.toggle("active", item === button));
  $$(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${button.dataset.tab}`));
  if (button.dataset.tab === "queue") refreshState();
}));

$("#save-config").addEventListener("click", () => saveConfig());
$("#save-servo").addEventListener("click", () => saveConfig("Servo settings saved"));
$("#refresh-position").addEventListener("click", refreshPosition);
$("#read-servo").addEventListener("click", readServo);

$("#add-grid-size").addEventListener("click", () => {
  const size = Number($("#custom-grid-size").value);
  if (!Number.isInteger(size) || size < 4 || size > 32) {
    toast("Choose a whole-number canvas size from 4 to 32", true);
    return;
  }
  const enabled = $$('[data-admin-grid-size]')
    .filter((input) => input.checked)
    .map((input) => Number(input.value));
  renderAdminGridSizes([...enabled, size]);
  $("#custom-grid-size").value = "";
  toast(`${size} × ${size} added — save setup when ready`);
});

$("#scan-ports").addEventListener("click", async () => {
  try {
    const result = await api("/api/admin/hardware/ports");
    $("#ports-result").textContent = result.ports.map((port) => `${port.device} — ${port.description}`).join(" · ") || "No serial ports found";
  } catch (error) { toast(error.message, true); }
});

$("#test-printer").addEventListener("click", async () => {
  if (!await saveConfig("Device settings saved")) return;
  const result = await hardwareAction("/api/admin/hardware/test-printer");
  $("#printer-light").className = `status-light ${result ? "good" : "bad"}`;
  if (result) { updatePosition(result.position); toast("Printer responded"); }
});

$("#test-servo").addEventListener("click", async () => {
  if (!await saveConfig("Device settings saved")) return;
  const result = await hardwareAction("/api/admin/hardware/test-servo");
  $("#servo-light").className = `status-light ${result ? "good" : "bad"}`;
  if (result) { currentServoPosition = result.position; $("#servo-current").textContent = result.position; toast("Servo responded"); }
});

$("#disable-steppers").addEventListener("click", () => hardwareAction("/api/admin/hardware/disable-steppers", "Steppers released — position the head by hand"));
$("#release-servo").addEventListener("click", () => hardwareAction("/api/admin/hardware/servo-disable", "Servo torque released"));

$("#set-origin").addEventListener("click", async () => {
  if (!confirm("Make the current pipette position X0 Y0 Z0?")) return;
  const result = await hardwareAction("/api/admin/hardware/set-origin", "Paper origin set to X0 Y0 Z0");
  if (result) updatePosition(result);
});

$$('[data-jog-axis]').forEach((button) => button.addEventListener("click", async () => {
  const distance = Number($("#jog-step").value) * Number(button.dataset.jogSign);
  try {
    const result = await api("/api/admin/hardware/jog", {
      method: "POST",
      body: JSON.stringify({ axis: button.dataset.jogAxis, distance }),
    });
    updatePosition(result);
  } catch (error) { toast(error.message, true); }
}));

$("#capture-paper-corner").addEventListener("click", async () => {
  await refreshPosition();
  if (!currentPosition) return;
  $("#paper-x").value = currentPosition.x;
  $("#paper-y").value = currentPosition.y;
  toast("Current X/Y copied — save setup when ready");
});

$$('[data-use-servo]').forEach((button) => button.addEventListener("click", async () => {
  await readServo();
  if (currentServoPosition === null) return;
  $(`#servo-${button.dataset.useServo}`).value = currentServoPosition;
  toast(`Current position copied to ${button.dataset.useServo}`);
}));

$$('[data-test-servo]').forEach((button) => button.addEventListener("click", async () => {
  const field = $(`#servo-${button.dataset.testServo}`);
  if (field.value === "") return toast("Set that servo position first", true);
  if (!await saveConfig("Servo settings saved")) return;
  try {
    const result = await api("/api/admin/hardware/servo-move", {
      method: "POST",
      body: JSON.stringify({ goal_position: Number(field.value) }),
    });
    currentServoPosition = result.position;
    $("#servo-current").textContent = result.position;
    toast(`Servo target set to ${result.position}`);
  } catch (error) { toast(error.message, true); }
}));

$("#well-list").addEventListener("click", async (event) => {
  const card = event.target.closest(".well-card");
  if (!card) return;
  if (event.target.dataset.capture === "xy") {
    await refreshPosition();
    if (!currentPosition) return;
    $("[data-field='x']", card).value = currentPosition.x;
    $("[data-field='y']", card).value = currentPosition.y;
    toast("Current well X/Y copied");
  }
  if (event.target.dataset.capture === "z") {
    await refreshPosition();
    if (!currentPosition) return;
    const input = event.target.closest("label").querySelector("input");
    input.value = currentPosition.z;
    toast("Current Z copied");
  }
  if (event.target.dataset.saveColor) {
    try {
      const updated = await api(`/api/admin/colors/${card.dataset.colorId}`, { method: "PUT", body: JSON.stringify(colorPayload(card)) });
      state.colors = state.colors.map((color) => color.id === updated.id ? updated : color);
      toast(`${updated.name} well saved`);
    } catch (error) { toast(error.message, true); }
  }
  if (event.target.dataset.deleteColor) {
    if (!confirm("Delete this color? Queued pieces that use it will no longer be printable.")) return;
    try {
      await api(`/api/admin/colors/${card.dataset.colorId}`, { method: "DELETE" });
      state.colors = state.colors.filter((color) => color.id !== card.dataset.colorId);
      renderWells();
      toast("Color deleted");
    } catch (error) { toast(error.message, true); }
  }
});

$("#add-color").addEventListener("click", async () => {
  try {
    const color = await api("/api/admin/colors", {
      method: "POST",
      body: JSON.stringify({ name: "New color", hex: "#f2b84b", x: null, y: null, intake_z: null, purge_z: null, enabled: true }),
    });
    state.colors.push(color);
    renderWells();
    toast("New color added");
  } catch (error) { toast(error.message, true); }
});

$("#print-next").addEventListener("click", async () => {
  if (!confirm("Start the next print now? The printer and pipette will move.")) return;
  try {
    const result = await api("/api/admin/jobs/print-next", { method: "POST", body: "{}" });
    toast(`Started piece #${result.job_id}`);
    await refreshState();
  } catch (error) { toast(error.message, true); await refreshState(); }
});

$("#cancel-print").addEventListener("click", async () => {
  if (!confirm("Stop the current print at the next safe command boundary?")) return;
  await hardwareAction("/api/admin/jobs/cancel-current", "Stop requested");
  await refreshState();
});

async function boot() {
  const authenticated = await refreshState({ populate: true });
  if (authenticated) {
    $("#login-view").hidden = true;
    $("#console-view").hidden = false;
    await refreshPosition();
  }
  setInterval(() => {
    if (!$("#console-view").hidden && $("#tab-queue").classList.contains("active")) refreshState();
  }, 2000);
}

boot();
