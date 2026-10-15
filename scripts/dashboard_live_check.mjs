#!/usr/bin/env node
// Живая проверка дашборда в настоящем Chromium по CDP (п.6 ручной проверки).
// Без внешних зависимостей: только встроенные fetch/WebSocket/child_process.
// Падает при любом securitypolicyviolation, console.error или необработанном
// исключении; проходит сценарии: вход по токену, журнал, карточка,
// проигрывание, разбор/pending, сводка, кластеры, голос с фейковым микрофоном.
//
// Требуется: поднятый стек и Chromium в PATH. Запуск: make live-check.
// Env: DASHBOARD_BASE (default http://127.0.0.1:8000),
//   DASHBOARD_TOKEN (обязателен), DASHBOARD_NAMESPACE (default dashboard_live),
//   CDP_PORT (default 9334), CHROMIUM_BIN (default chromium),
//   FAKE_WAV (default data/evaluation_suite/sample_clean_01_clean.wav),
//   FIXTURE_WAV (default data/evaluation_suite/sample_dialogue_01_telephony_8k.wav),
//   OUT_DIR (default reports/dashboard-audit).
// Коды выхода: 0 — всё зелёное; 1 — дефект (печатается причина);
//   2 — SKIP (нет chromium/сервера/токена).
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = process.env.DASHBOARD_BASE || 'http://127.0.0.1:8000';
const TOKEN = process.env.DASHBOARD_TOKEN || '';
const NS = process.env.DASHBOARD_NAMESPACE || 'dashboard_live';
const CDP_PORT = process.env.CDP_PORT || '9334';
const CDP = `http://127.0.0.1:${CDP_PORT}`;
const CHROMIUM = process.env.CHROMIUM_BIN || 'chromium';
const OUT_DIR = process.env.OUT_DIR || 'reports/dashboard-audit';
const REPO = process.cwd();
const FAKE_WAV = process.env.FAKE_WAV || path.join(REPO, 'data/evaluation_suite/sample_clean_01_clean.wav');
const FIXTURE_WAV = process.env.FIXTURE_WAV || path.join(REPO, 'data/evaluation_suite/sample_dialogue_01_telephony_8k.wav');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const failures = [];

// RSS renderer-процессов запущенного guard-браузера (только его профиль).
// Нужен для сценария длинного файла: ловим раздувание памяти декодированием.
function rendererRssMb() {
  try {
    const profile = globalThis.__liveProfile || '';
    if (!profile || !/^[-a-zA-Z0-9_/.]+$/.test(profile)) return 0;
    const ps = execFileSync('ps', ['-eo', 'pid,args']).toString();
    const pids = [];
    for (const line of ps.split('\n')) {
      if (!line.includes(profile) || !line.includes('--type=renderer')) continue;
      const m = line.trim().match(/^(\d+)\s/);
      if (m) pids.push(m[1]);
    }
    if (!pids.length) return 0;
    let sum = 0;
    for (const pid of pids) {
      if (!/^\d+$/.test(pid)) continue;
      try {
        const status = execFileSync('grep', ['VmRSS', `/proc/${pid}/status`]).toString();
        const num = status.replace(/\D+/g, '');
        if (num) sum += parseInt(num, 10);
      } catch (e) { /* гонка */ }
    }
    return Math.round(sum / 1024);
  } catch (e) {
    return 0;
  }
}
function check(cond, label, extra = '') {
  if (cond) console.log(`  ok: ${label}`);
  else failures.push(`${label}${extra ? ` (${extra})` : ''}`);
  return cond;
}

async function cdpUp(tries = 40) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`${CDP}/json/version`);
      if (r.ok) return true;
    } catch (e) { /* ждём */ }
    await sleep(500);
  }
  return false;
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let id = 0;
    const pending = new Map();
    const handlers = [];
    const api = {
      send(m, p = {}) {
        return new Promise((res, rej) => {
          const mid = ++id;
          pending.set(mid, { res, rej });
          ws.send(JSON.stringify({ id: mid, method: m, params: p }));
        });
      },
      onEvent(fn) { handlers.push(fn); },
      close() { try { ws.close(); } catch (e) { /* ignore */ } },
    };
    const timer = setTimeout(() => reject(new Error('CDP connect timeout')), 15000);
    ws.onopen = () => { clearTimeout(timer); resolve(api); };
    ws.onerror = () => { clearTimeout(timer); reject(new Error('CDP connect failed')); };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const h = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) h.rej(new Error(JSON.stringify(msg.error)));
        else h.res(msg.result);
      } else if (msg.method) handlers.forEach((h) => h(msg));
    };
  });
}

