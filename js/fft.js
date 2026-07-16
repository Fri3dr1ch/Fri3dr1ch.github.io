// js/fft.js
// Radix-2 iterative Cooley–Tukey FFT. In-place, no per-call allocations.
// Exposes window.KDV.fft.create(N) -> { forward(re,im), inverse(re,im) }.
// forward:  X = Σ x[n] e^{-2πi kn/N}  (no normalization)
// inverse:  x = (1/N) Σ X[k] e^{+2πi kn/N}
(function () {
  "use strict";

  window.KDV = window.KDV || {};

  function isPowerOfTwo(n) {
    return n > 0 && (n & (n - 1)) === 0;
  }

  function create(N) {
    if (!isPowerOfTwo(N)) {
      throw new Error("FFT length must be a power of 2, got " + N);
    }

    // Precomputed bit-reversal permutation.
    var rev = new Int32Array(N);
    var bits = Math.round(Math.log2(N));
    for (var i = 0; i < N; i++) {
      var x = i;
      var r = 0;
      for (var b = 0; b < bits; b++) {
        r = (r << 1) | (x & 1);
        x >>= 1;
      }
      rev[i] = r;
    }

    // Precomputed twiddle factors: W[k] = exp(-2πi k/N) for k = 0..N/2-1.
    var wr = new Float64Array(N / 2);
    var wi = new Float64Array(N / 2);
    for (var k = 0; k < N / 2; k++) {
      var ang = (-2 * Math.PI * k) / N;
      wr[k] = Math.cos(ang);
      wi[k] = Math.sin(ang);
    }

    function bitReverse(re, im) {
      for (var i = 0; i < N; i++) {
        var j = rev[i];
        if (j > i) {
          var tr = re[i];
          re[i] = re[j];
          re[j] = tr;
          var ti = im[i];
          im[i] = im[j];
          im[j] = ti;
        }
      }
    }

    // sign = -1 for forward, +1 for inverse.
    function transform(re, im, sign) {
      bitReverse(re, im);
      for (var size = 2; size <= N; size <<= 1) {
        var half = size >> 1;
        var step = N / size; // stride into twiddle table
        for (var start = 0; start < N; start += size) {
          for (var k = 0; k < half; k++) {
            var wRe = wr[k * step];
            var wIm = sign * wi[k * step]; // conjugate for inverse
            var a = start + k;
            var b = a + half;
            var tRe = re[b] * wRe - im[b] * wIm;
            var tIm = re[b] * wIm + im[b] * wRe;
            re[b] = re[a] - tRe;
            im[b] = im[a] - tIm;
            re[a] = re[a] + tRe;
            im[a] = im[a] + tIm;
          }
        }
      }
    }

    function forward(re, im) {
      transform(re, im, -1);
    }

    function inverse(re, im) {
      transform(re, im, +1);
      var invN = 1 / N;
      for (var i = 0; i < N; i++) {
        re[i] *= invN;
        im[i] *= invN;
      }
    }

    return { N: N, forward: forward, inverse: inverse };
  }

  KDV.fft = { create: create };
})();
