// js/kdv.js
// Spectral solver for KdV with flipped nonlinear sign:
//   u_t - 6 u u_x + u_xxx = 0,  x in [-L/2, L/2), periodic.
//
// Method: Fourier pseudo-spectral + integrating factor + RK4.
//   u_xxx -> (ik)^3 u_hat = -i k^3 u_hat, so the linear part is +i k^3 u_hat
//   (stiff). Integrating factor E(t) = exp(i k^3 t) removes it exactly.
//   Nonlinear part N(u) = 6 * FFT(u * IFFT(i k u_hat)), dealiased 2/3.
//   RK4 on w = exp(-i k^3 t) u_hat.
(function () {
  "use strict";

  window.KDV = window.KDV || {};

  function createSolver(opts) {
    var L = opts.L;
    var N = opts.N;
    var dt = opts.dt;

    if (!KDV.fft) throw new Error("KDV.fft required");
    var fft = KDV.fft.create(N);

    var dx = L / N;
    var k = new Float64Array(N); // wavenumbers, fft-ordered
    for (var n = 0; n <= N / 2; n++) k[n] = (2 * Math.PI * n) / L;
    for (var m = N / 2 + 1; m < N; m++) k[m] = (2 * Math.PI * (m - N)) / L;
    k[N / 2] = 0; // Nyquist ambiguous -> 0

    // i*k (for computing u_x = IFFT(i k u_hat))
    var ik = new Float64Array(N);
    for (var i = 0; i < N; i++) ik[i] = k[i];

    // k^3 (real), used in integrating factor exp(i k^3 t)
    var k3 = new Float64Array(N);
    for (var i = 0; i < N; i++) k3[i] = k[i] * k[i] * k[i];

    // Per-step integrating factor E = exp(i k^3 dt) and its inverse.
    var ERe = new Float64Array(N);
    var EIm = new Float64Array(N);
    var EinvRe = new Float64Array(N);
    var EinvIm = new Float64Array(N);
    for (var i = 0; i < N; i++) {
      var ang = k3[i] * dt;
      ERe[i] = Math.cos(ang);
      EIm[i] = Math.sin(ang);
      EinvRe[i] = Math.cos(ang);
      EinvIm[i] = -Math.sin(ang);
    }

    // 2/3 dealiasing mask.
    var kMax = (N / 3) * (2 * Math.PI / L);
    var mask = new Float64Array(N);
    for (var i = 0; i < N; i++) mask[i] = Math.abs(k[i]) <= kMax ? 1 : 0;

    // Work buffers (reused across steps).
    var u = new Float64Array(N); // current physical u
    var u0 = new Float64Array(N); // initial condition
    var t = 0;

    // Spectrum buffers.
    var wRe = new Float64Array(N); // transformed variable w = E^{-1} u_hat
    var wIm = new Float64Array(N);
    var uhatRe = new Float64Array(N);
    var uhatIm = new Float64Array(N);

    // RK4 stage buffers.
    var k1Re = new Float64Array(N), k1Im = new Float64Array(N);
    var k2Re = new Float64Array(N), k2Im = new Float64Array(N);
    var k3Re = new Float64Array(N), k3Im = new Float64Array(N);
    var k4Re = new Float64Array(N), k4Im = new Float64Array(N);
    var tmpRe = new Float64Array(N), tmpIm = new Float64Array(N);
    var nRe = new Float64Array(N), nIm = new Float64Array(N); // nonlinear N(u_hat)

    // Compute nonlinear term N(u_hat) = 6 * FFT(u * u_x), dealiased.
    // Inputs: uhatRe/Im (current spectrum). Outputs into nRe/nIm.
    // Also needs physical u; we recompute u from uhat to stay consistent.
    function nonlinear(uhatR, uhatI, nR, nI) {
      // u = real(IFFT(uhat))
      var re = tmpRe, im = tmpIm;
      for (var i = 0; i < N; i++) { re[i] = uhatR[i]; im[i] = uhatI[i]; }
      fft.inverse(re, im);
      for (var i = 0; i < N; i++) u[i] = re[i]; // physical u

      // u_x = IFFT(i k u_hat): multiply spectrum by i*k then inverse.
      for (var i = 0; i < N; i++) {
        re[i] = -ik[i] * uhatI[i];
        im[i] = ik[i] * uhatR[i];
      }
      fft.inverse(re, im);
      // re now holds u_x (real).
      var ux = re;
      for (var i = 0; i < N; i++) re[i] = u[i] * ux[i]; // u * u_x
      for (var i = 0; i < N; i++) im[i] = 0;
      fft.forward(re, im);
      for (var i = 0; i < N; i++) {
        nR[i] = 6 * re[i] * mask[i];
        nI[i] = 6 * im[i] * mask[i];
      }
    }

    // RHS of w-equation: F(w) = Einv * N(E * w), where E = exp(i k^3 t).
    // E_t depends on current time t. We pass t explicitly.
    // Writes into outRe/outIm.
    function rhs(wR, wI, time, outR, outI) {
      // u_hat = E(time) * w,  E(time) = exp(i k^3 time)
      for (var i = 0; i < N; i++) {
        var ang = k3[i] * time;
        var c = Math.cos(ang), s = Math.sin(ang);
        uhatRe[i] = c * wR[i] - s * wI[i];
        uhatIm[i] = s * wR[i] + c * wI[i];
      }
      nonlinear(uhatRe, uhatIm, nRe, nIm);
      // out = Einv(time) * N
      for (var i = 0; i < N; i++) {
        var ang2 = k3[i] * time;
        var c2 = Math.cos(ang2), s2 = Math.sin(ang2);
        // conj(E) = exp(-i k^3 time) = (c2, -s2)
        outR[i] = c2 * nRe[i] + s2 * nIm[i];
        outI[i] = -s2 * nRe[i] + c2 * nIm[i];
      }
    }

    function setInitial(u0in) {
      if (u0in.length !== N) throw new Error("u0 length must be N");
      for (var i = 0; i < N; i++) u0[i] = u0in[i];
      reset();
    }

    function reset() {
      for (var i = 0; i < N; i++) u[i] = u0[i];
      t = 0;
      // w = Einv(0) * u_hat = u_hat (since E(0)=1)
      for (var i = 0; i < N; i++) { uhatRe[i] = u0[i]; uhatIm[i] = 0; }
      fft.forward(uhatRe, uhatIm);
      for (var i = 0; i < N; i++) { wRe[i] = uhatRe[i]; wIm[i] = uhatIm[i]; }
    }

    function step() {
      // RK4 on w' = F(w, t), with F depending on time via E(t).
      var half = dt / 2;
      var sixth = dt / 6;

      // k1 = F(w, t)
      rhs(wRe, wIm, t, k1Re, k1Im);
      // k2 = F(w + h/2 k1, t + h/2)
      for (var i = 0; i < N; i++) {
        tmpRe[i] = wRe[i] + half * k1Re[i];
        tmpIm[i] = wIm[i] + half * k1Im[i];
      }
      rhs(tmpRe, tmpIm, t + half, k2Re, k2Im);
      // k3 = F(w + h/2 k2, t + h/2)
      for (var i = 0; i < N; i++) {
        tmpRe[i] = wRe[i] + half * k2Re[i];
        tmpIm[i] = wIm[i] + half * k2Im[i];
      }
      rhs(tmpRe, tmpIm, t + half, k3Re, k3Im);
      // k4 = F(w + h k3, t + h)
      for (var i = 0; i < N; i++) {
        tmpRe[i] = wRe[i] + dt * k3Re[i];
        tmpIm[i] = wIm[i] + dt * k3Im[i];
      }
      rhs(tmpRe, tmpIm, t + dt, k4Re, k4Im);

      // w += h/6 (k1 + 2k2 + 2k3 + k4)
      for (var i = 0; i < N; i++) {
        wRe[i] += sixth * (k1Re[i] + 2 * k2Re[i] + 2 * k3Re[i] + k4Re[i]);
        wIm[i] += sixth * (k1Im[i] + 2 * k2Im[i] + 2 * k3Im[i] + k4Im[i]);
      }
      t += dt;

      // Recover u = real(IFFT(E(t) * w))
      for (var i = 0; i < N; i++) {
        var ang = k3[i] * t;
        var c = Math.cos(ang), s = Math.sin(ang);
        uhatRe[i] = c * wRe[i] - s * wIm[i];
        uhatIm[i] = s * wRe[i] + c * wIm[i];
      }
      var re = tmpRe, im = tmpIm;
      for (var i = 0; i < N; i++) { re[i] = uhatRe[i]; im[i] = uhatIm[i]; }
      fft.inverse(re, im);
      for (var i = 0; i < N; i++) u[i] = re[i];
    }

    function getU() {
      // Return a copy so callers can't mutate internal state.
      var out = new Float64Array(N);
      for (var i = 0; i < N; i++) out[i] = u[i];
      return out;
    }

    return {
      setInitial: setInitial,
      step: step,
      reset: reset,
      getU: getU,
      get t() { return t; },
      get N() { return N; },
      get L() { return L; },
      get dt() { return dt; }
    };
  }

  KDV.kdv = { createSolver: createSolver };
})();
