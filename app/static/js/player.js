/* Локальный аудиоплеер звонков: два бэкенда.
 *
 * Короткие записи — WebAudio целиком (fetch с Authorization → arrayBuffer →
 * decodeAudioData → AudioBufferSourceNode): мгновенная подсветка и waveform.
 * Длинные записи (порог LONG_AUDIO_SEC/LONG_AUDIO_BYTES) — MEDIA-элемент
 * (<audio> с blob-URL) потоково: декодирование часа 16 кГц моно целиком
 * съедает +671 МБ RSS вкладки (замер CDP 2026-10-16), а MEDIA-элемент держит
 * порядка размера файла. Подсветка реплик/слов, таймлайн и таймер в обоих
 * путях едут на 200-мс тике плеера. Наружных запросов нет.
 *
 * Замечание о <audio> + blob: ранний вывод «MEDIA-элемент не работает»
 * был неверным — виноват был CSP media-src без blob: («Media load rejected
 * by URL safety check»); по http-URL тот же файл отдавал duration сразу.
 *
 * Файл грузится до dashboard.js; в Node (тесты загрузки) лежит рядом и не
 * трогает DOM/AudioContext на верхнем уровне.
 */
/* var (а не const): модули грузятся в node-тесте через indirect eval, где
 * const не виден между eval-вызовами, а function/var — видны. */

/* Чистый разбор заголовка WAV PCM (без декодирования): для длинных записей
 * длительность и пики waveform извлекаются сканированием байт, а не
 * decodeAudioData. prefixBytes — первые ~64 КБ файла. Возвращает null, если
 * это не PCM WAV или чанк data не найден в префиксе. */
function parseWavInfo(prefixBytes) {
  const b = prefixBytes instanceof Uint8Array ? prefixBytes : new Uint8Array(prefixBytes);
  if (b.length < 44) return null;
  const str = (o, n) => String.fromCharCode.apply(null, b.subarray(o, o + n));
  if (str(0, 4) !== 'RIFF' || str(8, 4) !== 'WAVE' || str(12, 4) !== 'fmt ') return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.length);
  if (dv.getUint16(20, true) !== 1) return null; // только PCM
  const channels = dv.getUint16(22, true);
  const sampleRate = dv.getUint32(24, true);
  const bits = dv.getUint16(34, true);
  if (!channels || !sampleRate || (bits !== 8 && bits !== 16 && bits !== 24 && bits !== 32)) {
    return null;
  }
  let off = 12 + 8 + dv.getUint32(16, true);
  while (off + 8 <= b.length) {
    const id = str(off, 4);
    const len = dv.getUint32(off + 4, true);
    if (id === 'data') {
      const bytesPerSec = (sampleRate * channels * bits) / 8;
      if (!bytesPerSec) return null;
      return {
        sampleRate: sampleRate,
        channels: channels,
        bits: bits,
        dataOff: off + 8,
        dataLen: len,
        durationSec: len / bytesPerSec,
      };
    }
    off += 8 + len + (len % 2);
  }
  return null;
}

/* Пики waveform сканированием PCM-байт (без декодирования).
 * bytes — ПОЛНЫЙ файл, info — из parseWavInfo. */
