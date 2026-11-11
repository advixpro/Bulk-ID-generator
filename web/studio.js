"use strict";

const $ = (id) => document.getElementById(id);
const key = document.querySelector('meta[name="studio-key"]').content;
const state = {
  pdf: "", templateName: "", count: 0, fonts: [], excel: "", sheet: "",
  data: null, photos: "", output: "", photoScan: null, resolutions: {},
  layout: { pages: [], fields: [] }, images: new Map(), dimensions: new Map(), selected: null,
  tool: "select", pickColor: null, setupId: "", setupName: "",
};
const presets = {
  custom: ["Custom", 0, 0],
  portrait: ["Portrait · 30 × 40 mm", 30, 40],
  passport: ["Passport · 35 × 45 mm", 35, 45],
  square: ["Square · 30 × 30 mm", 30, 30],
  classic: ["Portrait · 2 × 3 in", 50.8, 76.2],
};
const clamp = (value, low, high) => Math.max(low, Math.min(value, high));
const basename = (path) => path.split(/[\\/]/).filter(Boolean).at(-1) || path;
const labelFor = (field) => field.kind === "text" ? field.expression || "Text" :
  field.kind === "photo" ? "Photo" : "Cover Area";
const dialog = $("dialog");
const content = $("dialog-content");

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, callback, className) {
  const node = element("button", text, className);
  node.addEventListener("click", () => run(callback));
  return node;
}

function options(node, choices, value) {
  node.replaceChildren(...choices.map(([key, label]) => new Option(label, key)));
  if (choices.some(([key]) => String(key) === String(value))) node.value = value;
}

function notice(message = "", error = false) {
  $("notice").hidden = !message;
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
}

async function api(action, data = {}, binary = false) {
  const response = await fetch("/api/" + action, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Studio-Key": key },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error((await response.json()).error);
  return binary ? response.blob() : response.json();
}

async function run(callback) {
  try { await callback(); }
  catch (error) {
    notice(error.message, true);
    if (dialog.open) {
      const message = element("p", error.message, "warning");
      content.prepend(message);
    }
  }
}

function showDialog(title, ...children) {
  $("dialog-title").textContent = title;
  content.replaceChildren(...children);
  if (!dialog.open) dialog.showModal();
}

function patternData() {
  const start = $("start").valueAsNumber;
  if (!Number.isSafeInteger(start) || start < 0 || start > 999999999) {
    throw new Error("Enter a whole sequence start from 0 to 999999999.");
  }
  return {
    pdf: state.pdf, templateName: state.templateName, excel: state.excel, sheet: state.sheet,
    photos: state.photos, output: state.output,
    layout: { pages: state.layout.pages, fields: state.layout.fields.filter((field) => state.layout.pages.includes(field.page)) },
    photoColumn: $("match").value, groupColumn: $("group-column").value, resolutions: state.resolutions,
    pattern: $("pattern").value, start,
  };
}

async function readTemplate(path) {
  const info = await api("template", { pdf: path });
  state.pdf = path;
  state.templateName = basename(path);
  state.count = info.count;
  state.fonts = info.fonts;
  state.images.clear();
  state.dimensions.clear();
  $("pdf-name").textContent = state.templateName;
  $("pages").hidden = state.count < 2;
  $("preview").disabled = false;
}

async function pageImage(page, thumb = false) {
  const { image, width, height } = await api("page", { pdf: state.pdf, page, thumb });
  if (!thumb) state.dimensions.set(page, { width, height });
  return image;
}

async function loadPages(pages) {
  await Promise.all(pages.map(async (page) => {
    if (!state.images.has(page)) state.images.set(page, await pageImage(page));
  }));
  state.layout.pages = pages;
  state.selected = null;
  renderCards();
  renderProperties();
  updateHelp();
}

function fieldLabel(text, input) {
  const label = element("label", text);
  label.append(input);
  return label;
}

function pagePicker() {
  const front = element("select");
  const back = element("select");
  const pages = Array.from({ length: state.count }, (_, i) => [i, "Page " + (i + 1)]);
  options(front, pages, state.layout.pages[0] ?? 0);
  options(back, [["", "None · single-sided"], ...pages], state.layout.pages[1] ?? "");
  const fields = element("div", undefined, "group2");
  fields.append(fieldLabel("Front", front), fieldLabel("Back", back));
  const thumbs = element("div", undefined, "thumbnails");
  const lazy = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      lazy.unobserve(entry.target);
      const image = entry.target.querySelector("img");
      pageImage(Number(entry.target.dataset.page), true).then((url) => { image.src = url; })
        .catch(() => { image.alt = "Preview unavailable"; });
    }
  }, { root: dialog });
  for (const [page, label] of pages) {
    const tile = element("div", undefined, "thumbnail");
    tile.dataset.page = page;
    const image = element("img");
    image.alt = label;
    tile.append(image, element("div", label));
    thumbs.append(tile);
    lazy.observe(tile);
  }
  showDialog("Assign PDF pages", element("p", "Choose the front and optional back. The card editor displays them in that order."), fields,
    button("Use these pages", async () => {
      const pages = [Number(front.value)];
      if (back.value !== "") pages.push(Number(back.value));
      if (pages[0] === pages[1]) throw new Error("Select different pages for the front and back.");
      await loadPages(pages);
      dialog.close();
    }, "primary"), thumbs);
}

