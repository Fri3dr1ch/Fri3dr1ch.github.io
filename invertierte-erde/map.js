import { landView } from './land-view.js';

const byId = (id) => document.getElementById(id);
const canvas = byId('map');
const context = canvas.getContext('2d', { alpha: true });
const status = byId('map-status');
const zoomButtons = [byId('zoom-in'), byId('zoom-out'), byId('reset')];
const modeButtons = [byId('inverted'), byId('original')];
const rotationToggle = byId('rotate-map');
const landLineToggle = byId('show-land-line');
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });
const coordinate = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 });
const points = [
  { id: 'challenger', lat: 11.3733, lon: 142.5917, height: -10935, approximate: true },
  { id: 'everest', lat: 27.9881, lon: 86.9250, height: 8849, approximate: false },
].map((point) => ({
  ...point,
  pin: byId(`pin-${point.id}`),
  button: byId(`place-${point.id}`),
  role: byId(`role-${point.id}`),
  value: byId(`value-${point.id}`),
}));

const A1 = 1.340264;
const A2 = -.081106;
const A3 = .000893;
const A4 = .003796;
const M = Math.sqrt(3) / 2;
const TO_RADIANS = Math.PI / 180;
const HALF_WIDTH = Math.PI / (M * A1);
const HALF_HEIGHT = projectedNorth(Math.PI / 3);
const MAP_RATIO = HALF_WIDTH / HALF_HEIGHT;
const GRID_WIDTH = 3600;
const GRID_HEIGHT = 1800;
const MAX_ZOOM = 10;
const images = new Map();
const pointers = new Map();

let camera = { x: .5, y: .5, zoom: 1 };
let view = { width: 0, height: 0, pixels: 1 };
let mode = 'invertiert';
let activeImage = null;
let selection = null;
let gesture = null;
let heightGrid = null;
let frame = 0;

function projectedNorth(theta) {
  const squared = theta * theta;
  return theta * (A1 + A2 * squared + squared ** 3 * (A3 + A4 * squared));
}

function derivative(theta) {
  const squared = theta * theta;
  return A1 + 3 * A2 * squared + squared ** 3 * (7 * A3 + 9 * A4 * squared);
}

function sphereVector(lon, lat) {
  const phi = lat * TO_RADIANS;
  const lambda = lon * TO_RADIANS;
  return [Math.cos(phi) * Math.cos(lambda), Math.cos(phi) * Math.sin(lambda), Math.sin(phi)];
}

function sphericalCoordinates([x, y, z]) {
  return {
    lon: Math.atan2(y, x) / TO_RADIANS,
    lat: Math.asin(clamp(z, -1, 1)) / TO_RADIANS,
  };
}

function rotateToMap(lon, lat, axes) {
  if (!axes) return { lon, lat };
  const [x, y, z] = sphereVector(lon, lat);
  return sphericalCoordinates(axes.map(([a, b, c]) => a * x + b * y + c * z));
}

function project(lon, lat) {
  const local = rotateToMap(lon, lat, projection.axes);
  return projectLocal(local.lon, local.lat);
}

function projectLocal(lon, lat) {
  const theta = Math.asin(M * Math.sin(lat * TO_RADIANS));
  const east = Math.cos(theta) * lon * TO_RADIANS / (M * derivative(theta));
  return {
    x: .5 + east / (2 * HALF_WIDTH),
    y: .5 - projectedNorth(theta) / (2 * HALF_HEIGHT),
  };
}