function wavPeaks(bytes, info, buckets) {
  const dataLen = Math.min(info.dataLen, bytes.length - info.dataOff);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.length);
  const out = new Float32Array(buckets);
  const bytesPerSample = info.bits / 8;
  const frame = bytesPerSample * info.channels;
  const totalFrames = Math.floor(dataLen / frame);
  if (totalFrames <= 0) return out;
  const perBucket = Math.max(1, Math.floor(totalFrames / buckets));
  const norm = info.bits === 8 ? 128 : info.bits === 16 ? 32768 : info.bits === 24 ? 8388608 : 2147483648;
  for (let k = 0; k < buckets; k++) {
    let m = 0;
    const start = k * perBucket;
    const end = Math.min(totalFrames, start + perBucket);
    for (let f = start; f < end; f += 11) {
      const pos = info.dataOff + f * frame; // первый канал
      let v;
      if (info.bits === 8) v = (dv.getUint8(pos) - 128) / norm;
      else if (info.bits === 16) v = dv.getInt16(pos, true) / norm;
      else if (info.bits === 24) {
        v = (dv.getUint8(pos) | (dv.getUint8(pos + 1) << 8) | (dv.getInt8(pos + 2) << 16)) / norm;
      } else v = dv.getInt32(pos, true) / norm;
      const a = v < 0 ? -v : v;
      if (a > m) m = a;
    }
    out[k] = m > 1 ? 1 : m;
  }
  return out;
}

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
  // Бэкенды: 'webaudio' (декодирование целиком — короткие записи) и 'media'
  // (<audio> с blob-URL — длинные записи, без декодирования в память).
  // Порог — по длительности или размеру (см. LONG_AUDIO_SEC/LONG_AUDIO_BYTES).
  let backend = 'webaudio';
  let mediaEl = null;
  let mediaUrl = null;
  // Длинные записи: декодирование целиком съедает сотни мегабайт
  // (час 16 кГц моно: +671 МБ RSS вкладки, замер CDP 2026-10-16) — их ведёт
  // MEDIA-элемент потоково. Blob-URL разрешён CSP (media-src 'self' blob:).
  const LONG_AUDIO_SEC = 600;
  const LONG_AUDIO_BYTES = 32 * 1024 * 1024;

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
      // Длинная запись без PCM-пиков: трек + прогресс вместо формы сигнала.
      const p = Math.min(1, Math.max(0, progressRatio || 0));
      canvasCtx.fillStyle = '#e2e8f0';
      canvasCtx.fillRect(0, mid - 3, W, 6);
      canvasCtx.fillStyle = '#0284c7';
      canvasCtx.fillRect(0, mid - 3, Math.round(W * p), 6);
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

  function teardownMedia() {
    if (mediaEl) {
      try {
        mediaEl.pause();
      } catch (e) {
        /* уже остановлен */
      }
      try {
        mediaEl.removeAttribute('src');
      } catch (e) {
        /* не задан */
      }
      mediaEl = null;
    }
    if (mediaUrl) {
      try {
        URL.revokeObjectURL(mediaUrl);
      } catch (e) {
        /* уже отозван */
      }
      mediaUrl = null;
    }
  }

  function teardown() {
    stopSource();
    cancelTick();
    teardownMedia();
    playing = false;
    offsetSec = 0;
    buf = null;
    peaks = null;
    backend = 'webaudio';
  }

  // Дешёвый route: длительность без декодирования (только заголовки формата).
  function probeDuration(blob, timeoutMs) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const el = new Audio();
      el.preload = 'metadata';
      const done = (fn) => {
        try {
          URL.revokeObjectURL(url);
        } catch (e) {
          /* игнорируем */
        }
        fn();
      };
      const to = setTimeout(() => done(() => reject(new Error('probe-timeout'))), timeoutMs || 15000);
      el.onloadedmetadata = () => {
        clearTimeout(to);
        const d = el.duration;
        done(() => {
          if (Number.isFinite(d)) resolve(d);
          else reject(new Error('probe-nan'));
        });
      };
      el.onerror = () => {
        clearTimeout(to);
        done(() => reject(new Error('probe-error')));
      };
      el.src = url;
    });
  }

  async function loadViaWebAudio(blob) {
    const ab = await blob.arrayBuffer();
    const ctx = ensureCtx();
    buf = await ctx.decodeAudioData(ab);
    const ch = buf.numberOfChannels > 1 ? 1 : 0;
    const data = buf.getChannelData(Math.min(ch, buf.numberOfChannels - 1));
    peaks = computePeaks(data, 1200);
    backend = 'webaudio';
    if (ensureCanvas()) {
      fitCanvas();
      drawWave(0);
    }
    if (typeof api.onReady === 'function') api.onReady(buf.duration);
  }

  async function loadViaMedia(blob, wavInfo) {
    teardown();
    mediaUrl = URL.createObjectURL(blob);
    mediaEl = new Audio();
    mediaEl.preload = 'auto';
    const dur = await new Promise((resolve, reject) => {
      const to = setTimeout(() => reject(new Error('media-timeout')), 30000);
      mediaEl.onloadedmetadata = () => {
        clearTimeout(to);
        resolve(mediaEl.duration);
      };
      mediaEl.onerror = () => {
        clearTimeout(to);
        reject(new Error('media-error'));
      };
      mediaEl.src = mediaUrl;
    });
    if (!Number.isFinite(dur) || dur <= 0) throw new Error('media-nan');
    mediaEl.onended = () => {
      playing = false;
      offsetSec = dur;
      cancelTick();
      emitTime();
      drawWave(1);
    };
    if (wavInfo) {
      // Пики — сканированием байт (без decodeAudioData): быстро и дёшево.
      try {
        const all = new Uint8Array(await blob.arrayBuffer());
        const info = {
          sampleRate: wavInfo.sampleRate,
          channels: wavInfo.channels,
          bits: wavInfo.bits,
          dataOff: wavInfo.dataOff,
          dataLen: Math.min(wavInfo.dataLen, all.length - wavInfo.dataOff),
        };
        peaks = wavPeaks(all, info, 1200);
      } catch (e) {
        peaks = null;
      }
    } else {
      peaks = null;
    }
    backend = 'media';
    if (ensureCanvas()) {
      fitCanvas();
      drawWave(0);
    }
    if (typeof api.onReady === 'function') api.onReady(dur);
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
    if (backend === 'media') {
      if (!mediaEl || !Number.isFinite(mediaEl.duration)) return 0;
      return mediaEl.duration;
    }
    return buf ? buf.duration : 0;
  };

  api.getCurrentTime = function () {
    if (backend === 'media') {
      return mediaEl ? mediaEl.currentTime || 0 : 0;
    }
    if (!buf) return 0;
    if (!playing || !actx) return Math.min(offsetSec, buf.duration);
    const t = offsetSec + (actx.currentTime - startCtx);
    return Math.min(Math.max(0, t), buf.duration);
  };

  api.isPlaying = function () {
    return playing;
  };

  // Имя бэкенда для диагностики и живых проверок ('webaudio' | 'media').
  api.backendName = function () {
    return backend;
  };

  api.loadBlob = async function (blob) {
    teardown();
    try {
      // Сначала дешёвые сигналы, без декодирования.
      if (blob.size > LONG_AUDIO_BYTES) {
        await loadViaMedia(blob, null);
        return;
      }
      let info = null;
      try {
        const head = new Uint8Array(await blob.slice(0, 65536).arrayBuffer());
        info = parseWavInfo(head);
      } catch (e) {
        info = null;
      }
      if (info && info.durationSec > LONG_AUDIO_SEC) {
        await loadViaMedia(blob, info);
        return;
      }
      if (!info) {
        // Не WAV PCM: длительность — дешёвым probe, чтобы не декодировать часы вслепую.
        try {
          const dur = await probeDuration(blob);
          if (dur > LONG_AUDIO_SEC) {
            await loadViaMedia(blob, null);
            return;
          }
        } catch (e) {
          /* probe не удался — пробуем декодировать как раньше */
        }
      }
      await loadViaWebAudio(blob);
    } catch (e) {
      if (typeof api.onError === 'function') api.onError('decode');
      throw e;
    }
  };

  api.play = function () {
    if (backend === 'media') return mediaPlay();
    return waPlay();
  };

  function mediaPlay() {
    if (!mediaEl || playing) return false;
    try {
      const p = mediaEl.play();
      // play() — промис: отклонение (autoplay-политика) не врёт статусом,
      // время просто не пойдёт до следующего клика.
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (e) {
      return false;
    }
    playing = true;
    tick();
    return true;
  }

  function waPlay() {
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
  }

  api.pause = function () {
    if (!playing) return;
    if (backend === 'media') {
      try {
        mediaEl.pause();
      } catch (e) {
        /* игнорируем */
      }
      playing = false;
      cancelTick();
      emitTime();
      const d = api.getDuration();
      drawWave(d > 0 ? api.getCurrentTime() / d : 0);
      return;
    }
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
    if (backend === 'media') {
      if (!mediaEl || !Number.isFinite(mediaEl.duration)) return;
      const v = Math.min(Math.max(0, Number(sec) || 0), mediaEl.duration);
      try {
        mediaEl.currentTime = v;
      } catch (e) {
        return;
      }
      emitTime();
      drawWave(mediaEl.duration > 0 ? v / mediaEl.duration : 0);
      return;
    }
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
  module.exports = { Player: Player, parseWavInfo: parseWavInfo, wavPeaks: wavPeaks };
}