async function selectTemplate() {
  const { path } = await api("pick", { kind: "pdf" });
  if (!path) return;
  await readTemplate(path);
  state.layout = { pages: [], fields: [] };
  state.selected = null;
  renderCards();
  renderProperties();
  if (state.count === 1) await loadPages([0]);
  else pagePicker();
}

function updateHelp() {
  const descriptions = {
    select: "Drag a box to move it. Drag its corner to resize.",
    text: "Draw a text box on either side of the card.",
    photo: "Draw a photo box, then choose its proportions.",
    cover: "Draw a box over the area to cover.",
  };
  $("tool-help").textContent = state.pickColor ? "Click a color on the original PDF." :
    state.layout.pages.length ? descriptions[state.tool] : "Choose a template to begin";
  document.querySelectorAll("[data-tool]").forEach((node) => {
    if (node.tagName === "BUTTON") node.setAttribute("aria-pressed", node.dataset.tool === state.tool);
    else node.dataset.tool = state.pickColor ? "sample" : state.tool;
  });
}

function layoutRect(box, field) {
  Object.assign(box.style, { left: field.x * 100 + "%", top: field.y * 100 + "%",
    width: field.w * 100 + "%", height: field.h * 100 + "%" });
}

function drawBoxes(surface, page) {
  surface.querySelectorAll(".field").forEach((node) => node.remove());
  const fields = state.layout.fields.filter((field) => field.page === page)
    .sort((a, b) => Number(a.kind !== "cover") - Number(b.kind !== "cover"));
  for (const field of fields) {
    const active = state.selected === field;
    const box = element("div", undefined, "field" + (active ? " active" : ""));
    box.dataset.kind = field.kind;
    box.dataset.id = field.id;
    box.append(element("span", labelFor(field)));
    if (field.kind === "cover") {
      box.style.background = field.gradient
        ? "linear-gradient(" + (field.axis === "vertical" ? "to bottom" : "to right") + "," + field.color + "," + field.endColor + ")"
        : field.color;
    }
    if (active) box.append(element("i", undefined, "handle"));
    layoutRect(box, field);
    surface.append(box);
  }
}

function refreshBoxes() {
  for (const surface of document.querySelectorAll(".surface")) drawBoxes(surface, Number(surface.dataset.page));
}

