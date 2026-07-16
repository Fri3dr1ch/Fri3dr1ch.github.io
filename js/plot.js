// js/plot.js
// Solution animation canvas: draws u(x,t) in the same 4x2 box as draw.js.
// No internal loop — main.js owns the rAF loop and calls drawFrame.
(function () {
  "use strict";

  window.KDV = window.KDV || {};

  var COLORS = KDV.draw.COLORS;

  function init(canvas, opts) {
    var L = opts.L;
    var N = opts.N;
    var W = 600, H = 300;
    canvas.width = W;
    canvas.height = H;
    var ctx = canvas.getContext("2d");

    var overlay = null; // Float64Array length N, or null

    function xToPx(x) { return ((x + 2) / 4) * W; }
    function yToPx(y) { return H / 2 - (y / 1) * (H / 2); }

    function drawAxes() {
      ctx.fillStyle = COLORS.bg;
      ctx.fillRect(0, 0, W, H);

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

      ctx.strokeStyle = COLORS.axis;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      var cx = xToPx(0), cy = yToPx(0);
      ctx.moveTo(cx, 0); ctx.lineTo(cx, H);
      ctx.moveTo(0, cy); ctx.lineTo(W, cy);
      ctx.stroke();

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

    function drawCurve(u, color, lineWidth, alpha) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      var dx = L / N;
      for (var j = 0; j < N; j++) {
        var x = -2 + j * dx;
        var y = u[j];
        if (y > 1) y = 1; if (y < -1) y = -1;
        var px = xToPx(x), py = yToPx(y);
        if (j === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      // Close the loop visually (periodic).
      ctx.lineTo(xToPx(2), yToPx(u[0] > 1 ? 1 : (u[0] < -1 ? -1 : u[0])));
      ctx.stroke();
      ctx.restore();
    }

    function drawFrame(u, t) {
      drawAxes();
      if (overlay) drawCurve(overlay, COLORS.overlay, 1.5, 0.25);
      drawCurve(u, COLORS.stroke, 2.5, 1);

      // Time readout
      ctx.fillStyle = COLORS.axisLabel;
      ctx.font = "12px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "top";
      ctx.fillText("t = " + t.toFixed(3), W - 8, 8);
    }

    function clear() {
      overlay = null;
      drawAxes();
    }

    function setOverlay(u0) {
      overlay = u0;
    }

    return {
      drawFrame: drawFrame,
      clear: clear,
      setOverlay: setOverlay
    };
  }

  KDV.plot = { init: init };
})();
