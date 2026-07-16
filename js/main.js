// js/main.js
// Orchestration: wire DOM -> modules, own the rAF animation loop.
(function () {
  "use strict";

  window.KDV = window.KDV || {};

  // ---- Parameters ----
  var L = 4;
  var N = 256;
  var dt = 1e-3;
  var stepsPerFrame = 5;     // sim-time per frame = 5e-3
  var T_MAX = 2;

  // ---- Preset initial conditions (periodic-friendly) ----
  var PRESETS = {
    sine: {
      label: "Sine wave",
      fn: function (x) { return 0.5 * Math.sin((Math.PI * x) / 2); }
    },
    twomode: {
      label: "Two-mode sine",
      fn: function (x) {
        return 0.4 * (Math.sin((Math.PI * x) / 2) + 0.5 * Math.sin(Math.PI * x));
      }
    },
    gaussian: {
      label: "Narrow Gaussian",
      fn: function (x) {
        var sigma = 0.3;
        return -0.8 * Math.exp(-((x / sigma) * (x / sigma)));
      }
    },
    cosbump: {
      label: "Cos² bump",
      fn: function (x) { return 0.7 * Math.cos((Math.PI * x) / 4) * Math.cos((Math.PI * x) / 4); }
    }
  };

  var solver, drawCtrl, plotCtrl;
  var rafId = null;
  var playing = false;

  function $(id) { return document.getElementById(id); }

  function init() {
    solver = KDV.kdv.createSolver({ L: L, N: N, dt: dt });
    drawCtrl = KDV.draw.init($("drawCanvas"), { L: L, N: N });
    plotCtrl = KDV.plot.init($("plotCanvas"), { L: L, N: N });

    // Buttons
    $("btnSolve").addEventListener("click", onSolve);
    $("btnClear").addEventListener("click", onClear);
    $("btnPlay").addEventListener("click", togglePlay);
    $("btnReset").addEventListener("click", onReset);

    // Preset buttons
    var presetGroup = $("presetGroup");
    Object.keys(PRESETS).forEach(function (key) {
      var btn = document.createElement("button");
      btn.className = "btn btn-preset";
      btn.textContent = PRESETS[key].label;
      btn.addEventListener("click", function () {
        drawCtrl.loadPreset(PRESETS[key].fn);
        // Auto-solve on preset click for immediate feedback.
        onSolve();
      });
      presetGroup.appendChild(btn);
    });

    // Initial state: load sine preset so the page isn't empty.
    drawCtrl.loadPreset(PRESETS.sine.fn);
    setPlayLabel(false);
    updateStatus("Ready. Draw a curve or pick a preset, then Solve.");
  }

  function onSolve() {
    if (drawCtrl.isEmpty()) {
      updateStatus("Draw a curve first (or pick a preset).");
      return;
    }
    var u0 = drawCtrl.sample();
    solver.setInitial(u0);
    plotCtrl.setOverlay(u0);
    plotCtrl.drawFrame(solver.getU(), 0);
    setPlaying(true);
    updateStatus("Solving…");
  }

  function onClear() {
    setPlaying(false);
    drawCtrl.clear();
    plotCtrl.clear();
    updateStatus("Cleared.");
  }

  function onReset() {
    setPlaying(false);
    solver.reset();
    plotCtrl.drawFrame(solver.getU(), 0);
    updateStatus("Reset to t=0. Press Play to resume.");
  }

  function togglePlay() {
    setPlaying(!playing);
  }

  function setPlaying(v) {
    playing = v;
    if (playing) {
      loop();
    } else {
      if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      if (solver.t < T_MAX) updateStatus("Paused.");
    }
    setPlayLabel(playing);
  }

  function setPlayLabel(isPlaying) {
    $("btnPlay").textContent = isPlaying ? "Pause" : "Play";
  }

  function loop() {
    if (!playing) return;
    for (var i = 0; i < stepsPerFrame; i++) {
      if (solver.t >= T_MAX) break;
      solver.step();
    }
    plotCtrl.drawFrame(solver.getU(), solver.t);
    if (solver.t >= T_MAX) {
      setPlaying(false);
      updateStatus("Reached t = " + T_MAX.toFixed(2) + ". Done.");
      return;
    }
    rafId = requestAnimationFrame(loop);
  }

  function updateStatus(msg) {
    var el = $("status");
    if (el) el.textContent = msg;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