function renderCards() {
  $("empty").hidden = state.layout.pages.length > 0;
  $("cards").replaceChildren();
  state.layout.pages.forEach((page, index) => {
    const card = element("div", undefined, "card");
    const heading = element("div", undefined, "card-heading");
    heading.append(element("strong", index === 0 ? "Front" : "Back"), element("span", "PDF page " + (page + 1)));
    const surface = element("div", undefined, "surface");
    surface.dataset.page = page;
    surface.dataset.tool = state.tool;
    const image = element("img");
    image.alt = index === 0 ? "Front of the ID template" : "Back of the ID template";
    image.src = state.images.get(page);
    surface.append(image);
    surface.addEventListener("pointerdown", (event) => beginPointer(event, surface, page, image));
    card.append(heading, surface);
    $("cards").append(card);
    drawBoxes(surface, page);
  });
}

function beginPointer(event, surface, page, image) {
  if (event.button !== 0) return;
  const rect = surface.getBoundingClientRect();
  const point = (e) => [clamp((e.clientX - rect.left) / rect.width, 0, 1),
    clamp((e.clientY - rect.top) / rect.height, 0, 1)];
  const [x, y] = point(event);
  if (state.pickColor) {
    const canvas = element("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d");
    context.drawImage(image, Math.min(image.naturalWidth - 1, Math.floor(x * image.naturalWidth)),
      Math.min(image.naturalHeight - 1, Math.floor(y * image.naturalHeight)), 1, 1, 0, 0, 1, 1);
    const rgb = context.getImageData(0, 0, 1, 1).data;
    state.pickColor.field[state.pickColor.key] = "#" + [...rgb.slice(0, 3)].map((c) => c.toString(16).padStart(2, "0")).join("");
    state.pickColor = null;
    updateHelp();
    refreshBoxes();
    renderProperties();
    return;
  }
  const existing = event.target.closest(".field");
  const selected = existing && state.layout.fields.find((field) => field.id === existing.dataset.id);
  const resizing = event.target.classList.contains("handle");
  const adding = state.tool !== "select";
  if (!adding && !selected) {
    state.selected = null;
    renderProperties();
    refreshBoxes();
    return;
  }
  event.preventDefault();
  const field = adding ? {
    id: crypto.randomUUID(), kind: state.tool, page, x, y, w: 0, h: 0,
    ...(state.tool === "text" ? {
      expression: "", font: state.fonts[0] || "Arial", size: 12, minSize: 6,
      direction: "auto", align: "left", color: "#14283f", bold: false, italic: false,
    } : state.tool === "photo" ? {
      fit: "fill", locked: true, preset: "custom", radius: 0,
    } : { color: "#ffffff", gradient: false, endColor: "#ffffff", axis: "horizontal" }),
  } : selected;
  state.selected = field;
  if (adding) state.layout.fields.push(field);
  const original = { x: field.x, y: field.y, w: field.w, h: field.h };
  drawBoxes(surface, page);
  surface.setPointerCapture(event.pointerId);
  const move = (e) => {
    const [nx, ny] = point(e);
    if (adding) Object.assign(field, { x: Math.min(x, nx), y: Math.min(y, ny), w: Math.abs(nx - x), h: Math.abs(ny - y) });
    else if (resizing) {
      let w = clamp(original.w + nx - x, 0.005, 1 - field.x);
      let h = clamp(original.h + ny - y, 0.005, 1 - field.y);
      if (field.locked) {
        const ratio = original.w / original.h;
        w = Math.min(w, (1 - field.y) * ratio);
        h = w / ratio;
      }
      Object.assign(field, { w, h });
    } else {
      field.x = clamp(original.x + nx - x, 0, 1 - field.w);
      field.y = clamp(original.y + ny - y, 0, 1 - field.h);
    }
    layoutRect(surface.querySelector(".field.active"), field);
  };
  const finish = () => {
    surface.removeEventListener("pointermove", move);
    surface.removeEventListener("pointerup", finish);
    surface.removeEventListener("pointercancel", finish);
    if (adding && (field.w < .004 || field.h < .004)) {
      state.layout.fields = state.layout.fields.filter((item) => item !== field);
      state.selected = null;
    }
    if (adding) state.tool = "select";
    updateHelp();
    refreshBoxes();
    renderProperties();
  };
  surface.addEventListener("pointermove", move);
  surface.addEventListener("pointerup", finish);
  surface.addEventListener("pointercancel", finish);
}

