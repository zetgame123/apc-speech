/* Конвертация микрофонной записи в WAV PCM16 16 кГц моно для /v1/voice/search.
 *
 * Причина: MediaRecorder в Chromium отдаёт webm/opus, а быстрый путь
 * распознавания (T-one в worker-fast) принимает только PCM WAV («file does
 * not start with RIFF id» → 500 «Ошибка распознавания голосового запроса»,
 * проверено живьём 2026-10-15). WAV 16 кГц моно идёт и быстрым путём без
 * серверного ffmpeg — в плюс к SLA ≤ 2,5 с.
 *
 * Чистые функции (encode/resample) — без DOM, покрыты Node-тестом;
 * decodeToMono16k использует AudioContext и вызывается из обработчиков
 * записи с fallback на исходный blob при любой ошибке.
 */
function resampleTo16k(data, fromRate) {
  const toRate = 16000;
  if (!data || data.length === 0) return new Float32Array(0);
  if (fromRate === toRate) return Float32Array.from(data);
  if (!(fromRate > 0)) return Float32Array.from(data);
  const ratio = fromRate / toRate;
  const outLen = Math.max(1, Math.floor(data.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = data[idx] || 0;
    const b = data[idx + 1] !== undefined ? data[idx + 1] : a;
    out[i] = a + (b - a) * frac;
  }
  return out;
}

function mixToMono(channels) {
  if (!channels || channels.length === 0) return new Float32Array(0);
  if (channels.length === 1) return Float32Array.from(channels[0]);
  const len = Math.min(...channels.map((c) => c.length));
  const out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    let s = 0;
    for (let c = 0; c < channels.length; c++) s += channels[c][i];
    out[i] = s / channels.length;
  }
  return out;
}

/* Кодирует моно float [-1,1] в байты WAV PCM16 16 кГц (Uint8Array). */
function encodeWavPcm16(mono16k) {
  const n = mono16k.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const writeStr = (off, s) => {
    for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  dv.setUint32(4, 36 + n * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true); // PCM
  dv.setUint16(22, 1, true); // mono
  dv.setUint32(24, 16000, true);
  dv.setUint32(28, 16000 * 2, true); // byte rate
  dv.setUint16(32, 2, true); // block align
  dv.setUint16(34, 16, true); // bits
  writeStr(36, 'data');
  dv.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, mono16k[i]));
    dv.setInt16(44 + i * 2, v < 0 ? Math.round(v * 32768) : Math.round(v * 32767), true);
  }
  return new Uint8Array(buf);
}

/* Декодирует запись MediaRecorder (webm/opus) в WAV-blob 16 кГц моно.
 * Бросает исключение при недоступности WebAudio — вызыватель откатывается
 * на исходный blob. */
async function decodeToMono16kWav(arrayBuffer) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error('WebAudio недоступен');
  const ctx = new AC();
  try {
    // slice: decodeAudioData забирает буфер (detached) — не портим исходный.
    const decoded = await ctx.decodeAudioData(arrayBuffer.slice(0));
    const channels = [];
    for (let c = 0; c < decoded.numberOfChannels; c++) {
      channels.push(decoded.getChannelData(c));
    }
    const mono = mixToMono(channels);
    const mono16k = resampleTo16k(mono, decoded.sampleRate);
    return new Blob([encodeWavPcm16(mono16k)], { type: 'audio/wav' });
  } finally {
    if (ctx.state !== 'closed' && typeof ctx.close === 'function') {
      try {
        await ctx.close();
      } catch (e) {
        /* игнорируем */
      }
    }
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    resampleTo16k: resampleTo16k,
    mixToMono: mixToMono,
    encodeWavPcm16: encodeWavPcm16,
  };
}