async function main() {
  // Префлайт без запуска браузера.
  try {
    await import('node:child_process');
  } catch (e) { /* unreachable */ }
  const hasChromium = (() => {
    try {
      const r = spawnSync(CHROMIUM, ['--version'], { stdio: 'ignore' });
      return r.status === 0;
    } catch (e) {
      return false;
    }
  })();
  if (!hasChromium) { console.log('SKIP: нет chromium в PATH'); process.exit(2); }
  if (!TOKEN) { console.log('SKIP: задайте DASHBOARD_TOKEN'); process.exit(2); }
  try {
    const h = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(8000) });
    if (!h.ok) throw new Error(`health ${h.status}`);
  } catch (e) { console.log(`SKIP: API недоступен (${e.message})`); process.exit(2); }
  for (const f of [FAKE_WAV, FIXTURE_WAV]) {
    if (!fs.existsSync(f)) { console.log(`SKIP: нет файла ${f}`); process.exit(2); }
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'apc-dash-live-'));
  globalThis.__liveProfile = profile;
  const child = spawn(CHROMIUM, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${profile}`,
    '--autoplay-policy=no-user-gesture-required',
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream',
    `--use-file-for-fake-audio-capture=${FAKE_WAV}`,
    'about:blank',
  ], { stdio: 'ignore' });
  const killBrowser = () => { try { child.kill('SIGKILL'); } catch (e) { /* ignore */ } };
  process.on('exit', killBrowser);
  if (!await cdpUp()) { killBrowser(); console.log('SKIP: CDP не поднялся'); process.exit(2); }

  const badEvents = [];
  const httpErrors = [];
  const note = (s) => badEvents.push(s);
  let client;
  try {
    const targets = await (await fetch(`${CDP}/json/list`)).json();
    const pageTarget = targets.find((t) => t.type === 'page');
    client = await connect(pageTarget.webSocketDebuggerUrl);
    client.onEvent((m) => {
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        note(`[console.error] ${(m.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 250)}`);
      }
      if (m.method === 'Runtime.exceptionThrown') {
        note(`[exception] ${JSON.stringify(m.params.exceptionDetails).slice(0, 300)}`);
      }
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
        const text = m.params.entry.text;
        // Сетевой 404 разбираем точно по URL ниже (responseReceived);
        // здесь он — лишь дубль без адреса.
        if (/Failed to load resource.*status of 404/.test(text)) return;
        note(`[log.${m.params.entry.source}] ${text.slice(0, 250)}`);
      }
      if (m.method === 'Network.responseReceived') {
        const r = m.params.response;
        if (r.status >= 400) httpErrors.push(`http${r.status} ${r.url.slice(0, 160)}`);
      }
    });
    await client.send('Runtime.enable');
    await client.send('Log.enable');
    await client.send('Page.enable');
    await client.send('Network.enable');
    await client.send('Page.addScriptToEvaluateOnNewDocument', {
      source: `try{sessionStorage.setItem('apc.dashboard.token',${JSON.stringify(TOKEN)});`
        + `sessionStorage.setItem('apc.dashboard.namespace',${JSON.stringify(NS)});}catch(e){}`
        + `window.__violations=[];document.addEventListener('securitypolicyviolation',`
        + `e=>{window.__violations.push(e.violatedDirective+' :: '+e.blockedURI);});`,
    });
    const ev = async (expr, awaitPromise = false) =>
      (await client.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise })).result?.value;
    const shot = async (name) => {
      const s = await client.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(OUT_DIR, name), Buffer.from(s.data, 'base64'));
      console.log(`  shot: ${name}`);
    };
    const apiHeaders = { Authorization: `Bearer ${TOKEN}` };

    // --- Фикстура: загружаем звонок, чтобы сценарии не зависели от чужих данных.
    // Дедуп заданий по хэшу: подмешиваем уникальную долю секунды тишины,
    // иначе повторный прогон переиспользует старое (уже удалённое) задание.
    console.log('fixture: upload');
    const padWav = (raw, extraSamples) => {
      // Фикстура может содержать extra-чанки (LIST/INFO): ищем чанк 'data'
      // проходом, тишину дописываем в конец, размеры правим точечно.
      const buf = Buffer.from(raw);
      if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.readUInt16LE(20) !== 1) {
        throw new Error('фикстура не PCM WAV');
      }
      const ch = buf.readUInt16LE(22);
      const bits = buf.readUInt16LE(34);
      let off = 12;
      let dataOff = -1;
      let dataLen = 0;
      while (off + 8 <= buf.length) {
        const id = buf.toString('ascii', off, off + 4);
        const len = buf.readUInt32LE(off + 4);
        if (id === 'data') { dataOff = off; dataLen = len; break; }
        off += 8 + len + (len % 2);
      }
      if (dataOff < 0) throw new Error('в фикстуре нет чанка data');
      if (dataOff + 8 + dataLen !== buf.length) {
        throw new Error('чанк data не последний — паддинг дописал бы мусор');
      }
      const pad = Buffer.alloc(extraSamples * ch * (bits / 8), 0);
      const out = Buffer.concat([buf, pad]);
      out.writeUInt32LE(out.length - 8, 4);
      out.writeUInt32LE(dataLen + pad.length, dataOff + 4);
      return out;
    };
    const fixtureBytes = padWav(fs.readFileSync(FIXTURE_WAV), 8000 + (Date.now() % 5) * 1600);
    const fd = new FormData();
    fd.append('file', new Blob([fixtureBytes], { type: 'audio/wav' }), 'live_check_fixture.wav');
    fd.append('namespace', NS);
    fd.append('direction', 'inbound');
    fd.append('external_id', `live-check-${Date.now()}`);
    const up = await fetch(`${BASE}/v1/jobs/upload`, { method: 'POST', headers: apiHeaders, body: fd });
    check(up.status === 202 || up.status === 200, 'upload принят', `http${up.status}`);
    const upBody = await up.json();
    const jobId = upBody.job_id;
    const callId = upBody.call_id || null;
    console.log(`  job=${jobId} call=${callId}`);

    // --- Сценарий 1: вход по токену, журнал; pending разбор сразу после загрузки.
    console.log('scenario: dashboard + pending analysis');
    await client.send('Page.navigate', { url: `${BASE}/dashboard` });
    await sleep(6000);
    check(await ev(`!document.getElementById('authGate') || document.getElementById('authGate').style.display === 'none'`),
      'гейт входа скрыт при токене');
    const rows = await ev(`document.querySelectorAll('#callsTableBody tr').length`);
    check(rows > 0, 'журнал не пуст', `rows=${rows}`);
    // call_id появляется в статусе задания раньше, чем задание готово:
    // открываем звонок во время обработки — разбор ещё 404, ждём pending.
    let pendingCallId = callId;
    for (let i = 0; i < 20 && !pendingCallId; i++) {
      await sleep(3000);
      try {
        const jr = await fetch(`${BASE}/v1/jobs/${jobId}?namespace=${encodeURIComponent(NS)}`, { headers: apiHeaders });
        pendingCallId = (await jr.json()).call_id || null;
      } catch (e) { /* повтор */ }
    }
    if (pendingCallId) {
      await ev(`openArchivedCall(${JSON.stringify(pendingCallId)})`);
      await sleep(5000);
      const summary = await ev(`document.getElementById('summaryText').innerText`);
      const status = await ev(`document.getElementById('statusCallText').innerText`);
      check(!/Ошибка загрузки результатов/.test(status), 'нет ошибки загрузки при обработке', status.slice(0, 80));
      const isPending = /Разбор отсутствует/.test(summary);
      const isDone = summary.length > 20 && !isPending;
      check(isPending || isDone, 'разбор: pending или готов, но не ошибка', summary.slice(0, 70));
      await shot('live_pending.png');
    } else {
      check(false, 'call_id появился до готовности задания');
    }

    // --- Сценарий 1b: длинный файл (синтетический WAV 60 мин, без загрузки
    // на сервер): обязан ехать MEDIA-бэкендом без раздувания памяти.
    console.log('scenario: long file (synthetic 60-min WAV)');
    await client.send('Page.navigate', { url: `${BASE}/dashboard` });
    await sleep(5000);
    const rssBefore = rendererRssMb();
    const synth = await ev(`(async()=>{
      const rate=16000, secs=3600;
      const dataLen=rate*2*secs;
      const buf=new ArrayBuffer(44+dataLen);
      const dv=new DataView(buf);
      const ws=(o,s)=>{for(let i=0;i<s.length;i++)dv.setUint8(o+i,s.charCodeAt(i));};
      ws(0,'RIFF');dv.setUint32(4,36+dataLen,true);ws(8,'WAVE');ws(12,'fmt ');
      dv.setUint32(16,16,true);dv.setUint16(20,1,true);dv.setUint16(22,1,true);
      dv.setUint32(24,rate,true);dv.setUint32(28,rate*2,true);
      dv.setUint16(32,2,true);dv.setUint16(34,16,true);ws(36,'data');dv.setUint32(40,dataLen,true);
      const blob=new Blob([buf],{type:'audio/wav'});
      const t0=performance.now();
      await Player.loadBlob(blob);
      return JSON.stringify({backend:Player.backendName(),dur:Player.getDuration(),
        ms:Math.round(performance.now()-t0),bytes:blob.size});
    })()`, true);
    let longInfo = {};
    try { longInfo = JSON.parse(synth); } catch (e) { longInfo = {}; }
    console.log(`  long: ${synth}`);
    check(longInfo.backend === 'media', 'длинный файл ведёт MEDIA-бэкенд', longInfo.backend);
    check(Math.abs((longInfo.dur || 0) - 3600) < 5, 'длительность ~3600 с', String(longInfo.dur));
    await ev(`Player.setTime(100); Player.play();`);
    await sleep(2500);
    const lt = await ev(`Player.getCurrentTime()`);
    check(lt > 101, 'длинный файл проигрывается', `t=${lt}`);
    await ev(`Player.setTime(3500);`);
    await sleep(1200);
    const lt2 = await ev(`Player.getCurrentTime()`);
    check(Math.abs(lt2 - 3500) < 10, 'перемотка часа работает', `t=${lt2}`);
    await ev(`Player.pause()`);
    const rssAfter = rendererRssMb();
    const rssGrowth = rssAfter - rssBefore;
    console.log(`  rss growth MB: ${rssGrowth} (before=${rssBefore} after=${rssAfter})`);
    check(rssGrowth < 250, 'память не раздута декодированием', `+${rssGrowth} МБ`);
    await shot('live_long.png');

    // --- Ждём готовности задания, затем карточка + проигрывание + разбор.
    console.log('wait: job succeeded');
    let succeeded = false;
    for (let i = 0; i < 100; i++) {
      await sleep(6000);
      const jr = await fetch(`${BASE}/v1/jobs/${jobId}?namespace=${encodeURIComponent(NS)}`, { headers: apiHeaders });
      const jb = await jr.json();
      if (jb.status === 'succeeded') { succeeded = true; break; }
      if (jb.status === 'failed') break;
    }
    check(succeeded, 'задание дошло до succeeded');
    const doneCallId = callId || (await (await fetch(`${BASE}/v1/jobs/${jobId}?namespace=${encodeURIComponent(NS)}`, { headers: apiHeaders })).json()).call_id;
    if (!doneCallId) {
      check(false, 'call_id известен после готовности задания');
      throw new Error('no call_id');
    }

    console.log('scenario: playback + analysis');
    await client.send('Page.reload', { ignoreCache: true });
    await sleep(6000);
    await ev(`openArchivedCall(${JSON.stringify(doneCallId)})`);
    let dur = 0;
    for (let i = 0; i < 30 && dur <= 0; i++) { await sleep(1000); dur = await ev(`Player.getDuration()`); }
    check(dur > 0, 'длительность декодирована', `dur=${dur}`);
    check((await ev(`document.getElementById('statusCallText').innerText`)) === 'Аудио загружено в плеер',
      'честный статус загрузки');
    await ev(`Player.setTime(0.5); Player.play();`);
    await sleep(3000);
    const t1 = await ev(`Player.getCurrentTime()`);
    check(t1 > 1.5, 'время идёт при проигрывании', `t=${t1}`);
    const hl = await ev(`document.querySelectorAll('.word-active').length + '/' + document.querySelectorAll('.replica-active').length`);
    const timer = await ev(`document.getElementById('audioTimer').innerText`);
    console.log(`  highlight words/replica=${hl} timer=${timer}`);
    await ev(`Player.pause()`);
    const sumDone = await ev(`document.getElementById('summaryText').innerText`);
    check(sumDone.length > 20 && !/Разбор отсутствует/.test(sumDone), 'разбор отрендерен', sumDone.slice(0, 60));
    // Перемотка кликом по слову.
    const tBefore = await ev(`Player.getCurrentTime()`);
    await ev(`(()=>{const ws=[...document.querySelectorAll('.word-span')]; const w=ws[ws.length-1]; if(w) w.click();})()`);
    await sleep(800);
    const tAfter = await ev(`Player.getCurrentTime()`);
    check(tAfter > tBefore, 'клик по слову перематывает', `${tBefore} -> ${tAfter}`);
    await ev(`Player.pause()`);
    await shot('live_playback.png');

    // --- Сценарий 2: голосовой поиск с фейковым микрофоном (3 запроса для кластеров).
    console.log('scenario: voice + clusters');
    await client.send('Page.navigate', { url: `${BASE}/voice` });
    await sleep(6000);
    let voiceOk = 0;
    for (let n = 0; n < 3; n++) {
      await ev(`document.getElementById('btnVoiceSearchRecord').click()`);
      await sleep(4000);
      await ev(`document.getElementById('btnVoiceSearchRecord').click()`);
      let txt = '';
      for (let i = 0; i < 25; i++) {
        await sleep(1000);
        txt = await ev(`document.getElementById('voiceTextResult').innerText`);
        if (txt && !/Ожидание|Подготовка/.test(txt)) break;
      }
      if (txt && !/Временно недоступно|Нет связи|Ошибка/.test(txt)) voiceOk++;
      console.log(`  voice #${n + 1}: ${txt.slice(0, 60)}`);
    }
    check(voiceOk >= 1, 'голос распознан', `${voiceOk}/3`);
    await shot('live_voice.png');
    await ev(`document.getElementById('btnRefreshSummary').click()`);
    await sleep(4000);
    const sumBody = await ev(`document.getElementById('summaryBody').innerText`);
    check(/Звонков за период: [1-9]/.test(sumBody), 'сводка посчитала звонки', sumBody.slice(0, 60));
    await ev(`document.getElementById('btnRunClustering').click()`);
    let clusters = 0;
    for (let i = 0; i < 30; i++) {
      await sleep(2000);
      clusters = await ev(`document.querySelectorAll('.cluster-card').length`);
      if (clusters > 0) break;
      const errTxt = await ev(`document.getElementById('clusteringResultsContainer').innerText`);
      if (/не хватает|нужно хотя бы|Ошибка/.test(errTxt)) break;
    }
    check(clusters > 0, 'кластеры построены', `cards=${clusters}`);
    await shot('live_clusters.png');

    // --- Чистка фикстуры (best effort, отдельный namespace и так изолирован).
    try {
      await fetch(`${BASE}/v1/data/delete?namespace=${encodeURIComponent(NS)}&call_id=${doneCallId}`,
        { method: 'DELETE', headers: { ...apiHeaders, 'Content-Type': 'application/json' } });
    } catch (e) { /* best effort */ }

    const violations = await ev(`JSON.stringify(window.__violations || [])`).catch(() => '[]');
    check(JSON.parse(violations || '[]').length === 0, 'ноль securitypolicyviolation', violations);
    // console.error/exception за всю сессию.
    check(badEvents.length === 0, 'ноль ошибок консоли/исключений', badEvents.slice(0, 3).join(' | '));
    // HTTP-ошибки — все, кроме штатного 404 анализа (сигнал «разбор ещё не
    // готов», фронт показывает pending; путь сверен с openapi.yaml).
    const unexpectedHttp = httpErrors.filter((e) => !/http404 .*\/analysis\?/.test(e));
    check(unexpectedHttp.length === 0, 'ноль неожиданных HTTP-ошибок', unexpectedHttp.slice(0, 3).join(' | '));
  } finally {
    if (client) client.close();
    killBrowser();
  }

  if (failures.length > 0) {
    console.log(`\nLIVE-CHECK FAILED (${failures.length}):`);
    failures.forEach((f) => console.log(`  - ${f}`));
    process.exit(1);
  }
  console.log('\nLIVE-CHECK PASSED');
}

main().catch((e) => { console.error(`LIVE-CHECK ERROR: ${e.message}`); process.exit(1); });