function property(field, name, key, type, choices) {
  const input = element(choices ? "select" : "input");
  if (choices) options(input, choices, field[key]);
  else {
    input.type = type;
    if (type === "checkbox") input.checked = field[key];
    else input.value = field[key];
    if (type === "number") input.step = "0.1";
  }
  input.addEventListener(key === "expression" ? "input" : "change", () => {
    field[key] = choices ? input.value : type === "checkbox" ? input.checked :
      type === "number" ? (Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : field[key]) : input.value;
    refreshBoxes();
    if (key === "gradient" || key === "font") renderProperties();
    if (key === "expression") renderLayers();
  });
  return fieldLabel(name, input);
}

function colorProperty(field, name, key) {
  const label = property(field, name, key, "color");
  const row = element("div", undefined, "inline");
  row.append(label.querySelector("input"), button("Pick", () => {
    state.pickColor = { field, key };
    updateHelp();
  }));
  label.append(row);
  return label;
}

function geometry(field) {
  const group = element("div", undefined, "group2");
  const { width, height } = state.dimensions.get(field.page);
  for (const [key, title, scale] of [
    ["x", "Left %", 100], ["y", "Top %", 100],
    ["w", "Width (mm)", width * 25.4 / 72], ["h", "Height (mm)", height * 25.4 / 72],
  ]) {
    const input = element("input");
    input.type = "number";
    input.step = ".1";
    input.value = (field[key] * scale).toFixed(2);
    input.addEventListener("change", () => {
      const value = input.valueAsNumber / scale;
      if (!Number.isFinite(value)) return renderProperties();
      const ratio = field.w / field.h;
      if (key === "x") field.x = clamp(value, 0, 1 - field.w);
      if (key === "y") field.y = clamp(value, 0, 1 - field.h);
      if (key === "w") {
        field.w = clamp(value, .001, Math.min(1 - field.x, field.locked ? (1 - field.y) * ratio : 1));
        if (field.locked) field.h = field.w / ratio;
      }
      if (key === "h") {
        field.h = clamp(value, .001, Math.min(1 - field.y, field.locked ? (1 - field.x) / ratio : 1));
        if (field.locked) field.w = field.h * ratio;
      }
      refreshBoxes();
      renderProperties();
    });
    group.append(fieldLabel(title, input));
  }
  return group;
}

function renderLayers() {
  const layers = $("layers");
  const fields = state.layout.fields.filter((field) => state.layout.pages.includes(field.page));
  layers.replaceChildren();
  $("field-count").textContent = fields.length;
  for (const field of fields) {
    const side = state.layout.pages[0] === field.page ? "Front" : "Back";
    layers.append(button(side + " · " + labelFor(field), () => {
      state.selected = field;
      state.tool = "select";
      updateHelp();
      refreshBoxes();
      renderProperties();
    }, "layer" + (state.selected === field ? " active" : "")));
  }
}

function insertToken(input, token) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.setRangeText(token, start, end, "end");
  input.focus();
  input.dispatchEvent(new Event("input"));
}

