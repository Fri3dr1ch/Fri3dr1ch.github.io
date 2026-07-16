// js/draw.js
// Drawing canvas: user draws y = f(x) on [-2,2] x [-1,1].
// Returns the curve sampled on the solver grid via sample().
(function () {
  "use strict";

  window.KDV = window.KDV || {};

  // Shared visual constants (duplicated in plot.js to keep modules independent).
  var COLORS = {
    bg: "#0f1226",
    grid: "#262b4d",
    axis: "#4a518f",
    axisLabel: "#8a92c8",
    stroke: "#5ce1e6",
    strokeMuted: "#3a8a8e",
    overlay: "#8a92c8"
  };

  function init(canvas, opts) {
    var L = opts.L;
    var N = opts.N;
    var W = 600, H = 300;
    canvas.width = W;
    canvas.height = H;
    var ctx = canvas.getContext("2d");

    // x in [-2,2] -> px in [0,W]; y in [-1,1] -> px in [H,0]
    function xToPx(x) { return ((x + 2) / 4) * W; }
    function yToPx(y) { return H / 2 - (y / 1) * (H / 2); }
    function pxToX(px) { return (px / W) * 4 - 2; }
    function pxToY(py) { return 1 - (py / H) * 2; }

    // Strokes: array of arrays of {x,y}. Last stroke is "current".
    var strokes = [];
    var drawing = false;

    function isEmpty() { return strokes.length === 0; }

    function clear() {
      strokes = [];
      drawing = false;
      render();
    }

    function drawAxes() {
      // Background
      ctx.fillStyle = COLORS.bg;
      ctx.fillRect(0, 0, W, H);

      // Gridlines every 0.5 in x and y
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (var gx = -2; gx <= 2.001; gx += 0.5) {
        var px = xToPx(gx);
        ctx.moveTo(px, 0); ctx.lineTo(px, H);
      }
      for (var gy = -1; gy <= 1.001; gy += 0.5) {
        var py = yToPx(gy);
        ctx.moveTo(0, py); ctx.lineTo(W, py);
      }
      ctx.stroke();

      // Axes (x=0, y=0)
      ctx.strokeStyle = COLORS.axis;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      var cx = xToPx(0), cy = yToPx(0);
      ctx.moveTo(cx, 0); ctx.lineTo(cx, H);
      ctx.moveTo(0, cy); ctx.lineTo(W, cy);
      ctx.stroke();

      // Tick labels
      ctx.fillStyle = COLORS.axisLabel;
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (var tx = -2; tx <= 2.001; tx += 1) {
        if (tx === 0) continue;
        ctx.fillText(tx.toFixed(0), xToPx(tx), yToPx(0) + 4);
      }
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (var ty = -1; ty <= 1.001; ty += 0.5) {
        if (ty === 0) continue;
        ctx.fillText(ty.toFixed(1), xToPx(0) - 4, yToPx(ty));
      }
    }

    function render() {
      drawAxes();
      for (var s = 0; s < strokes.length; s++) {
        var stroke = strokes[s];
        var isLast = s === strokes.length - 1;
        ctx.strokeStyle = isLast ? COLORS.stroke : COLORS.strokeMuted;
        ctx.lineWidth = isLast ? 2.5 : 1.5;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.beginPath();
        for (var i = 0; i < stroke.length; i++) {
          var p = stroke[i];
          var px = xToPx(p.x), py = yToPx(p.y);
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.stroke();
      }
    }

    function addPoint(px, py) {
      var x = pxToX(px), y = pxToY(py);
      if (x < -2) x = -2; if (x > 2) x = 2;
      if (y < -1) y = -1; if (y > 1) y = 1;
      var stroke = strokes[strokes.length - 1];
      stroke.push({ x: x, y: y });
    }

    function pointerPos(e) {
      var rect = canvas.getBoundingClientRect();
      var cx, cy;
      if (e.touches && e.touches.length) {
        cx = e.touches[0].clientX; cy = e.touches[0].clientY;
      } else {
        cx = e.clientX; cy = e.clientY;
      }
      var px = ((cx - rect.left) / rect.width) * W;
      var py = ((cy - rect.top) / rect.height) * H;
      return { px: px, py: py };
    }

    function onDown(e) {
      e.preventDefault();
      drawing = true;
      strokes.push([]);
      var p = pointerPos(e);
      addPoint(p.px, p.py);
      render();
    }
    function onMove(e) {
      if (!drawing) return;
      e.preventDefault();
      var p = pointerPos(e);
      addPoint(p.px, p.py);
      render();
    }
    function onUp(e) {
      if (!drawing) return;
      e.preventDefault();
      drawing = false;
    }

    canvas.addEventListener("mousedown", onDown);
    canvas.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("touchstart", onDown, { passive: false });
    canvas.addEventListener("touchmove", onMove, { passive: false });
    canvas.addEventListener("touchend", onUp, { passive: false });

    // Sample the drawn curve onto the solver grid x[j] = -2 + j*dx.
    // For each grid x, take the last-drawn y among strokes covering that x
    // (linear interp within a stroke). Uncovered -> 0.
    function sample() {
      var dx = L / N;
      var u0 = new Float64Array(N);
      for (var j = 0; j < N; j++) {
        var xj = -2 + j * dx;
        var val = 0;
        var found = false;
        // Iterate strokes in order; later strokes override earlier.
        for (var s = 0; s < strokes.length; s++) {
          var stroke = strokes[s];
          for (var i = 0; i < stroke.length - 1; i++) {
            var p0 = stroke[i], p1 = stroke[i + 1];
            var x0 = p0.x, x1 = p1.x;
            var lo = Math.min(x0, x1), hi = Math.max(x0, x1);
            if (xj >= lo && xj <= hi) {
              var t = (x1 !== x0) ? (xj - x0) / (x1 - x0) : 0;
              val = p0.y + t * (p1.y - p0.y);
              found = true;
            }
          }
        }
        u0[j] = found ? val : 0;
      }
      return u0;
    }

    // Load a preset function: clear strokes, draw fn sampled at ~200 points.
    function loadPreset(fn) {
      strokes = [];
      var pts = 200;
      var stroke = [];
      for (var i = 0; i <= pts; i++) {
        var x = -2 + (i / pts) * 4;
        var y = fn(x);
        if (y < -1) y = -1; if (y > 1) y = 1;
        stroke.push({ x: x, y: y });
      }
      strokes.push(stroke);
      render();
    }

    // Initial render.
    render();

    return {
      clear: clear,
      sample: sample,
      loadPreset: loadPreset,
      isEmpty: isEmpty,
      _render: render
    };
  }

  KDV.draw = { init: init, COLORS: COLORS };
})();
