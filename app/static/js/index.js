  // =========================================================================
  // GLOBAL STATE & STORAGE
  // =========================================================================
  /* escapeHtml и setText — общий хелпер, см. /static/js/escape.js */

  const tokenInput = document.getElementById('apiToken');
  const namespaceInput = document.getElementById('namespaceInput');
  const TOKEN_KEY = 'apc.dashboard.token';
  const NS_KEY = 'apc.dashboard.namespace';
  const DEFAULT_NAMESPACE = 'demo_outdoor_msk';

  function getToken() {
    try {
      return (sessionStorage.getItem(TOKEN_KEY) || '').trim();
    } catch (e) {
      return '';
    }
  }
  function readNamespace() {
    try {
      return (sessionStorage.getItem(NS_KEY) || '').trim() || DEFAULT_NAMESPACE;
    } catch (e) {
      return DEFAULT_NAMESPACE;
    }
  }
  function syncAuthInputs() {
    if (tokenInput && !tokenInput.value) tokenInput.value = getToken();
    if (namespaceInput && !namespaceInput.value) namespaceInput.value = readNamespace();
  }
  function saveAuthInputs() {
    try {
      if (tokenInput) sessionStorage.setItem(TOKEN_KEY, tokenInput.value.trim());
      if (namespaceInput) sessionStorage.setItem(NS_KEY, namespaceInput.value.trim());
    } catch (e) {
      /* sessionStorage недоступен — токен живёт только в полях ввода */
    }
  }

  function ensureApiBanner() {
    let banner = document.getElementById('apiBanner');
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = 'apiBanner';
    banner.className = 'api-banner';
    banner.setAttribute('role', 'alert');
    const text = document.createElement('span');
    text.id = 'apiBannerText';
    const tokenBtn = document.createElement('button');
    tokenBtn.id = 'apiBannerTokenBtn';
    tokenBtn.type = 'button';
    tokenBtn.className = 'btn btn-sm btn-outline-danger';
    tokenBtn.textContent = 'Ввести токен';
    tokenBtn.addEventListener('click', () => showAuthGate());
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn btn-sm btn-link text-decoration-none';
    closeBtn.textContent = '×';
    closeBtn.setAttribute('aria-label', 'Закрыть сообщение');
    closeBtn.addEventListener('click', () => banner.classList.remove('show'));
    banner.appendChild(text);
    banner.appendChild(tokenBtn);
    banner.appendChild(closeBtn);
    document.body.prepend(banner);
    return banner;
  }
  function showAuthBanner(status) {
    const banner = ensureApiBanner();
    const text = document.getElementById('apiBannerText');
    const tokenBtn = document.getElementById('apiBannerTokenBtn');
    banner.classList.remove('api-banner-net');
    banner.classList.add('api-banner-error');
    if (text) {
      text.textContent = status === 403
        ? 'Доступ запрещён (403): у токена нет прав. Введите токен с нужным scope.'
        : 'Не авторизован (401): токен отсутствует или неверный. Введите токен.';
    }
    if (tokenBtn) tokenBtn.style.display = '';
    banner.classList.add('show');
  }
  function showNetBanner(message) {
    const banner = ensureApiBanner();
    const text = document.getElementById('apiBannerText');
    const tokenBtn = document.getElementById('apiBannerTokenBtn');
    banner.classList.remove('api-banner-error');
    banner.classList.add('api-banner-net');
    if (text) text.textContent = message || 'Нет связи с сервером. Проверьте, что сервис запущен, и повторите.';
    if (tokenBtn) tokenBtn.style.display = 'none';
    banner.classList.add('show');
  }
  function hideApiBanner() {
    const banner = document.getElementById('apiBanner');
    if (banner) banner.classList.remove('show');
  }
  function checkAuthResponse(res) {
    if (res.status === 401 || res.status === 403) {
      showAuthBanner(res.status);
      const err = new Error(res.status === 401 ? 'Не авторизован (401)' : 'Доступ запрещён (403)');
      err.auth = true;
      throw err;
    }
    return res;
  }
  if (!window.__apcFetchPatched) {
    window.__apcFetchPatched = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (url, options) {
      return nativeFetch(url, options).then(
        (res) => {
          if (typeof url === 'string' && url.indexOf('/v1/') === 0) {
            if (res.status === 401 || res.status === 403) showAuthBanner(res.status);
            else if (res.ok) hideApiBanner();
          }
          return res;
        },
        (err) => {
          if (typeof url === 'string' && url.indexOf('/v1/') === 0) showNetBanner();
          throw err;
        }
      );
    };
  }

  function showAuthGate() {
    if (document.getElementById('authGate')) {
      document.getElementById('authGate').style.display = 'flex';
      return;
    }
    const overlay = document.createElement('div');
    overlay.id = 'authGate';
    overlay.className = 'auth-gate-overlay';
    const card = document.createElement('div');
    card.className = 'auth-gate-card';
    const title = document.createElement('h5');
    title.textContent = 'Вход в дашборд';
    const hint = document.createElement('p');
    hint.className = 'text-muted small';
    hint.textContent = 'Введите неймспейс и Bearer-токен (scope read/write). Токен хранится только в sessionStorage этой вкладки и никуда кроме API не отправляется.';
    const nsLabel = document.createElement('label');
    nsLabel.className = 'form-label small';
    nsLabel.textContent = 'Неймспейс';
    const nsInput = document.createElement('input');
    nsInput.id = 'authGateNs';
    nsInput.className = 'form-control mb-2';
    nsInput.value = readNamespace();
    const tokenLabel = document.createElement('label');
    tokenLabel.className = 'form-label small';
    tokenLabel.textContent = 'Bearer-токен';
    const tokenField = document.createElement('input');
    tokenField.id = 'authGateToken';
    tokenField.type = 'password';
    tokenField.className = 'form-control mb-3';
    tokenField.setAttribute('autocomplete', 'off');
    tokenField.value = getToken();
    const submit = document.createElement('button');
    submit.type = 'button';
    submit.className = 'btn btn-primary w-100';
    submit.textContent = 'Войти';
    submit.addEventListener('click', () => {
      try {
        sessionStorage.setItem(NS_KEY, nsInput.value.trim());
        sessionStorage.setItem(TOKEN_KEY, tokenField.value.trim());
      } catch (e) {
        /* без sessionStorage — только поля шапки */
      }
      syncAuthInputs();
      if (tokenInput) tokenInput.value = tokenField.value.trim();
      if (namespaceInput) namespaceInput.value = nsInput.value.trim();
      overlay.style.display = 'none';
      hideApiBanner();
      if (getToken()) {
        if (typeof refreshCallsList === 'function') refreshCallsList();
        if (typeof fetchVoiceHistory === 'function') fetchVoiceHistory();
      }
    });
    card.appendChild(title);
    card.appendChild(hint);
    card.appendChild(nsLabel);
    card.appendChild(nsInput);
    card.appendChild(tokenLabel);
    card.appendChild(tokenField);
    card.appendChild(submit);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
  }

  function initAuth() {
    syncAuthInputs();
    if (tokenInput) {
      tokenInput.addEventListener('change', () => {
        saveAuthInputs();
        if (!getToken()) showAuthGate();
      });
    }
    if (!getToken()) showAuthGate();
    // Кнопки без inline-onclick (CSP): привязка делегированием.
    document.addEventListener('click', (e) => {
      const callBtn = e.target && e.target.closest ? e.target.closest('[data-call-id]') : null;
      if (callBtn && typeof openArchivedCall === 'function') {
        openArchivedCall(callBtn.getAttribute('data-call-id'));
        return;
      }
      if (e.target && e.target.closest && e.target.closest('#btnFetchHistory')) {
        if (typeof fetchVoiceHistory === 'function') fetchVoiceHistory();
        return;
      }
      if (e.target && e.target.closest && e.target.closest('#btnClearHistory')) {
        if (typeof clearQueryHistory === 'function') clearQueryHistory();
        return;
      }
      if (e.target && e.target.closest && e.target.closest('#btnRunClustering')) {
        if (typeof runTopicClustering === 'function') runTopicClustering();
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAuth);
  } else {
    initAuth();
  }
  let currentCallId = null;
  let wavesurfer = null;
  let mediaRecorder = null;
  let callAudioChunks = [];
  let isRecordingCall = false;
  let callPollInterval = null;

  function getHeaders() {
    return {
      "Authorization": `Bearer ${getToken()}`,
      "Accept": "application/json"
    };
  }
  function getNamespace() {
    return readNamespace();
  }

  // =========================================================================
  // WAVESURFER INITIALIZATION
  // =========================================================================
  function initWavesurfer() {
    if (wavesurfer) wavesurfer.destroy();
    wavesurfer = WaveSurfer.create({
      container: '#waveform',
      waveColor: '#94a3b8',
      progressColor: '#0284c7',
      cursorColor: '#0369a1',
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 85,
      responsive: true
    });

    wavesurfer.on('timeupdate', (currentTime) => {
      // Synchronized word highlighting
      const allWords = document.querySelectorAll('.word-span');
      allWords.forEach(span => {
        const s = parseFloat(span.dataset.start);
        const e = parseFloat(span.dataset.end);
        if (currentTime >= s && currentTime <= e) {
          span.classList.add('word-active');
        } else {
          span.classList.remove('word-active');
        }
      });
      // Speaker timeline playhead update
      const totalDur = wavesurfer.getDuration();
      const playhead = document.getElementById('timelinePlayhead');
      if (playhead && totalDur > 0) {
        playhead.style.display = 'block';
        playhead.style.left = `${(currentTime / totalDur) * 100}%`;
      }
      // Timer update
      updateAudioTimer(currentTime, wavesurfer.getDuration());
    });

    wavesurfer.on('ready', () => {
      document.getElementById('btnPlayPause').disabled = false;
      document.getElementById('btnRewind5').disabled = false;
      document.getElementById('btnForward5').disabled = false;
      document.getElementById('btnExportMenu').disabled = false;
      document.getElementById('btnQuickTxt').disabled = false;
      document.getElementById('btnQuickSrt').disabled = false;
      document.getElementById('btnQuickJson').disabled = false;
      updateAudioTimer(0, wavesurfer.getDuration());
    });
  }

  function formatTime(sec) {
    if (isNaN(sec)) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
  }

  function updateAudioTimer(curr, total) {
    document.getElementById('audioTimer').innerText = `${formatTime(curr)} / ${formatTime(total)}`;
  }

  document.getElementById('btnPlayPause').addEventListener('click', () => {
    if (wavesurfer) wavesurfer.playPause();
  });
  document.getElementById('btnRewind5').addEventListener('click', () => {
    if (wavesurfer) wavesurfer.setTime(Math.max(0, wavesurfer.getCurrentTime() - 5));
  });
  document.getElementById('btnForward5').addEventListener('click', () => {
    if (wavesurfer) wavesurfer.setTime(Math.min(wavesurfer.getDuration(), wavesurfer.getCurrentTime() + 5));
  });

  initWavesurfer();

  // =========================================================================
  // TAB 1: CALL RECORDING & UPLOAD
  // =========================================================================
  const btnUpload = document.getElementById('btnUpload');
  const btnRecordCall = document.getElementById('btnRecordCall');
  const btnLoadDemoCall = document.getElementById('btnLoadDemoCall');
  const fileInput = document.getElementById('audioFile');
  const statusCallText = document.getElementById('statusCallText');
  const chatBox = document.getElementById('chatBox');

  btnUpload.addEventListener('click', () => {
    if (!fileInput.files.length) return alert("Пожалуйста, выберите аудиофайл!");
    uploadCallAudio(fileInput.files[0], fileInput.files[0].name);
  });

  btnRecordCall.addEventListener('click', async () => {
    if (isRecordingCall) {
      mediaRecorder.stop();
      btnRecordCall.innerHTML = '<i class="bi bi-mic me-1"></i> Записать с микрофона';
      btnRecordCall.classList.replace('btn-danger', 'btn-outline-danger');
      isRecordingCall = false;
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      callAudioChunks = [];

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) callAudioChunks.push(event.data);
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(callAudioChunks, { type: 'audio/wav' });
        uploadCallAudio(audioBlob, "call_recorded_mic.wav");
        stream.getTracks().forEach(track => track.stop());
      };

      mediaRecorder.start();
      isRecordingCall = true;
      btnRecordCall.innerHTML = '<i class="bi bi-stop-circle me-1"></i> Остановить запись...';
      btnRecordCall.classList.replace('btn-outline-danger', 'btn-danger');
      statusCallText.innerText = "Идёт запись разговора...";
    } catch (err) {
      alert("Ошибка доступа к микрофону: " + err.message);
    }
  });

  btnLoadDemoCall.addEventListener('click', async () => {
    statusCallText.innerText = "Загрузка образца телефонного звонка...";
    try {
      const res = await fetch('/v1/voice/sample-audio');
      if (!res.ok) throw new Error("Не удалось получить демонстрационный звонок");
      const blob = await res.blob();
      uploadCallAudio(blob, "sample_dialogue_telephony.wav");
    } catch (e) {
      alert("Ошибка: " + e.message);
    }
  });

  function uploadCallAudio(blob, filename) {
    const blobUrl = URL.createObjectURL(blob);
    wavesurfer.load(blobUrl);

    const formData = new FormData();
    formData.append("file", blob, filename);
    formData.append("namespace", getNamespace());
    formData.append("direction", "inbound");

    statusCallText.innerText = "Отправка на сервер (создание Job)...";

    fetch("/v1/jobs/upload", {
      method: "POST",
      headers: { "Authorization": `Bearer ${getToken()}` },
      body: formData
    })
    .then(res => res.json())
    .then(data => {
      if (data.error) throw new Error(data.message || data.error);
      currentCallId = null;
      statusCallText.innerText = `В очереди (Job ID: ${(data.job_id || '').substring(0,8)}...)...`;
      pollCallJob(data.job_id);
    })
    .catch(err => {
      statusCallText.innerText = "Ошибка: " + err.message;
    });
  }

  function pollCallJob(jobId) {
    if (callPollInterval) clearInterval(callPollInterval);
    let pollFailures = 0;

    callPollInterval = setInterval(() => {
      fetch(`/v1/jobs/${jobId}?namespace=${getNamespace()}`, { headers: getHeaders() })
        .then(checkAuthResponse).then(res => res.json())
        .then(data => {
          if (data.status === "succeeded") {
            clearInterval(callPollInterval);
            statusCallText.innerText = "Анализ завершён успешно!";
            currentCallId = data.call_id;
            loadCallResults(data.call_id);
            refreshCallsList();
          } else if (data.status === "failed") {
            clearInterval(callPollInterval);
            statusCallText.innerText = "Ошибка обработки: " + (data.error?.message || "Сбой");
          } else {
            statusCallText.innerText = `Обработка: ${data.progress}% (${data.step || 'выполняется'})...`;
          }
        })
        .catch((err) => {
          if (err && err.auth) { clearInterval(callPollInterval); return; }
          pollFailures += 1;
          if (pollFailures >= 3) {
            clearInterval(callPollInterval);
            statusCallText.innerText = "Нет связи с сервером: опрос остановлен. Проверьте сервис и загрузите снова.";
          } else {
            statusCallText.innerText = "Нет связи с сервером, повтор опроса...";
          }
        });
    }, 1500);
  }

  function loadCallResults(callId) {
    currentCallId = callId;
    const ns = getNamespace();
    Promise.all([
      fetch(`/v1/calls/${callId}/transcript?namespace=${ns}`, { headers: getHeaders() }).then(checkAuthResponse).then(r => r.json()),
      fetch(`/v1/calls/${callId}/analysis?namespace=${ns}`, { headers: getHeaders() }).then(checkAuthResponse).then(r => r.json())
    ]).then(([transcript, analysis]) => {
      document.getElementById('btnQuickTxt').disabled = false;
      document.getElementById('btnQuickSrt').disabled = false;
      document.getElementById('btnQuickJson').disabled = false;
      document.getElementById('btnExportMenu').disabled = false;
      renderChat(transcript.segments);
      renderAnalysis(analysis);
    }).catch(err => {
      if (!(err && err.auth)) statusCallText.innerText = "Ошибка загрузки результатов: " + (err.message || err);
    });
  }

  function renderSpeakerTimeline(segments) {
    const timelineWrapper = document.getElementById('speakerTimelineWrapper');
    const timeline = document.getElementById('speakerTimeline');
    if (!timelineWrapper || !timeline) return;

    if (!segments || segments.length === 0) {
      timelineWrapper.style.display = 'none';
      return;
    }

    timelineWrapper.style.display = 'block';
    timeline.innerHTML = '';

    const wsDur = wavesurfer ? wavesurfer.getDuration() : 0;
    const maxEnd = Math.max(...segments.map(s => s.end || 0));
    const totalDur = wsDur > 0 ? wsDur : (maxEnd > 0 ? maxEnd : 1);

    segments.forEach(seg => {
      const isFirstSpeaker =
        seg.speaker_role === 'manager' ||
        seg.speaker_role === 'participant_1' ||
        seg.speaker_label === 'SPEAKER_0';
      const roleLabel = isFirstSpeaker ? 'Менеджер' : 'Клиент';
      const color = isFirstSpeaker ? '#0284c7' : '#f97316';

      const leftPct = (seg.start / totalDur) * 100;
      const widthPct = Math.max(0.4, ((seg.end - seg.start) / totalDur) * 100);

      const block = document.createElement('div');
      block.className = 'speaker-block position-absolute';
      block.style.left = `${leftPct}%`;
      block.style.width = `${widthPct}%`;
      block.style.top = '4px';
      block.style.bottom = '4px';
      block.style.backgroundColor = color;
      block.style.borderRadius = '3px';
      block.style.cursor = 'pointer';
      block.style.opacity = '0.85';
      block.style.transition = 'opacity 0.15s, transform 0.15s';
      block.title = `${roleLabel} (${seg.start.toFixed(1)}s - ${seg.end.toFixed(1)}s): ${seg.text || ''}`;

      block.addEventListener('mouseenter', () => {
        block.style.opacity = '1';
        block.style.transform = 'scaleY(1.15)';
      });
      block.addEventListener('mouseleave', () => {
        block.style.opacity = '0.85';
        block.style.transform = 'scaleY(1)';
      });
      block.addEventListener('click', (e) => {
        e.stopPropagation();
        if (wavesurfer) {
          wavesurfer.setTime(seg.start);
          wavesurfer.play();
        }
      });

      timeline.appendChild(block);
    });

    timelineWrapper.onclick = (e) => {
      const rect = timelineWrapper.getBoundingClientRect();
      const clickRatio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      if (wavesurfer && totalDur > 0) {
        wavesurfer.setTime(clickRatio * totalDur);
        wavesurfer.play();
      }
    };
  }

  function renderChat(segments) {
    chatBox.innerHTML = '';
    renderSpeakerTimeline(segments);
    if (!segments || segments.length === 0) {
      chatBox.innerHTML = '<div class="text-muted text-center mt-5">Пустая расшифровка (речь не обнаружена)</div>';
      return;
    }

    let managerTime = 0;
    let clientTime = 0;

    segments.forEach(seg => {
      const isFirstSpeaker =
        seg.speaker_role === 'manager' ||
        seg.speaker_role === 'participant_1' ||
        seg.speaker_label === 'SPEAKER_0';
      const dur = seg.end - seg.start;
      if (isFirstSpeaker) managerTime += dur; else clientTime += dur;

      const bubble = document.createElement('div');
      bubble.className = `msg-bubble ${isFirstSpeaker ? 'msg-manager' : 'msg-client'}`;

      let roleLabel = 'Менеджер';
      if (seg.speaker_role === 'client') {
        roleLabel = 'Клиент';
      } else if (seg.speaker_role === 'participant_1') {
        roleLabel = 'Участник 1';
      } else if (seg.speaker_role === 'participant_2') {
        roleLabel = 'Участник 2';
      } else if (!isFirstSpeaker) {
        roleLabel = 'Клиент';
      }

      const badge = document.createElement('div');
      badge.className = `speaker-badge ${isFirstSpeaker ? 'text-primary' : 'text-success'}`;
      badge.innerHTML = `
        <span><i class="bi bi-person-${isFirstSpeaker ? 'badge' : 'fill'} me-1"></i>${roleLabel} (${escapeHtml(seg.speaker_label || (isFirstSpeaker ? 'SPEAKER_0' : 'SPEAKER_1'))})</span>
        <span class="text-muted fw-normal">${seg.start.toFixed(1)}s - ${seg.end.toFixed(1)}s</span>
      `;
      bubble.appendChild(badge);

      const textContainer = document.createElement('div');
      if (seg.words && seg.words.length > 0) {
        seg.words.forEach(w => {
          const span = document.createElement('span');
          span.className = 'word-span';
          span.innerText = w.word + ' ';
          span.dataset.start = w.start;
          span.dataset.end = w.end;
          span.addEventListener('click', () => {
            if (wavesurfer) {
              wavesurfer.setTime(w.start);
              wavesurfer.play();
            }
          });
          textContainer.appendChild(span);
        });
      } else {
        textContainer.innerText = seg.text;
      }

      bubble.appendChild(textContainer);
      chatBox.appendChild(bubble);
    });

    const total = managerTime + clientTime;
    if (total > 0) {
      const mPct = Math.round((managerTime / total) * 100);
      const cPct = 100 - mPct;
      document.getElementById('activeSpeakerRatioBadge').innerText = `Менеджер: ${mPct}% | Клиент: ${cPct}%`;
    }
  }

  function renderAnalysis(analysis) {
    document.getElementById('summaryText').innerText = analysis.summary || "Саммари сформировано на основе ключевых тем.";
    document.getElementById('inquiryTypeBadge').innerText = "Тип: " + (analysis.inquiry_type || "общий");

    // Entities
    const entBox = document.getElementById('entitiesBox');
    entBox.innerHTML = '';
    const e = analysis.entities || {};
    let count = 0;
    if (e.cities && e.cities.length) {
      entBox.innerHTML += `<span class="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25">📍 ${escapeHtml(e.cities.join(', '))}</span>`;
      count++;
    }
    if (e.formats && e.formats.length) {
      entBox.innerHTML += `<span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25">📐 ${escapeHtml(e.formats.join(', '))}</span>`;
      count++;
    }
    if (e.budget_rub) {
      entBox.innerHTML += `<span class="badge bg-warning bg-opacity-10 text-warning-emphasis border border-warning border-opacity-50">💰 ${escapeHtml(e.budget_rub.toLocaleString())} ₽</span>`;
      count++;
    }
    if (e.quantity) {
      entBox.innerHTML += `<span class="badge bg-info bg-opacity-10 text-info-emphasis border border-info border-opacity-50">🔢 ${escapeHtml(e.quantity)} шт.</span>`;
      count++;
    }
    if (e.period) {
      entBox.innerHTML += `<span class="badge bg-secondary bg-opacity-10 text-secondary border">📅 ${escapeHtml(e.period)}</span>`;
      count++;
    }
    if (!count) entBox.innerHTML = '<span class="text-muted small">Сущности не определены</span>';

    // Sentiment and Emotion Profile
    const sent = analysis.sentiment || {};
    const overallSent = (typeof sent === 'string' ? sent : sent.overall) || 'neutral';
    const overallSentBadge = document.getElementById('overallSentimentBadge');
    if (overallSentBadge) {
      let sentLabel = 'Нейтральная';
      let sentBg = 'bg-secondary';
      if (overallSent === 'positive') { sentLabel = 'Позитивная'; sentBg = 'bg-success'; }
      else if (overallSent === 'negative') { sentLabel = 'Негативная'; sentBg = 'bg-danger'; }
      overallSentBadge.innerText = `Тональность: ${sentLabel}`;
      overallSentBadge.className = `badge ${sentBg}`;
    }

    const emotionMapClient = {
      'enthusiasm_joy': '😊 Энтузиазм / Радость',
      'neutral_business': '😐 Спокойный / Деловой',
      'doubt_skepticism': '🤔 Сомнение / Скепсис',
      'irritation_anger': '😠 Раздражение / Гнев',
      'urgency_anxiety': '⚡ Спешка / Тревога'
    };
    const emotionMapManager = {
      'enthusiastic_helpful': '🌟 Вовлеченный / Помогает',
      'calm_professional': '💼 Спокойный / Профессиональный',
      'indifferent_bored': '🥱 Безразличный / Уставший',
      'irritated_defensive': '🛡️ Раздраженный / В защите'
    };

    const clientEmo = sent.client_emotion || 'neutral_business';
    const managerEmo = sent.manager_emotion || 'calm_professional';
    const elClientEmo = document.getElementById('clientEmotionText');
    const elManagerEmo = document.getElementById('managerEmotionText');
    if (elClientEmo) elClientEmo.innerText = emotionMapClient[clientEmo] || clientEmo;
    if (elManagerEmo) elManagerEmo.innerText = emotionMapManager[managerEmo] || managerEmo;

    const emoBreakdown = sent.emotions_breakdown || { positive_pct: 0, neutral_pct: 100, negative_pct: 0 };
    const elEmoPercentages = document.getElementById('emotionPercentages');
    if (elEmoPercentages) {
      elEmoPercentages.innerText = `Позитив: ${emoBreakdown.positive_pct}% | Нейтрал: ${emoBreakdown.neutral_pct}% | Негатив: ${emoBreakdown.negative_pct}%`;
    }
    const barPos = document.getElementById('emoPosBar');
    const barNeu = document.getElementById('emoNeuBar');
    const barNeg = document.getElementById('emoNegBar');
    if (barPos) barPos.style.width = `${emoBreakdown.positive_pct}%`;
    if (barNeu) barNeu.style.width = `${emoBreakdown.neutral_pct}%`;
    if (barNeg) barNeg.style.width = `${emoBreakdown.negative_pct}%`;

    // Checklist & Compliance
    const cl = analysis.manager_checklist || {};
    const score = cl.score !== undefined ? cl.score : 0;
    const scoreBadge = document.getElementById('checklistScore');
    if (scoreBadge) {
      scoreBadge.innerText = `Оценка: ${score} / 5`;
      scoreBadge.className = `badge fs-6 ${score >= 4 ? 'bg-success' : score >= 2 ? 'bg-warning text-dark' : 'bg-danger'}`;
    }

    // Integral Compliance Score
    const complianceScore = cl.compliance_score !== undefined && cl.compliance_score !== null
      ? Math.round(cl.compliance_score)
      : Math.round((score / 5) * 100);
    const complianceText = document.getElementById('complianceScoreText');
    const complianceBar = document.getElementById('complianceProgressBar');
    if (complianceText) complianceText.innerText = `${complianceScore}%`;
    if (complianceBar) {
      complianceBar.style.width = `${complianceScore}%`;
      complianceBar.className = `progress-bar ${complianceScore >= 80 ? 'bg-success' : complianceScore >= 50 ? 'bg-warning text-dark' : 'bg-danger'}`;
    }

    const ul = document.getElementById('checklistUl');
    ul.innerHTML = '';
    const items = [
      { name: "Приветствие и представление", val: cl.greeting },
      { name: "Выявление потребности клиента", val: cl.need_identification },
      { name: "Предложение форматов/вариантов", val: cl.options_offered },
      { name: "Обозначение следующего шага", val: cl.next_step_named },
      { name: "Взятие контактных данных", val: cl.contacts_taken }
    ];

    items.forEach((item, idx) => {
      ul.innerHTML += `
        <li class="list-group-item d-flex justify-content-between align-items-center px-0">
          <span>${idx+1}. ${item.name}</span>
          <span class="badge ${item.val ? 'bg-success' : 'bg-secondary'} rounded-pill">
            ${item.val ? '✓ Да' : '✗ Нет'}
          </span>
        </li>
      `;
    });

    // Violations Box
    const violationsBox = document.getElementById('violationsBox');
    if (violationsBox) {
      if (cl.violations && cl.violations.length > 0) {
        violationsBox.style.display = 'block';
        violationsBox.innerHTML = `
          <div class="alert alert-danger py-2 px-3 mb-2">
            <div class="fw-bold small mb-1"><i class="bi bi-exclamation-octagon-fill me-1"></i> Зафиксированные нарушения (${cl.violations.length}):</div>
            <ul class="mb-0 ps-3 small">
              ${cl.violations.map(v => `<li>${escapeHtml(v)}</li>`).join('')}
            </ul>
          </div>
        `;
      } else {
        violationsBox.style.display = 'block';
        violationsBox.innerHTML = `
          <div class="alert alert-success py-2 px-3 mb-2 small">
            <i class="bi bi-shield-check me-1"></i> Грубых нарушений скрипта и стоп-слов не зафиксировано
          </div>
        `;
      }
    }

    // Recommendations Box
    const recommendationsBox = document.getElementById('recommendationsBox');
    if (recommendationsBox) {
      if (cl.recommendations && cl.recommendations.length > 0) {
        recommendationsBox.style.display = 'block';
        recommendationsBox.innerHTML = `
          <div class="alert alert-info py-2 px-3 mb-0">
            <div class="fw-bold small mb-1"><i class="bi bi-lightbulb-fill me-1"></i> Рекомендации супервайзера:</div>
            <ul class="mb-0 ps-3 small">
              ${cl.recommendations.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
            </ul>
          </div>
        `;
      } else {
        recommendationsBox.style.display = 'none';
      }
    }

    // Speech metrics
    const sm = analysis.deterministic_speech_metrics || {};
    document.getElementById('metricManagerRatio').innerText = sm.manager_speech_ratio ? `${Math.round(sm.manager_speech_ratio * 100)}%` : '-';
    document.getElementById('metricClientRatio').innerText = sm.client_speech_ratio ? `${Math.round(sm.client_speech_ratio * 100)}%` : '-';
    document.getElementById('metricInterruptions').innerText = sm.interruptions_count !== undefined ? sm.interruptions_count : '-';
    document.getElementById('metricWpm').innerText = sm.words_per_minute || '-';
    document.getElementById('metricAnsweringMachine').innerText = sm.is_answering_machine ? '⚠️ Да' : 'Нет';
    document.getElementById('metricAnsweringMachine').className = `fw-bold ${sm.is_answering_machine ? 'text-danger' : 'text-success'}`;
  }

  // =========================================================================
  // CALLS ARCHIVE LIST (GET /v1/calls)
  // =========================================================================
  const btnRefreshCalls = document.getElementById('btnRefreshCalls');
  const callsTableBody = document.getElementById('callsTableBody');

  btnRefreshCalls.addEventListener('click', refreshCallsList);

  function refreshCallsList() {
    callsTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">Загрузка звонков...</td></tr>';
    fetch(`/v1/calls?namespace=${getNamespace()}&limit=15`, { headers: getHeaders() })
      .then(checkAuthResponse).then(res => res.json())
      .then(data => {
        if (!data.items || data.items.length === 0) {
          callsTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">Нет сохраненных звонков в этом namespace</td></tr>';
          return;
        }
        callsTableBody.innerHTML = '';
        data.items.forEach(c => {
          const tr = document.createElement('tr');
          const durStr = c.duration_sec ? `${Math.round(c.duration_sec)} с` : '-';
          const dtStr = c.created_at ? new Date(c.created_at).toLocaleString('ru-RU') : '-';
          const safeId = escapeHtml(c.call_id);
          const safeExternalId = c.external_id ? escapeHtml(c.external_id) : '<span class="text-muted">-</span>';
          const safeManagerId = c.manager_id ? escapeHtml(c.manager_id) : 'Автоматически';
          tr.innerHTML = `
            <td><code>${safeId.substring(0, 8)}...</code></td>
            <td>${safeExternalId}</td>
            <td><span class="badge bg-light text-dark border">${safeManagerId}</span></td>
            <td>${durStr}</td>
            <td>${c.is_answering_machine ? '<span class="badge bg-danger">Автоответчик</span>' : '<span class="badge bg-success">Живой разговор</span>'}</td>
            <td class="small text-muted">${dtStr}</td>
            <td>
              <button class="btn btn-outline-primary btn-sm py-0 px-2" data-call-id="${safeId}">
                <i class="bi bi-play-circle me-1"></i> Открыть
              </button>
            </td>
          `;
          callsTableBody.appendChild(tr);
        });
      })
      .catch(err => {
        const msg = (err && err.auth)
          ? "Нет доступа: введите токен с правами read."
          : `Ошибка загрузки: ${escapeHtml(err.message || err)}`;
        callsTableBody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">${msg}</td></tr>`;
      });
  }

  function openArchivedCall(callId) {
    statusCallText.innerText = `Загрузка аудио и анализа звонка ${callId.substring(0,8)}...`;
    const ns = getNamespace();
    // Load audio from S3 via new endpoint
    fetch(`/v1/calls/${callId}/audio?namespace=${ns}`, { headers: getHeaders() })
      .then(res => {
        if (!res.ok) throw new Error("Не удалось получить аудиозапись из S3");
        return res.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        wavesurfer.load(url);
        statusCallText.innerText = "Аудио загружено в плеер";
        loadCallResults(callId);
      })
      .catch(e => {
        console.warn("Audio stream fetch error, loading results only:", e);
        loadCallResults(callId);
        statusCallText.innerText = "Результаты загружены";
      });
  }

  // Trigger Download helper with Authorization header
  async function triggerDownload(url, filename) {
    try {
      const res = await fetch(url, { headers: getHeaders() });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ message: res.statusText }));
        alert("Ошибка скачивания: " + (err.message || res.statusText));
        return;
      }
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (e) {
      console.error("Ошибка при скачивании файла:", e);
      alert("Ошибка при скачивании: " + e.message);
    }
  }

  // Quick 1-click Transcript Download Buttons
  const btnQuickTxt = document.getElementById('btnQuickTxt');
  const btnQuickSrt = document.getElementById('btnQuickSrt');
  const btnQuickJson = document.getElementById('btnQuickJson');

  if (btnQuickTxt) {
    btnQuickTxt.addEventListener('click', () => {
      if (!currentCallId) return;
      triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=text`, `transcript_${currentCallId}.txt`);
    });
  }
  if (btnQuickSrt) {
    btnQuickSrt.addEventListener('click', () => {
      if (!currentCallId) return;
      triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=srt`, `transcript_${currentCallId}.srt`);
    });
  }
  if (btnQuickJson) {
    btnQuickJson.addEventListener('click', () => {
      if (!currentCallId) return;
      triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=json`, `transcript_${currentCallId}.json`);
    });
  }

  // Export handlers (dropdown)
  document.getElementById('expJson').addEventListener('click', (e) => {
    e.preventDefault();
    if (!currentCallId) return;
    triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=json`, `transcript_${currentCallId}.json`);
  });
  document.getElementById('expText').addEventListener('click', (e) => {
    e.preventDefault();
    if (!currentCallId) return;
    triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=text`, `transcript_${currentCallId}.txt`);
  });
  document.getElementById('expSrt').addEventListener('click', (e) => {
    e.preventDefault();
    if (!currentCallId) return;
    triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=srt`, `transcript_${currentCallId}.srt`);
  });
  document.getElementById('expVtt').addEventListener('click', (e) => {
    e.preventDefault();
    if (!currentCallId) return;
    triggerDownload(`/v1/calls/${currentCallId}/transcript?namespace=${getNamespace()}&format=vtt`, `transcript_${currentCallId}.vtt`);
  });
  const expAnalysis = document.getElementById('expAnalysis');
  if (expAnalysis) {
    expAnalysis.addEventListener('click', (e) => {
      e.preventDefault();
      if (!currentCallId) return;
      triggerDownload(`/v1/calls/${currentCallId}/analysis?namespace=${getNamespace()}`, `analysis_${currentCallId}.json`);
    });
  }

  // Initial call load
  setTimeout(refreshCallsList, 800);


  // =========================================================================
  // TAB 2: VOICE SEARCH SIMULATION & QUERY HISTORY
  // =========================================================================
  const btnVoiceSearchRecord = document.getElementById('btnVoiceSearchRecord');
  const voiceMicIcon = document.getElementById('voiceMicIcon');
  const voiceRecordingStatus = document.getElementById('voiceRecordingStatus');
  const voiceRecordingTimer = document.getElementById('voiceRecordingTimer');
  const voiceFileInput = document.getElementById('voiceFileInput');

  const voiceLatencyDisplay = document.getElementById('voiceLatencyDisplay');
  const voiceSlaBadge = document.getElementById('voiceSlaBadge');
  const voiceSlaPercentDisplay = document.getElementById('voiceSlaPercentDisplay');
  const voiceLatencyProgress = document.getElementById('voiceLatencyProgress');
  const voiceTextResult = document.getElementById('voiceTextResult');
  const voiceDurationDisplay = document.getElementById('voiceDurationDisplay');
  const voiceConfidenceDisplay = document.getElementById('voiceConfidenceDisplay');
  const voiceLangDisplay = document.getElementById('voiceLangDisplay');
  const voiceRtfDisplay = document.getElementById('voiceRtfDisplay');

  let isRecordingVoice = false;
  let voiceMediaRecorder = null;
  let voiceAudioChunks = [];
  let voiceRecordStartTime = 0;
  let voiceTimerInterval = null;

  btnVoiceSearchRecord.addEventListener('click', async () => {
    if (isRecordingVoice) {
      stopVoiceRecording();
      return;
    }
    startVoiceRecording();
  });

  async function startVoiceRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceMediaRecorder = new MediaRecorder(stream);
      voiceAudioChunks = [];
      voiceRecordStartTime = Date.now();

      voiceMediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) voiceAudioChunks.push(e.data);
      };

      voiceMediaRecorder.onstop = () => {
        const audioBlob = new Blob(voiceAudioChunks, { type: 'audio/wav' });
        submitVoiceSearch(audioBlob, "mic_voice_search.wav");
        stream.getTracks().forEach(t => t.stop());
      };

      voiceMediaRecorder.start();
      isRecordingVoice = true;
      btnVoiceSearchRecord.classList.add('btn-recording-pulse');
      voiceMicIcon.className = 'bi bi-stop-fill';
      voiceRecordingStatus.innerText = "Идёт запись... Говорите фразу!";
      
      voiceTimerInterval = setInterval(() => {
        const elapsed = (Date.now() - voiceRecordStartTime) / 1000;
        const remain = Math.max(0, 6.0 - elapsed).toFixed(0);
        voiceRecordingTimer.innerText = `Запись: ${elapsed.toFixed(1)} с (Кликните для отправки или авто-стоп через ${remain} с)`;
      }, 100);

      if (voiceAutoStopTimeout) clearTimeout(voiceAutoStopTimeout);
      voiceAutoStopTimeout = setTimeout(() => {
        if (isRecordingVoice) stopVoiceRecording();
      }, 6000);

    } catch (err) {
      alert("Ошибка доступа к микрофону: " + err.message);
    }
  }

  let voiceAutoStopTimeout = null;

  function stopVoiceRecording() {
    if (voiceAutoStopTimeout) {
      clearTimeout(voiceAutoStopTimeout);
      voiceAutoStopTimeout = null;
    }
    if (voiceMediaRecorder && isRecordingVoice) {
      voiceMediaRecorder.stop();
      isRecordingVoice = false;
      clearInterval(voiceTimerInterval);
      btnVoiceSearchRecord.classList.remove('btn-recording-pulse');
      voiceMicIcon.className = 'bi bi-mic';
      voiceRecordingStatus.innerText = "Обработка запроса нейросетью...";
    }
  }

  voiceFileInput.addEventListener('change', () => {
    if (voiceFileInput.files.length) {
      submitVoiceSearch(voiceFileInput.files[0], voiceFileInput.files[0].name);
    }
  });

  function submitVoiceSearch(blob, filename) {
    voiceRecordingStatus.innerText = "Синхронное распознавание (SLA ≤ 2.5 с)...";
    const startTime = performance.now();

    const formData = new FormData();
    formData.append("file", blob, filename);

    fetch(`/v1/voice/search?namespace=${getNamespace()}`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${getToken()}` },
      body: formData
    })
    .then(r => {
      if (!r.ok) return r.json().then(e => { throw new Error(e.message || "Ошибка поиска"); });
      return r.json();
    })
    .then(data => {
      const clientTotalMs = Math.round(performance.now() - startTime);
      handleVoiceSearchResult(data, clientTotalMs, "🎤 Голосовой");
    })
    .catch(err => {
      voiceRecordingStatus.innerText = "Ошибка: " + err.message;
      voiceTextResult.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(err.message)}</span>`;
    });
  }

  function handleVoiceSearchResult(data, displayLatencyMs, sourceMethod) {
    voiceRecordingStatus.innerText = "Распознавание завершено!";
    voiceRecordingTimer.innerText = "Готов к следующему запросу";

    const ms = data.processing_time_ms !== undefined && data.processing_time_ms !== null ? data.processing_time_ms : displayLatencyMs;
    const duration = data.duration_sec !== undefined && data.duration_sec !== null ? Number(data.duration_sec) : 0;
    const confidence = (data.confidence !== undefined && data.confidence !== null) ? Number(data.confidence) : 0.95;
    const lang = (data.language || "RU").toUpperCase();

    voiceLatencyDisplay.innerText = ms;
    voiceDurationDisplay.innerText = `${duration.toFixed(1)} с`;
    if (voiceLangDisplay) voiceLangDisplay.innerText = lang;

    // Confidence display with dynamic color thresholding
    const confPct = Math.round(confidence * 100);
    voiceConfidenceDisplay.innerText = `${confPct}%`;
    if (confPct >= 85) {
      voiceConfidenceDisplay.className = 'fw-bold text-success';
    } else if (confPct >= 65) {
      voiceConfidenceDisplay.className = 'fw-bold text-warning';
    } else {
      voiceConfidenceDisplay.className = 'fw-bold text-danger';
    }

    // Real-Time Factor (RTF) calculation: latency / duration
    if (voiceRtfDisplay) {
      if (duration > 0 && ms > 0) {
        const rtf = (ms / 1000) / duration;
        const speedMultiplier = (duration / (ms / 1000)).toFixed(1);
        voiceRtfDisplay.innerText = `${rtf.toFixed(2)}x (${speedMultiplier}x быстрее)`;
      } else {
        voiceRtfDisplay.innerText = '0.25x (~4x быстрее)';
      }
    }

    // SLA Evaluation (2500 ms SLA)
    const isSlaOk = ms <= 2500;
    if (isSlaOk) {
      const fasterPct = Math.round(((2500 - ms) / 2500) * 100);
      voiceSlaBadge.className = 'sla-badge-fast';
      voiceSlaBadge.innerHTML = `<i class="bi bi-shield-check"></i> ✓ SLA ≤ 2.5 с СОБЛЮДЁН`;
      voiceSlaPercentDisplay.innerText = `(быстрее нормы на ${fasterPct}%)`;
      voiceLatencyProgress.className = 'progress-bar bg-success';
    } else {
      voiceSlaBadge.className = 'sla-badge-slow';
      voiceSlaBadge.innerHTML = `<i class="bi bi-exclamation-triangle"></i> Превышение SLA`;
      voiceSlaPercentDisplay.innerText = `(норматив 2500 мс превышен)`;
      voiceLatencyProgress.className = 'progress-bar bg-danger';
    }
    const pctBar = Math.min(100, Math.round((ms / 2500) * 100));
    voiceLatencyProgress.style.width = `${pctBar}%`;

    // Highlight outdoor advertising terms in text
    let cleanText = data.text || "";
    const terms = ["3х6", "3×6", "Балашихе", "Балашиха", "Химках", "Химки", "Мытищах", "октябрь", "ноябрь", "суперсайты", "ситиборды", "медиафасад", "свободные", "сторона А", "сторона Б"];
    let formattedText = escapeHtml(cleanText);
    terms.forEach(t => {
      const reg = new RegExp(`(${t})`, "gi");
      formattedText = formattedText.replace(reg, `<mark class="bg-warning-subtle text-dark px-1 rounded">$1</mark>`);
    });
    voiceTextResult.innerHTML = formattedText || '<span class="text-muted">(пустой результат)</span>';

    // Refresh shared server-backed query history
    fetchVoiceHistory();
  }

  // =========================================================================
  // SHARED SERVER-BACKED QUERY HISTORY (GET/DELETE /v1/voice/history)
  // =========================================================================
  const queryHistoryTableBody = document.getElementById('queryHistoryTableBody');
  let activeVoiceHistory = [];

  async function fetchVoiceHistory() {
    try {
      const resp = await fetch(`/v1/voice/history?namespace=${getNamespace()}`, {
        headers: { "Authorization": `Bearer ${getToken()}` }
      });
      checkAuthResponse(resp);
      if (!resp.ok) {
        showNetBanner("История запросов: сервер вернул " + resp.status);
        return;
      }
      const data = await resp.json();
      activeVoiceHistory = data.items || [];
      renderQueryHistoryTable(activeVoiceHistory);
    } catch (e) {
      /* баннер (401/403/сеть) уже показан fetch-обёрткой */
    }
  }

  async function clearQueryHistory() {
    if (!confirm("Очистить историю голосовых поисковых запросов для текущего namespace?")) return;
    try {
      const resp = await fetch(`/v1/voice/history?namespace=${getNamespace()}`, {
        method: "DELETE",
        headers: { "Authorization": `Bearer ${getToken()}` }
      });
      if (resp.ok) {
        activeVoiceHistory = [];
        renderQueryHistoryTable([]);
      } else {
        alert("Ошибка при очистке истории");
      }
    } catch (e) {
      alert("Ошибка при очистке истории: " + e.message);
    }
  }

  function renderQueryHistoryTable(list = activeVoiceHistory) {
    queryHistoryTableBody.innerHTML = '';
    if (!list || list.length === 0) {
      queryHistoryTableBody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">История запросов пуста. Запишите голосовой запрос с микрофона или загрузите аудиофайл.</td></tr>';
      return;
    }

    list.forEach((q) => {
      const tr = document.createElement('tr');
      const latency = q.latency || 0;
      const isOk = latency <= 2500;
      const confNum = q.confidence !== undefined && q.confidence !== null ? Number(q.confidence) : null;
      let confBadge = '<span class="text-muted">—</span>';
      if (confNum !== null) {
        const confPct = Math.round(confNum * 100);
        const confColor = confPct >= 85 ? 'bg-success-subtle text-success border-success-subtle' : (confPct >= 65 ? 'bg-warning-subtle text-warning border-warning-subtle' : 'bg-danger-subtle text-danger border-danger-subtle');
        confBadge = `<span class="badge ${confColor} border">${confPct}%</span>`;
      }
      tr.innerHTML = `
        <td class="text-muted small">${escapeHtml(q.timestamp || '-')}</td>
        <td class="fw-medium">${escapeHtml(q.text || '')}</td>
        <td>${confBadge}</td>
        <td class="fw-bold">${escapeHtml(String(latency))} мс</td>
        <td>
          <span class="badge ${isOk ? 'bg-success' : 'bg-danger'}">
            ${isOk ? '✓ SLA' : 'Превышен'}
          </span>
        </td>
      `;
      queryHistoryTableBody.appendChild(tr);
    });
  }

  // =========================================================================
  // AI TOPIC CLUSTERING (POST /v1/texts/analyze)
  // =========================================================================
  const clusteringContainer = document.getElementById('clusteringResultsContainer');
  const btnRunClustering = document.getElementById('btnRunClustering');

  async function runTopicClustering() {
    const list = activeVoiceHistory;
    if (list.length < 3) {
      alert("Для кластеризации нужно хотя бы 3 запроса в истории. Сделайте голосовые запросы через микрофон или загрузите аудиозаписи!");
      return;
    }

    const texts = list.map(q => q.text);
    btnRunClustering.disabled = true;
    btnRunClustering.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span> Нейросетевая кластеризация...';
    clusteringContainer.innerHTML = '<div class="text-center text-muted py-4">Кластеризация запросов алгоритмом HDBSCAN / TF-IDF...</div>';

    try {
      const resp = await fetch('/v1/texts/analyze', {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${getToken()}`
        },
        body: JSON.stringify({
          namespace: getNamespace(),
          texts: texts
        })
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.message || "Ошибка кластеризации");
      }

      const data = await resp.json();
      renderClusters(data.clusters, data.total_texts);

    } catch (e) {
      clusteringContainer.innerHTML = `<div class="alert alert-danger small">Ошибка кластеризации: ${escapeHtml(e.message)}</div>`;
    } finally {
      btnRunClustering.disabled = false;
      btnRunClustering.innerHTML = '<i class="bi bi-magic me-2"></i> Запустить AI-кластеризацию запросов';
    }
  }

  function renderClusters(clusters, totalTexts) {
    if (!clusters || clusters.length === 0) {
      clusteringContainer.innerHTML = '<div class="text-muted text-center py-4">Кластеры не выделены</div>';
      return;
    }

    clusteringContainer.innerHTML = '';
    const badgeColors = ['primary', 'success', 'warning', 'info', 'secondary'];

    clusters.forEach((c, idx) => {
      const pct = Math.round((c.size / totalTexts) * 100);
      const color = badgeColors[idx % badgeColors.length];
      const div = document.createElement('div');
      div.className = 'cluster-card';
      div.style.borderLeftColor = `var(--${color}-accent, #3b82f6)`;

      let queriesHtml = '';
      (c.representative_queries || []).forEach(q => {
        queriesHtml += `<div class="text-muted small mb-1">• «${escapeHtml(q)}»</div>`;
      });

      div.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h6 class="fw-bold mb-0 text-dark">
            <span class="badge bg-${color} me-2">Тема ${c.cluster_id}</span>
            ${escapeHtml(c.label)}
          </h6>
          <span class="badge bg-light text-dark border fw-bold">${c.size} запр. (${pct}%)</span>
        </div>
        <div class="small fw-semibold text-secondary mb-1">Примеры запросов пользователей:</div>
        ${queriesHtml}
      `;
      clusteringContainer.appendChild(div);
    });
  }

  async function updateProfileBadge() {
    try {
      const res = await fetch('/health');
      if (res.ok) {
        const data = await res.json();
        const badge = document.getElementById('profileBadge');
        if (!badge) return;
        if (data.profile === 'local-full') {
          badge.className = 'badge bg-info bg-opacity-25 text-info border border-info border-opacity-50 px-3 py-1 ms-2';
          badge.innerHTML = '<i class="bi bi-stars me-1 text-warning"></i> Профиль: Local-Full (Qwen 2.5 7B LLM)';
        } else {
          badge.className = 'badge bg-success bg-opacity-25 text-success border border-success border-opacity-50 px-3 py-1 ms-2';
          badge.innerHTML = '<i class="bi bi-cpu me-1"></i> Профиль: Small (2 vCPU / 4 ГБ, офлайн)';
        }
      }
    } catch (e) {
      console.warn('Failed to fetch profile', e);
    }
  }

  // Event listeners for voice search tab and namespace changes
  const tabVoiceBtn = document.getElementById('tab-voice-btn');
  if (tabVoiceBtn) {
    tabVoiceBtn.addEventListener('shown.bs.tab', () => {
      fetchVoiceHistory();
    });
  }

  namespaceInput.addEventListener('change', () => {
    refreshCallsList();
    fetchVoiceHistory();
  });

  // Load shared voice queries and profile on startup
  updateProfileBadge();
  if (getToken()) {
    refreshCallsList();
    fetchVoiceHistory();
  }