function renderProperties() {
  const panel = $("properties");
  const field = state.selected;
  panel.replaceChildren();
  renderLayers();
  $("property-description").textContent = field ? labelFor(field) : "Select a field on the card to edit its placement and style.";
  if (!field) return;
  if (field.kind === "text") {
    const expression = property(field, "Text or column expression", "expression", "text");
    const input = expression.querySelector("input");
    input.placeholder = "{Name AR} - {Name EN}";
    const variable = element("select");
    options(variable, (state.data?.columns || []).map((column) => [column, column]));
    variable.setAttribute("aria-label", "Text variable");
    const insert = element("div", undefined, "inline");
    insert.append(variable, button("Insert", () => {
      if (variable.value) insertToken(input, "{" + variable.value + "}");
    }));
    panel.append(expression, insert,
      property(field, "Font", "font", "", state.fonts.map((font) => [font, font])),
      property(field, "Bold", "bold", "checkbox"),
      ...(field.font === "Noto Kufi Arabic" ? [] : [property(field, "Italic", "italic", "checkbox")]),
      property(field, "Preferred size (pt)", "size", "number"),
      property(field, "Minimum size (pt)", "minSize", "number"),
      property(field, "Reading direction", "direction", "", [["auto", "Automatic"], ["ltr", "Left to right"], ["rtl", "Right to left"]]),
      property(field, "Alignment", "align", "", [["left", "Left"], ["center", "Center"], ["right", "Right"]]),
      colorProperty(field, "Text color", "color"));
  } else if (field.kind === "photo") {
    const preset = element("select");
    options(preset, Object.entries(presets).map(([key, entry]) => [key, entry[0]]), field.preset);
    preset.addEventListener("change", () => {
      field.preset = preset.value;
      if (field.preset !== "custom") {
        const [, mmW, mmH] = presets[field.preset];
        const { width, height } = state.dimensions.get(field.page);
        const w = mmW * 72 / 25.4 / width;
        const h = mmH * 72 / 25.4 / height;
        const scale = Math.min(1, 1 / w, 1 / h);
        field.w = w * scale;
        field.h = h * scale;
        field.x = Math.min(field.x, 1 - field.w);
        field.y = Math.min(field.y, 1 - field.h);
        field.locked = true;
      }
      refreshBoxes();
      renderProperties();
    });
    panel.append(fieldLabel("Size preset", preset), property(field, "Lock aspect ratio", "locked", "checkbox"),
      property(field, "Image fit", "fit", "", [["fill", "Fill · crop to frame"], ["fit", "Fit · show entire photo"]]),
      property(field, "Auto Framing", "autoFraming", "checkbox"),
      property(field, "Use off-white background", "offWhite", "checkbox"),
      property(field, "Crop camera watermarks", "cropWatermark", "checkbox"),
      property(field, "Corner radius (pt)", "radius", "number"));
  } else {
    panel.append(colorProperty(field, "Fill color", "color"), property(field, "Linear gradient", "gradient", "checkbox"));
    if (field.gradient) panel.append(colorProperty(field, "End color", "endColor"),
      property(field, "Gradient direction", "axis", "", [["horizontal", "Left to right"], ["vertical", "Top to bottom"]]));
  }
  panel.append(geometry(field), button("Remove field", () => {
    state.layout.fields = state.layout.fields.filter((item) => item !== field);
    state.selected = null;
    refreshBoxes();
    renderProperties();
  }, "danger"));
}

function loadMatchOptions(preferred) {
  const columns = state.data?.columns || [];
  const choices = columns.map((column) => [column, column]);
  if (preferred && !columns.includes(preferred)) {
    choices.unshift([preferred, preferred + (state.data ? " · missing" : " · saved")]);
  }
  const match = $("match");
  options(match, [["", "Select a column"], ...choices], preferred);
  match.disabled = !state.data;
  options($("filename-column"), columns.length ? columns.map((column) => [column, column]) : [["", "Excel column"]]);
  loadGroupOptions($("group-column").value);
}

function loadGroupOptions(preferred) {
  const columns = state.data?.columns || [];
  const choices = columns.map((column) => [column, column]);
  if (preferred && !columns.includes(preferred)) {
    choices.unshift([preferred, preferred + (state.data ? " · missing" : " · saved")]);
  }
  const select = $("group-column");
  options(select, [["", "No grouping"], ...choices], preferred);
  select.disabled = !state.data;
}

async function loadExcel(path, sheet = "", preferred = $("match").value) {
  const data = await api("excel", { excel: path, sheet });
  state.excel = path;
  state.sheet = data.sheet;
  state.data = data;
  $("excel-name").textContent = basename(path) + " · " + data.total.toLocaleString() + " records";
  options($("sheet"), data.sheets.map((name) => [name, name]), data.sheet);
  $("sheet").disabled = false;
  loadMatchOptions(preferred || data.columns.find((column) => column.toLowerCase() === "employee id"));
  options($("sample"), data.samples.map(({ row }, index) => [index, "Excel row " + row]));
  $("sample").disabled = !data.samples.length;
  renderProperties();
  await updateDuplicates();
  if (state.photos) await scanPhotos(false);
}