function unproject(x, y) {
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  const north = (.5 - y) * 2 * HALF_HEIGHT;
  let theta = north / A1;
  for (let step = 0; step < 7; step++) {
    theta -= (projectedNorth(theta) - north) / derivative(theta);
  }
  const lon = (x - .5) * 2 * HALF_WIDTH * derivative(theta) * M /
    (Math.cos(theta) * TO_RADIANS);
  if (Math.abs(lon) > 180.000001) return null;
  const lat = Math.asin(clamp(Math.sin(theta) / M, -1, 1)) / TO_RADIANS;
  const localLon = clamp(lon, -180, 180);
  if (!projection.axes) return { lon: localLon, lat };
  const [localX, localY, localZ] = sphereVector(localLon, lat);
  const [center, right, up] = projection.axes;
  return sphericalCoordinates(center.map((value, i) => value * localX + right[i] * localY + up[i] * localZ));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function gridLine(coordinates, axes) {
  const segments = [];
  let segment = [];
  let previous;
  for (const [lon, lat] of coordinates) {
    const local = rotateToMap(lon, lat, axes);
    if (axes && previous && Math.abs(local.lon - previous.lon) > 180) {
      const edge = previous.lon > 0 ? 180 : -180;
      const span = local.lon + 2 * edge - previous.lon;
      const fraction = span === 0 ? .5 : clamp((edge - previous.lon) / span, 0, 1);
      const edgeLat = previous.lat + fraction * (local.lat - previous.lat);
      segment.push(projectLocal(edge, edgeLat));
      segments.push(segment);
      segment = [projectLocal(-edge, edgeLat)];
    }
    segment.push(projectLocal(local.lon, local.lat));
    previous = local;
  }
  segments.push(segment);
  return segments;
}

function geographicGrid(axes) {
  const lines = [];
  for (let lon = -180; lon < 180; lon += 30) {
    if (!axes && lon === -180) continue;
    lines.push(...gridLine(Array.from({ length: 181 }, (_, i) => [lon, -90 + i]), axes));
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    lines.push(...gridLine(Array.from({ length: 361 }, (_, i) => [-180 + i, lat]), axes));
  }
  return lines;
}

const globeCenter = { x: .5, y: .5 };
const landCenter = sphericalCoordinates(landView.axes[0]);
const projections = {
  original: {
    image: 'original',
    label: 'Equal Earth',
    axes: null,
    home: globeCenter,
    grid: geographicGrid(null),
  },
  northUp: {
    image: 'invertiert-nord',
    label: 'Equal Earth · Nord oben',
    axes: landView.northUpAxes,
    home: projectLocal(0, landCenter.lat),
    grid: geographicGrid(landView.northUpAxes),
  },
  rotated: {
    image: 'invertiert',
    label: 'Equal Earth · gedreht',
    axes: landView.axes,
    home: globeCenter,
    grid: geographicGrid(landView.axes),
  },
};
let projection = projections.northUp;
const outline = [
  Array.from({ length: 91 }, (_, i) => projectLocal(-180, 90 - i * 2)),
  Array.from({ length: 91 }, (_, i) => projectLocal(180, -90 + i * 2)),
].flat();

// The arc is the rotated equator. Split it if an endpoint lies across the cut.
const landLine = [-360, 0, 360].flatMap((shift) => {
  const start = Math.max(-180, landView.arc[0] + shift);
  const end = Math.min(180, landView.arc[1] + shift);
  return end > start ? [[projectLocal(start, 0), projectLocal(end, 0)]] : [];
});


function dimensions(zoom = camera.zoom) {
  // Fit the whole globe even when the home latitude sits off the equator.
  const homeY = projection.home.y;
  const verticalSpan = 2 * Math.max(homeY, 1 - homeY);
  const width = Math.max(1, Math.min(view.width - 32, (view.height - 30) * MAP_RATIO / verticalSpan)) * zoom;
  return { width, height: width / MAP_RATIO };
}

function mapRect() {
  const { width, height } = dimensions();
  return {
    width, height,
    x: view.width / 2 - width * camera.x,
    y: view.height / 2 - height * camera.y,
  };
}

function screenPoint(point, rect) {
  return {
    x: rect.x + point.x * rect.width,
    y: rect.y + point.y * rect.height,
  };
}

function polyline(points, rect) {
  const first = screenPoint(points[0], rect);
  context.moveTo(first.x, first.y);
  for (const point of points.slice(1)) {
    const screen = screenPoint(point, rect);
    context.lineTo(screen.x, screen.y);
  }
}

function moveCamera(x, y, zoom = camera.zoom) {
  const home = projection.home;
  if (zoom === 1) {
    camera = { ...home, zoom };
  } else {
    const { width, height } = dimensions(zoom);
    const limitX = Math.min(.5, view.width / (2 * width));
    const marginY = view.height / (2 * height);
    camera = {
      x: clamp(x, limitX, 1 - limitX),
      y: clamp(y, Math.min(home.y, marginY), Math.max(home.y, 1 - marginY)),
      zoom,
    };
  }
  requestDraw();
}

function resetCamera() {
  moveCamera(projection.home.x, projection.home.y, 1);
}

function setProjection(next) {
  if (projection === next) return;
  const center = camera.zoom > 1 ? unproject(camera.x, camera.y) ?? selection : null;
  const width = dimensions().width;
  projection = next;
  if (center) {
    const target = project(center.lon, center.lat);
    moveCamera(target.x, target.y, clamp(width / dimensions(1).width, 1, MAX_ZOOM));
  } else {
    resetCamera();
  }
}

function zoomAt(factor, anchor, destination = anchor) {
  const zoom = clamp(camera.zoom * factor, 1, MAX_ZOOM);
  const rect = mapRect();
  const next = dimensions(zoom);
  const x = (anchor.x - rect.x) / rect.width + (view.width / 2 - destination.x) / next.width;
  const y = (anchor.y - rect.y) / rect.height + (view.height / 2 - destination.y) / next.height;
  moveCamera(x, y, zoom);
}

function centerOfView() {
  return { x: view.width / 2, y: view.height / 2 };
}

function requestDraw() {
  if (!frame) frame = requestAnimationFrame(draw);
}

function resize() {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const pixels = Math.min(devicePixelRatio || 1, 2);
  if (width === view.width && height === view.height && pixels === view.pixels) return;
  view = { width, height, pixels };
  canvas.width = Math.round(width * pixels);
  canvas.height = Math.round(height * pixels);
  moveCamera(camera.x, camera.y);
}

function draw() {
  frame = 0;
  const rect = mapRect();
  context.setTransform(view.pixels, 0, 0, view.pixels, 0, 0);
  context.clearRect(0, 0, view.width, view.height);
  if (activeImage) {
    context.imageSmoothingQuality = 'high';
    context.drawImage(activeImage, rect.x, rect.y, rect.width, rect.height);

    context.lineWidth = .8;
    context.setLineDash([3, 4]);
    context.strokeStyle = '#244c4540';
    context.beginPath();
    for (const line of projection.grid) polyline(line, rect);
    context.stroke();
    context.setLineDash([]);
    context.lineWidth = 1;
    context.strokeStyle = '#3f5f5675';
    context.beginPath();
    polyline(outline, rect);
    context.closePath();
    context.stroke();

    if (projection === projections.rotated && landLineToggle.checked) drawLandLine(rect);

    if (selection && !selection.id) {
      const point = screenPoint(project(selection.lon, selection.lat), rect);
      drawCursor(point, '#27473e', false);
    }
    if (canvas.matches(':focus-visible')) drawCursor(centerOfView(), '#27473e', true);
  }

  for (const point of points) {
    const position = screenPoint(project(point.lon, point.lat), rect);
    const visible = activeImage && position.x >= 10 && position.x <= view.width - 10 &&
      position.y >= 10 && position.y <= view.height - 10;
    point.pin.hidden = !visible;
    if (visible) {
      point.pin.style.left = `${position.x}px`;
      point.pin.style.top = `${position.y}px`;
      point.pin.classList.toggle('label-right', position.x < 128);
    }
  }
  zoomButtons[0].disabled = !activeImage || camera.zoom >= MAX_ZOOM;
  zoomButtons[1].disabled = !activeImage || camera.zoom <= 1;
  zoomButtons[2].disabled = !activeImage || camera.zoom === 1;
}

function drawLandLine(rect) {
  context.beginPath();
  for (const segment of landLine) polyline(segment, rect);
  context.setLineDash([6, 5]);
  context.lineWidth = 3;
  context.strokeStyle = '#fff9e8c7';
  context.stroke();
  context.lineWidth = 1.35;
  context.strokeStyle = '#263f36cf';
  context.stroke();
  context.setLineDash([]);
}

function drawCursor(point, color, cross) {
  context.beginPath();
  context.arc(point.x, point.y, cross ? 6 : 5, 0, 2 * Math.PI);
  if (cross) {
    context.moveTo(point.x - 10, point.y);
    context.lineTo(point.x - 4, point.y);
    context.moveTo(point.x + 4, point.y);
    context.lineTo(point.x + 10, point.y);
    context.moveTo(point.x, point.y - 10);
    context.lineTo(point.x, point.y - 4);
    context.moveTo(point.x, point.y + 4);
    context.lineTo(point.x, point.y + 10);
  }
  context.lineWidth = 3;
  context.strokeStyle = '#fffaf0';
  context.stroke();
  context.lineWidth = 1.5;
  context.strokeStyle = color;
  context.stroke();
}

function imageFor(name) {
  if (!images.has(name)) {
    const image = new Image();
    image.src = `terrain-${name}.webp`;
    const promise = image.decode().then(() => image).catch((error) => {
      images.delete(name);
      throw error;
    });
    images.set(name, promise);
  }
  return images.get(name);
}

function signedHeight(height, approximate = false) {
  const sign = height > 0 ? '+' : height < 0 ? '−' : '';
  return `${approximate ? 'ca. ' : ''}${sign}${number.format(Math.abs(height))} m`;
}

async function showMode(nextMode) {
  mode = nextMode;
  const inverted = mode === 'invertiert';
  const rotated = inverted && rotationToggle.checked;
  const requested = inverted ? (rotated ? projections.rotated : projections.northUp) : projections.original;
  setProjection(requested);
  byId('rotation-control').hidden = !inverted;
  byId('land-line-control').hidden = !rotated;
  byId('projection-label').textContent = projection.label;
  modeButtons[0].setAttribute('aria-pressed', String(inverted));
  modeButtons[1].setAttribute('aria-pressed', String(!inverted));

  for (const point of points) {
    const height = inverted ? -point.height : point.height;
    for (const item of [point.pin, point.button]) {
      item.classList.toggle('high', height > 0);
      item.classList.toggle('low', height < 0);
    }
    point.role.textContent = height > 0 ? 'Höchster Punkt' : 'Tiefster Punkt';
    point.value.textContent = signedHeight(height, point.approximate);
  }

  activeImage = null;
  status.textContent = 'Karte wird geladen …';
  status.hidden = false;
  requestDraw();
  try {
    const image = await imageFor(requested.image);
    if (projection !== requested) return;
    activeImage = image;
    status.hidden = true;
  } catch {
    if (projection !== requested) return;
    status.textContent = 'Die Karte konnte nicht geladen werden. Wähle die Ansicht erneut.';
  }
  requestDraw();
}

async function loadHeights() {
  const response = await fetch('elevation.dat');
  if (!response.ok) throw new Error(`Geländedaten: HTTP ${response.status}`);
  const raw = await response.arrayBuffer();
  const gzip = new Uint8Array(raw, 0, 2);
  const buffer = gzip[0] === 0x1f && gzip[1] === 0x8b
    ? await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()
    : raw;
  if (buffer.byteLength !== GRID_WIDTH * GRID_HEIGHT * 2) throw new Error('Unvollständige Geländedaten');
  const grid = new Int16Array(buffer);
  for (let y = 0; y < GRID_HEIGHT; y++) {
    const start = y * GRID_WIDTH;
    for (let x = 1; x < GRID_WIDTH; x++) {
      grid[start + x] += grid[start + x - 1];
    }
  }
  return grid;
}

async function sampleHeight(point) {
  if (!heightGrid) {
    heightGrid = loadHeights().catch((error) => {
      heightGrid = null;
      throw error;
    });
  }
  const grid = await heightGrid;
  const x = clamp(Math.floor((point.lon + 180) / 360 * GRID_WIDTH), 0, GRID_WIDTH - 1);
  const y = clamp(Math.floor((90 - point.lat) / 180 * GRID_HEIGHT), 0, GRID_HEIGHT - 1);
  return grid[y * GRID_WIDTH + x] * 10;
}

function pointCoordinates({ lat, lon }) {
  return `${coordinate.format(Math.abs(lat))}° ${lat < 0 ? 'S' : 'N'} · ${coordinate.format(Math.abs(lon))}° ${lon < 0 ? 'W' : 'O'}`;
}

function showValues(height, approximate) {
  byId('point-empty').hidden = true;
  byId('point-inverted').textContent = signedHeight(-height, approximate);
  byId('point-original').textContent = signedHeight(height, approximate);
  byId('point-values').hidden = false;
}

async function select(point) {
  selection = point;
  requestDraw();
  for (const marker of points) {
    marker.button.setAttribute('aria-pressed', String(point.id === marker.id));
    marker.pin.classList.toggle('is-selected', point.id === marker.id);
  }
  const coordinates = byId('point-coordinates');
  coordinates.textContent = pointCoordinates(point);
  coordinates.hidden = false;

  if (point.id) {
    showValues(point.height, point.approximate);
    return;
  }

  const message = byId('point-empty');
  message.textContent = 'Höhe wird geladen …';
  message.hidden = false;
  byId('point-values').hidden = true;
  try {
    const height = await sampleHeight(point);
    if (selection === point) showValues(height, true);
  } catch {
    if (selection === point) message.textContent = 'Höhenwert konnte nicht geladen werden.';
  }
}

function selectAt(position) {
  const rect = mapRect();
  const point = unproject((position.x - rect.x) / rect.width, (position.y - rect.y) / rect.height);
  if (point) select(point);
}

function selectLandmark(point, zoomIn) {
  select(point);
  if (zoomIn) {
    const projected = project(point.lon, point.lat);
    moveCamera(projected.x, projected.y, Math.max(camera.zoom, 3));
  }
}

function eventPosition(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function midpoint(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function pan(from, to) {
  const { width, height } = dimensions();
  moveCamera(camera.x + (from.x - to.x) / width, camera.y + (from.y - to.y) / height);
}

canvas.addEventListener('pointerdown', (event) => {
  if (!activeImage || event.button !== 0 || pointers.size >= 2) return;
  const position = eventPosition(event);
  canvas.setPointerCapture(event.pointerId);
  pointers.set(event.pointerId, position);
  if (pointers.size === 1) {
    gesture = { start: position, moved: false };
  } else {
    gesture.moved = true;
    canvas.classList.remove('is-dragging');
  }
});

canvas.addEventListener('pointermove', (event) => {
  if (!pointers.has(event.pointerId)) return;
  const before = [...pointers.values()];
  const position = eventPosition(event);
  pointers.set(event.pointerId, position);
  if (pointers.size === 2) {
    const after = [...pointers.values()];
    const scale = distance(...after) / Math.max(1, distance(...before));
    zoomAt(scale, midpoint(...before), midpoint(...after));
  } else {
    gesture.moved ||= distance(gesture.start, position) > 5;
    if (gesture.moved) {
      canvas.classList.add('is-dragging');
      pan(before[0], position);
    }
  }
});

function endPointer(event) {
  if (!pointers.has(event.pointerId)) return;
  pointers.delete(event.pointerId);
  if (!pointers.size) {
    if (event.type === 'pointerup' && !gesture.moved && distance(gesture.start, eventPosition(event)) <= 5) {
      selectAt(eventPosition(event));
    }
    canvas.classList.remove('is-dragging');
    gesture = null;
  }
}

canvas.addEventListener('pointerup', endPointer);
canvas.addEventListener('pointercancel', endPointer);
canvas.addEventListener('lostpointercapture', endPointer);

canvas.addEventListener('wheel', (event) => {
  if (!activeImage || event.deltaY === 0) return;
  if (!event.ctrlKey && (event.deltaY > 0 && camera.zoom === 1 || event.deltaY < 0 && camera.zoom === MAX_ZOOM)) return;
  event.preventDefault();
  const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? view.height : 1;
  const factor = Math.exp(-clamp(event.deltaY * unit, -130, 130) * .0027);
  zoomAt(factor, eventPosition(event));
}, { passive: false });

canvas.addEventListener('keydown', (event) => {
  if (!activeImage) return;
  const offsets = { ArrowLeft: [45, 0], ArrowRight: [-45, 0], ArrowUp: [0, 45], ArrowDown: [0, -45] };
  const offset = offsets[event.key];
  if (offset) {
    pan({ x: 0, y: 0 }, { x: offset[0], y: offset[1] });
  } else if (event.key === '+' || event.key === '=') {
    zoomAt(1.45, centerOfView());
  } else if (event.key === '-' || event.key === '−') {
    zoomAt(1 / 1.45, centerOfView());
  } else if (event.key === 'Home') {
    resetCamera();
  } else if (event.key === 'Enter' || event.key === ' ') {
    selectAt(centerOfView());
  } else {
    return;
  }
  event.preventDefault();
});

rotationToggle.checked = false;
rotationToggle.addEventListener('change', () => showMode(mode));
landLineToggle.addEventListener('change', requestDraw);
canvas.addEventListener('focus', requestDraw);
canvas.addEventListener('blur', requestDraw);
for (const point of points) {
  point.button.addEventListener('click', () => selectLandmark(point, true));
  point.pin.addEventListener('click', () => selectLandmark(point, false));
}
modeButtons[0].addEventListener('click', () => { if (mode !== 'invertiert' || !activeImage) showMode('invertiert'); });
modeButtons[1].addEventListener('click', () => { if (mode !== 'original' || !activeImage) showMode('original'); });
zoomButtons[0].addEventListener('click', () => zoomAt(1.45, centerOfView()));
zoomButtons[1].addEventListener('click', () => zoomAt(1 / 1.45, centerOfView()));
zoomButtons[2].addEventListener('click', resetCamera);

new ResizeObserver(resize).observe(canvas);
resize();
showMode('invertiert');
