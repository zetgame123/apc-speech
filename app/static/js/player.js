/* Локальный аудиоплеер звонков на WebAudio (без WaveSurfer).
 *
 * Почему не WaveSurfer и не <audio>: в реальном Chromium проверено по CDP,
 * что wavesurfer.load(blobUrl) вечно висит (fetch(blob:) резался CSP, а после
 * снятия запрета MEDIA-элемент всё равно не отдаёт loadedmetadata в headless —
 * ни типированный blob, ни синтетический WAV; при этом decodeAudioData те же
 * байты декодирует). Поэтому: fetch с Authorization → arrayBuffer →
 * decodeAudioData → AudioBufferSourceNode. Подсветка реплик/слов, таймлайн и
 * таймер едут на rAF-тике плеера. Наружных запросов нет, CSP не трогаем.
 *
 * Файл грузится до dashboard.js; в Node (тесты загрузки) лежит рядом и не
 * трогает DOM/AudioContext на верхнем уровне.
 */
/* var (а не const): модули грузятся в node-тесте через indirect eval, где
 * const не виден между eval-вызовами, а function/var — видны. */
var Player = (function () {
  'use strict';

  let actx = null;
  let buf = null;
  let src = null;
  let srcGen = 0;
  let startCtx = 0;
  let offsetSec = 0;
  let playing = false;
  let tickTimer = 0;
  let lastDrawMs = 0;
  let peaks = null;
  let canvas = null;
  let canvasCtx = null;

  // Колбэки страницы (назначает dashboard.js).
  const api = {
    onTimeUpdate: null, // (currentTimeSec, durationSec)
    onReady: null, // (durationSec)
    onError: null, // (message)
  };

  function ensureCtx() {
    if (!actx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      actx = new AC();
    }
    if (actx.state === 'suspended' && typeof actx.resume === 'function') {
      const p = actx.resume();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    }
    return actx;
  }

  function ensureCanvas() {
    if (canvas) return true;
    const box = document.getElementById('waveform');
    if (!box) return false;
    box.innerHTML = '';
    canvas = document.createElement('canvas');
    canvas.className = 'wave-canvas';
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Форма сигнала. Клик — перемотка.');
    box.appendChild(canvas);
    canvasCtx = canvas.getContext('2d');
    canvas.addEventListener('click', (e) => {
      const rect = canvas.getBoundingClientRect();
      const ratio = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0;
      api.setTime(ratio * api.getDuration());
    });
    return true;
  }

  function fitCanvas() {
    if (!canvas) return;
    const box = document.getElementById('waveform');
    const w = box && box.clientWidth ? box.clientWidth : 800;
    const h = 90;
    const dpr = (typeof window.devicePixelRatio === 'number' && window.devicePixelRatio > 0)
      ? window.devicePixelRatio
      : 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = '100%';
    canvas.style.height = h + 'px';
  }

  function computePeaks(data, buckets) {
    const out = new Float32Array(buckets);
    const step = Math.max(1, Math.floor(data.length / buckets));
    for (let b = 0; b < buckets; b++) {
      const start = b * step;
      const end = Math.min(data.length, start + step);
      let m = 0;
      // Разреженный проход по ведру: точный максимум не нужен, форма — да.
      for (let i = start; i < end; i += 7) {
        const v = data[i] < 0 ? -data[i] : data[i];
        if (v > m) m = v;
      }
      out[b] = m > 1 ? 1 : m;
    }
    return out;
  }

  function drawWave(progressRatio) {
    if (!canvasCtx || !canvas) return;
    const W = canvas.width;
    const H = canvas.height;
    canvasCtx.clearRect(0, 0, W, H);
    const mid = H / 2;
    if (!peaks || peaks.length === 0) {
      canvasCtx.fillStyle = '#94a3b8';
      canvasCtx.fillRect(0, mid - 1, W, 2);
      return;
    }
    const n = peaks.length;
    const barW = Math.max(1, Math.floor(W / n));
    const played = Math.floor(n * Math.min(1, Math.max(0, progressRatio || 0)));
    for (let i = 0; i < n; i++) {
      const amp = peaks[i];
      const bh = Math.max(2, amp * (H - 8));
      canvasCtx.fillStyle = i < played ? '#0284c7' : '#94a3b8';
      canvasCtx.fillRect(i * barW, mid - bh / 2, Math.max(1, barW - 1), bh);
    }
  }

  function stopSource() {
    srcGen += 1;
    if (src) {
      try {
        src.onended = null;
        src.stop();
      } catch (e) {
        /* уже остановлен */
      }
      try {
        src.disconnect();
      } catch (e) {
        /* не подключён */
      }
      src = null;
    }
  }

  function cancelTick() {
    if (tickTimer) {
      if (typeof clearInterval === 'function') clearInterval(tickTimer);
      tickTimer = 0;
    }
  }

  function emitTime() {
    if (typeof api.onTimeUpdate === 'function') {
      try {
        api.onTimeUpdate(api.getCurrentTime(), api.getDuration());
      } catch (e) {
        /* подсветка не должна ронять воспроизведение */
      }
    }
  }

  // Тик — на setInterval, а не rAF: rAF встаёт в фоновой вкладке и в headless,
  // а подсветка реплик и таймер должны ехать, пока идёт звук.
  function tick() {
    cancelTick();
    emitTime();
    if (typeof setInterval !== 'function') return;
    lastDrawMs = 0;
    tickTimer = setInterval(() => {
      if (!playing) return;
      const t = api.getCurrentTime();
      const d = api.getDuration();
      if (d > 0 && t >= d) {
        // Естественный конец записи.
        offsetSec = d;
        playing = false;
        stopSource();
        emitTime();
        drawWave(1);
        return;
      }
      emitTime();
      drawWave(d > 0 ? t / d : 0);
    }, 200);
  }

  api.getDuration = function () {
    return buf ? buf.duration : 0;
  };

  api.getCurrentTime = function () {
    if (!buf) return 0;
    if (!playing || !actx) return Math.min(offsetSec, buf.duration);
    const t = offsetSec + (actx.currentTime - startCtx);
    return Math.min(Math.max(0, t), buf.duration);
  };

  api.isPlaying = function () {
    return playing;
  };

  api.loadBlob = async function (blob) {
    stopSource();
    cancelTick();
    playing = false;
    offsetSec = 0;
    buf = null;
    peaks = null;
    try {
      const ab = await blob.arrayBuffer();
      const ctx = ensureCtx();
      buf = await ctx.decodeAudioData(ab);
      const ch = buf.numberOfChannels > 1 ? 1 : 0;
      const data = buf.getChannelData(Math.min(ch, buf.numberOfChannels - 1));
      peaks = computePeaks(data, 1200);
      if (ensureCanvas()) {
        fitCanvas();
        drawWave(0);
      }
      if (typeof api.onReady === 'function') api.onReady(buf.duration);
    } catch (e) {
      if (typeof api.onError === 'function') api.onError('decode');
      throw e;
    }
  };

  api.play = function () {
    if (!buf || playing) return false;
    const ctx = ensureCtx();
    stopSource();
    src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const gen = srcGen;
    src.onended = () => {
      // Ручной stop() инкрементит srcGen — чужой узел игнорируем.
      if (playing && gen === srcGen) {
        offsetSec = api.getDuration();
        playing = false;
        emitTime();
        drawWave(1);
      }
    };
    const off = Math.min(offsetSec, buf.duration);
    try {
      src.start(0, off >= buf.duration ? 0 : off);
    } catch (e) {
      return false;
    }
    if (off >= buf.duration) offsetSec = 0;
    startCtx = ctx.currentTime;
    playing = true;
    lastDrawMs = 0;
    tick();
    return true;
  };

  api.pause = function () {
    if (!playing) return;
    offsetSec = api.getCurrentTime();
    playing = false;
    stopSource();
    cancelTick();
    emitTime();
    drawWave(api.getDuration() > 0 ? offsetSec / api.getDuration() : 0);
  };

  api.toggle = function () {
    if (playing) api.pause();
    else api.play();
  };

  api.setTime = function (sec) {
    if (!buf) return;
    const d = buf.duration;
    const v = Math.min(Math.max(0, Number(sec) || 0), d);
    const wasPlaying = playing;
    if (wasPlaying) {
      stopSource();
      offsetSec = v;
      startCtx = ensureCtx().currentTime;
      src = ensureCtx().createBufferSource();
      src.buffer = buf;
      src.connect(ensureCtx().destination);
      const gen = srcGen;
      src.onended = () => {
        if (playing && gen === srcGen) {
          offsetSec = api.getDuration();
          playing = false;
          emitTime();
          drawWave(1);
        }
      };
      try {
        src.start(0, v);
      } catch (e) {
        playing = false;
      }
      lastDrawMs = 0;
      tick();
    } else {
      offsetSec = v;
    }
    emitTime();
    drawWave(d > 0 ? v / d : 0);
  };

  return api;
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { Player: Player };
}