async function updateDuplicates() {
  const node = $("duplicate-rows");
  node.hidden = true;
  const photoColumn = $("match").value;
  if (!photoColumn || !state.excel) return;
  const groups = await api("duplicates", { excel: state.excel, sheet: state.sheet, photoColumn });
  const entries = Object.entries(groups);
  if (!entries.length) return;
  const examples = entries.slice(0, 4).map(([value, rows]) => value + " (rows " + rows.slice(0, 8).join(", ") + (rows.length > 8 ? ", …" : "") + ")");
  node.textContent = entries.length + " duplicate matching value(s). Affected rows will be skipped. " + examples.join(" · ");
  node.hidden = false;
}

async function scanPhotos(review) {
  $("photo-status").textContent = "Checking image content…";
  try {
    state.photoScan = await api("photos", {
      photos: state.photos, excel: state.excel, sheet: state.sheet, photoColumn: $("match").value,
    });
    updatePhotoStatus();
    if (review && (state.photoScan.groups.length || Object.keys(state.photoScan.invalid).length)) reviewPhotos();
  } catch (error) {
    state.photoScan = null;
    $("photo-status").textContent = "Photo check failed.";
    throw error;
  }
}

function updatePhotoStatus() {
  const { count, groups, invalid } = state.photoScan;
  const unresolved = groups.filter((group) => !Object.hasOwn(state.resolutions, group.key)).length;
  const parts = [count.toLocaleString() + " photos"];
  if (groups.length) parts.push(unresolved ? unresolved + " identical groups need review" : "Identical groups resolved");
  if (Object.keys(invalid).length) parts.push(Object.keys(invalid).length + " unreadable");
  $("photo-status").textContent = parts.join(" · ");
  $("photo-review").hidden = !groups.length && !Object.keys(invalid).length;
}

function reviewPhotos() {
  if (!state.photoScan) return;
  const { groups, invalid } = state.photoScan;
  const parts = [element("p", "Files in each group have identical image content. The first file is listed in alphabetical order. Omitting a file can leave its matched record without a photo.")];
  if (Object.keys(invalid).length) {
    parts.push(element("p", "Unreadable photos: " + Object.keys(invalid).join(", "), "warning"));
  }
  for (const [index, group] of groups.entries()) {
    const section = element("section", undefined, "photo-group");
    const header = element("h3", "Group " + (index + 1));
    const row = element("div", undefined, "photo-group-files");
    const controls = [];
    const note = element("p", "Choose how to resolve this group.", "muted");
    const thumbnail = api("thumbnail", { photos: state.photos, name: group.files[0].name });
    for (const file of group.files) {
      const item = element("label", undefined, "thumbnail");
      const check = element("input");
      check.type = "checkbox";
      check.checked = state.resolutions[group.key]?.includes(file.name) ?? true;
      controls.push(check);
      const image = element("img");
      image.alt = "Preview of " + file.name;
      item.append(image);
      thumbnail.then(({ image: src }) => { image.src = src; })
        .catch(() => { image.alt = "Preview unavailable for " + file.name; });
      item.append(check, element("span", " Keep " + file.name),
        element("div", file.rows.length ? "Excel rows " + file.rows.join(", ") : "No matched row"));
      row.append(item);
    }
    function apply(keep) {
      state.resolutions[group.key] = keep;
      controls.forEach((check, i) => { check.checked = keep.includes(group.files[i].name); });
      const affected = [...new Set(group.files.filter((file) => !keep.includes(file.name)).flatMap((file) => file.rows))];
      note.textContent = keep.length + " of " + group.files.length + " photos kept" +
        (affected.length ? ". Rows potentially affected: " + affected.join(", ") + "." : ".");
      updatePhotoStatus();
    }
    const actions = element("div", undefined, "dialog-actions");
    actions.append(button("Keep all", () => apply(group.files.map((file) => file.name))),
      button("Keep only first", () => apply([group.files[0].name])),
      button("Apply checked photos", () => apply(group.files.filter((_, i) => controls[i].checked).map((file) => file.name))));
    section.append(header, row, actions, note);
    if (Object.hasOwn(state.resolutions, group.key)) apply(state.resolutions[group.key]);
    parts.push(section);
  }
  parts.push(button("Done", () => {
    if (groups.some((group) => !Object.hasOwn(state.resolutions, group.key))) {
      throw new Error("Choose an option for every identical-photo group.");
    }
    dialog.close();
  }, "primary"));
  showDialog("Review photographs", ...parts);
}

async function saveSetup() {
  if (!state.pdf || !state.layout.pages.length) throw new Error("Choose the template and card pages first.");
  const name = element("input");
  name.value = state.setupName;
  name.maxLength = 100;
  const id = state.setupId;
  const save = async (overwrite) => {
    const result = await api("save", { ...patternData(), id: overwrite ? id : "", name: name.value });
    state.setupId = result.id;
    state.setupName = result.name;
    state.pdf = result.pdf;
    $("setup-label").textContent = result.name;
    dialog.close();
    notice("Saved setup “" + result.name + "”.");
  };
  const controls = element("div", undefined, "dialog-actions");
  if (id) controls.append(button("Update setup", () => save(true), "primary"), button("Save as new setup", () => save(false)));
  else controls.append(button("Save setup", () => save(false), "primary"));
  showDialog("Save Setup", fieldLabel("Setup name", name), controls);
  name.focus();
  name.select();
}

async function openSetup() {
  const setups = await api("list");
  const list = setups.map((item) => button(item.name, async () => {
    const setup = await api("open", { id: item.id });
    await readTemplate(setup.pdf);
    state.setupName = setup.name;
    state.setupId = setup.id;
    state.templateName = setup.templateName;
    state.excel = state.photos = state.output = "";
    state.data = state.photoScan = null;
    state.resolutions = {};
    state.sheet = setup.sheet;
    state.layout = setup.layout;
    $("setup-label").textContent = setup.name;
    $("pdf-name").textContent = setup.templateName;
    $("excel-name").textContent = "No workbook selected";
    $("photos-name").textContent = $("output-name").textContent = "No folder selected";
    $("photo-status").textContent = "";
    $("photo-review").hidden = $("duplicate-rows").hidden = true;
    $("sheet").disabled = $("sample").disabled = true;
    $("sheet").replaceChildren();
    $("sample").replaceChildren();
    $("pattern").value = setup.pattern;
    $("start").value = setup.start;
    loadMatchOptions(setup.photoColumn);
    loadGroupOptions(setup.groupColumn);
    await loadPages(setup.layout.pages);
    dialog.close();
    notice("Opened “" + setup.name + "”. Choose the files for this batch.");
  }, "layer"));
  showDialog("Open Setup", ...(list.length ? list : [element("p", "No setups saved yet.")]));
}

async function preview() {
  const data = patternData();
  if (!data.layout.pages.length) throw new Error("Choose the card pages first.");
  notice("Rendering the selected card…");
  const result = await api("preview", { ...data, sample: Number($("sample").value || 0) });
  notice();
  const elements = [element("p", result.filename ? "Proposed export path · " + result.filename : "Load Excel to preview employee data.")];
  if (result.notice) elements.push(element("p", result.notice, "warning"));
  result.images.forEach((src, index) => {
    const side = index === 0 ? "Front" : "Back";
    const image = element("img", undefined, "preview-image");
    image.src = src;
    image.alt = side + " preview";
    elements.push(element("strong", side), image);
  });
  showDialog("Card preview", ...elements);
}

async function exportCards() {
  const data = patternData();
  if (!data.pdf || !data.layout.pages.length || !data.excel || !data.output) {
    throw new Error("Choose a PDF template, Excel workbook, and output folder.");
  }
  if (!data.layout.fields.length) throw new Error("Add at least one field to the card.");
  if (data.layout.fields.some((field) => field.kind === "photo") && (!data.photos || !data.photoColumn)) {
    throw new Error("Choose a photo folder and the Excel column that matches the filenames.");
  }
  if (state.photoScan?.groups.some((group) => !Object.hasOwn(state.resolutions, group.key))) {
    reviewPhotos();
    return;
  }
  $("export").disabled = true;
  try {
    const { id } = await api("export", data);
    const progress = element("progress");
    const message = element("p", "Checking your batch…");
    showDialog("Generating cards", message, progress);
    for (;;) {
      const job = await api("job", { id });
      if (job.status === "failed") throw new Error(job.error);
      if (job.status === "complete") {
        showResults(id, job.result);
        break;
      }
      if (job.total) {
        progress.max = job.total;
        progress.value = job.done;
        message.textContent = job.done.toLocaleString() + " of " + job.total.toLocaleString() + " records checked";
        notice("Exporting · " + message.textContent);
      }
      await new Promise((resolve) => setTimeout(resolve, 450));
    }
  } finally {
    $("export").disabled = false;
  }
}

function showResults(id, result) {
  const metrics = element("div", undefined, "result-grid");
  for (const [name, field] of [["Records", "total"], ["Generated", "generated"], ["Skipped", "skipped"], ["Renamed", "renamed"]]) {
    const metric = element("div", undefined, "metric");
    metric.append(element("strong", result[field].toLocaleString()), element("span", name));
    metrics.append(metric);
  }
  const reasons = element("ul");
  Object.entries(result.reasons).forEach(([reason, count]) => reasons.append(element("li", count + " · " + reason)));
  const reviewCount = result.review ?? 0;
  const review = element("p", reviewCount.toLocaleString() + " generated PDFs saved in For Review.", "warning");
  review.hidden = reviewCount === 0;
  Object.entries(result.reviewReasons ?? {}).forEach(([reason, count]) => reasons.append(element("li", count + " · " + reason)));
  const report = button("Save Detailed Report", async () => {
    const blob = await api("report", { id }, true);
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = "ID Card Export Report.xlsx";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, "primary");
  showDialog("Export complete", metrics, review, reasons, report);
  notice(result.generated.toLocaleString() + " PDFs generated. " + result.skipped.toLocaleString() + " records skipped.");
}

const actions = {
  pdf: selectTemplate, "empty-pdf": selectTemplate, pages: pagePicker, preview, export: exportCards,
  open: openSetup, save: saveSetup, "photo-review": reviewPhotos,
  excel: async () => {
    const { path } = await api("pick", { kind: "excel" });
    if (path) {
      const info = await api("excel", { excel: path });
      await loadExcel(path, !state.data && info.sheets.includes(state.sheet) ? state.sheet : info.sheet);
    }
  },
  photos: async () => {
    const { path } = await api("pick", { kind: "photos" });
    if (!path) return;
    state.photos = path;
    state.resolutions = {};
    state.photoScan = null;
    $("photos-name").textContent = basename(path);
    await scanPhotos(true);
  },
  output: async () => {
    const { path } = await api("pick", { kind: "output" });
    if (path) {
      state.output = path;
      $("output-name").textContent = path;
    }
  },
  "add-filename": () => {
    const column = $("filename-column").value;
    if (column) insertToken($("pattern"), "{" + column + "}");
  },
  "dialog-close": () => dialog.close(),
};
for (const [id, action] of Object.entries(actions)) $(id).addEventListener("click", () => run(action));
$("sheet").addEventListener("change", () => run(() => loadExcel(state.excel, $("sheet").value)));
$("match").addEventListener("change", () => run(async () => {
  await updateDuplicates();
  if (state.photos) await scanPhotos(false);
}));
document.querySelectorAll("button[data-tool]").forEach((node) => node.addEventListener("click", () => {
  state.tool = node.dataset.tool;
  state.pickColor = null;
  updateHelp();
}));
loadMatchOptions("");
