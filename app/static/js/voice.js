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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1
        }
      });
      voiceMediaRecorder = new MediaRecorder(stream);
      voiceAudioChunks = [];
      voiceRecordStartTime = Date.now();

      voiceMediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) voiceAudioChunks.push(e.data);
      };

      voiceMediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        // MediaRecorder даёт webm/opus, а быстрый путь /v1/voice/search берёт
        // только PCM WAV — конвертируем в браузере (WAV PCM16 16 кГц моно).
        // При неудаче шлём как есть: сервер ответит честной ошибкой.
        const rawBlob = new Blob(voiceAudioChunks, { type: voiceMediaRecorder.mimeType || 'audio/webm' });
        voiceRecordingStatus.innerText = "Подготовка аудио (конвертация в WAV 16 кГц)...";
        rawBlob.arrayBuffer()
          .then((ab) => decodeToMono16kWav(ab))
          .catch(() => rawBlob)
          .then((blob) => submitVoiceSearch(blob, "mic_voice_search.wav"));
      };

      voiceMediaRecorder.start();
      isRecordingVoice = true;
      btnVoiceSearchRecord.classList.add('btn-recording-pulse');
      voiceMicIcon.className = 'bi bi-stop-fill';
      voiceRecordingStatus.innerText = "Идёт запись... Говорите фразу!";
      
      voiceTimerInterval = setInterval(() => {
        const elapsed = (Date.now() - voiceRecordStartTime) / 1000;
        const remain = Math.max(0, VOICE_RECORD_LIMIT_SEC - elapsed).toFixed(0);
        voiceRecordingTimer.innerText = `Запись: ${elapsed.toFixed(1)} с (Кликните для отправки или авто-стоп через ${remain} с)`;
      }, 100);

      if (voiceAutoStopTimeout) clearTimeout(voiceAutoStopTimeout);
      voiceAutoStopTimeout = setTimeout(() => {
        if (isRecordingVoice) stopVoiceRecording();
      }, VOICE_RECORD_LIMIT_SEC * 1000);

    } catch (err) {
      voiceRecordingStatus.innerText = "Нет доступа к микрофону: " + err.message;
    }
  }

  // Лимит записи голосового запроса — как у POST /v1/voice/search (до 60 с).
  const VOICE_RECORD_LIMIT_SEC = 60;
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
    .then(async r => {
      if (r.ok) return r.json();
      let body = null;
      try { body = await r.json(); } catch (e) { body = null; }
      const err = new Error((body && body.message) || `Сервер вернул ${r.status}`);
      err.status = r.status;
      try { err.retryAfter = r.headers.get('Retry-After'); } catch (e) { err.retryAfter = null; }
      throw err;
    })
    .then(data => {
      const clientTotalMs = Math.round(performance.now() - startTime);
      handleVoiceSearchResult(data, clientTotalMs, "🎤 Голосовой");
    })
    .catch(err => {
      // 503/429 — воркеры не прогреты или очередь занята: понятный текст
      // с Retry-After, а не «Ошибка распознавания». Без статуса — обрыв сети.
      if (err && (err.status === 503 || err.status === 429)) {
        const wait = err.retryAfter ? ` Повторите через ${err.retryAfter} с.` : ' Повторите чуть позже.';
        voiceRecordingStatus.innerText = `Сервис распознавания временно недоступен (${err.status}).${wait}`;
        voiceTextResult.innerHTML = `<span class="text-warning">Временно недоступно (${escapeHtml(String(err.status))}): повторите запрос.${err.retryAfter ? ` Retry-After: ${escapeHtml(String(err.retryAfter))} с.` : ''}</span>`;
      } else if (err && err.status) {
        voiceRecordingStatus.innerText = "Ошибка: " + err.message;
        voiceTextResult.innerHTML = `<span class="text-danger">Ошибка: ${escapeHtml(err.message)}</span>`;
      } else {
        voiceRecordingStatus.innerText = "Нет связи с сервером. Проверьте, что сервис запущен, и повторите.";
        voiceTextResult.innerHTML = '<span class="text-danger">Нет связи с сервером</span>';
      }
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
        voiceRtfDisplay.innerText = '—';
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

  let clearHistoryArmed = false;
  let clearHistoryTimer = null;

  async function clearQueryHistory() {
    const btn = document.getElementById('btnClearHistory');
    if (!clearHistoryArmed) {
      clearHistoryArmed = true;
      if (btn) {
        btn.dataset.label = btn.innerHTML;
        btn.innerHTML = 'Нажмите ещё раз для подтверждения';
        btn.classList.replace('btn-outline-danger', 'btn-danger');
      }
      if (clearHistoryTimer) clearTimeout(clearHistoryTimer);
      clearHistoryTimer = setTimeout(() => {
        clearHistoryArmed = false;
        if (btn && btn.dataset.label) {
          btn.innerHTML = btn.dataset.label;
          btn.classList.replace('btn-danger', 'btn-outline-danger');
        }
      }, 5000);
      return;
    }
    clearHistoryArmed = false;
    if (clearHistoryTimer) clearTimeout(clearHistoryTimer);
    if (btn && btn.dataset.label) {
      btn.innerHTML = btn.dataset.label;
      btn.classList.replace('btn-danger', 'btn-outline-danger');
    }
    try {
      const resp = await fetch(`/v1/voice/history?namespace=${getNamespace()}`, {
        method: "DELETE",
        headers: { "Authorization": `Bearer ${getToken()}` }
      });
      checkAuthResponse(resp);
      if (resp.ok) {
        activeVoiceHistory = [];
        renderQueryHistoryTable([]);
      } else {
        showNetBanner(`Не удалось очистить историю: сервер вернул ${resp.status}`);
      }
    } catch (e) {
      if (!(e && e.auth)) showNetBanner("Не удалось очистить историю: " + (e.message || e));
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

  async function collectCallTexts(limit) {
    // Тексты звонков через существующие ручки: список + расшифровка каждого.
    const listResp = await fetch(
      `/v1/calls?${new URLSearchParams({ namespace: getNamespace(), limit: String(limit) }).toString()}`,
      { headers: getHeaders() }
    );
    checkAuthResponse(listResp);
    if (!listResp.ok) throw new Error(`список звонков: сервер вернул ${listResp.status}`);
    const list = await listResp.json();
    const items = (list && list.items) || [];
    const texts = [];
    for (const c of items) {
      if (!c.call_id) continue;
      const tResp = await fetch(
        `/v1/calls/${c.call_id}/transcript?${new URLSearchParams({ namespace: getNamespace() }).toString()}`,
        { headers: getHeaders() }
      );
      checkAuthResponse(tResp);
      if (!tResp.ok) continue;
      const t = await tResp.json();
      if (t && t.full_text && t.full_text.trim()) texts.push(t.full_text.trim());
    }
    return texts;
  }

  function clusteringNotice(message, isError) {
    clusteringContainer.innerHTML = `<div class="alert ${isError ? 'alert-danger' : 'alert-info'} small">${escapeHtml(message)}</div>`;
  }

  async function runTopicClustering() {
    const useCalls = document.getElementById('clusterSourceCalls')
      && document.getElementById('clusterSourceCalls').checked;
    let texts;
    if (useCalls) {
      setText(clusteringContainer, 'Загрузка текстов звонков из журнала...');
      try {
        texts = await collectCallTexts(20);
      } catch (e) {
        if (!(e && e.auth)) clusteringNotice(`Не удалось собрать тексты звонков: ${e.message || e}`, true);
        return;
      }
      if (texts.length < 2) {
        clusteringNotice("В журнале нет расшифровок для кластеризации (нужно хотя бы 2 непустых текста).", true);
        return;
      }
    } else {
      const list = activeVoiceHistory;
      if (list.length < 3) {
        clusteringNotice("Для кластеризации нужно хотя бы 3 запроса в истории. Сделайте голосовые запросы через микрофон или загрузите аудиозаписи!", true);
        return;
      }
      texts = list.map(q => q.text).filter((t) => t && t.trim());
      if (texts.length < 3) {
        clusteringNotice("В истории нет текстов запросов для кластеризации.", true);
        return;
      }
    }
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

  // =========================================================================
  // СВОДКА ПО ЗВОНКАМ (GET /v1/analytics/summary) + CSV (GET /v1/analytics/export_csv)
  // Распределения — CSS-барами без внешних библиотек; весь текст через textContent.
  // =========================================================================
  const summaryBody = document.getElementById('summaryBody');
  const summaryTopics = document.getElementById('summaryTopics');
  const summaryEntities = document.getElementById('summaryEntities');
  const summaryChecklist = document.getElementById('summaryChecklist');
  const summaryTrends = document.getElementById('summaryTrends');
  const summaryFilterError = document.getElementById('summaryFilterError');
  const SUMMARY_CHECKLIST_LABELS = [
    ['average_score', 'Средняя оценка (из 5)', false],
    ['greeting_pct', 'Приветствие', true],
    ['need_identification_pct', 'Выявление потребности', true],
    ['options_offered_pct', 'Предложение вариантов', true],
    ['next_step_named_pct', 'Следующий шаг', true],
    ['contacts_taken_pct', 'Контакты', true],
  ];

  function summaryBarRow(label, displayValue, frac) {
    const row = document.createElement('div');
    row.className = 'mb-1';
    const top = document.createElement('div');
    top.className = 'd-flex justify-content-between small';
    const name = document.createElement('span');
    name.textContent = label;
    const val = document.createElement('span');
    val.className = 'fw-bold';
    val.textContent = displayValue;
    top.appendChild(name);
    top.appendChild(val);
    const track = document.createElement('div');
    track.className = 'progress progress-slim';
    const bar = document.createElement('div');
    bar.className = 'progress-bar bg-success';
    bar.style.width = `${Math.max(0, Math.min(100, Math.round(frac * 100)))}%`;
    track.appendChild(bar);
    row.appendChild(top);
    row.appendChild(track);
    return row;
  }

  function readSummaryFilter() {
    const val = (id) => {
      const el = document.getElementById(id);
      return el ? el.value.trim() : '';
    };
    const fromIso = toIsoOrNull(val('sumFrom'));
    const toIso = toIsoOrNull(val('sumTo'));
    if (fromIso === null || toIso === null) {
      setText(summaryFilterError, 'Некорректная дата в фильтре периода.');
      return null;
    }
    return {
      date_from: fromIso,
      date_to: toIso,
      manager_id: val('sumManager'),
      inquiry_type: val('sumType'),
    };
  }

  function summaryParams() {
    const f = readSummaryFilter();
    if (!f) return null;
    if (f.date_from && f.date_to && f.date_from > f.date_to) {
      setText(summaryFilterError, '«Период с» позже, чем «Период по». Исправьте даты.');
      return null;
    }
    setText(summaryFilterError, '');
    const params = new URLSearchParams({ namespace: getNamespace() });
    if (f.date_from) params.set('date_from', f.date_from);
    if (f.date_to) params.set('date_to', f.date_to);
    if (f.manager_id) params.set('manager_id', f.manager_id);
    if (f.inquiry_type) params.set('inquiry_type', f.inquiry_type);
    return params;
  }

  function renderSummary(data) {
    const total = (data && data.total_calls) || 0;
    setText(summaryBody, total === 0
      ? 'Нет данных за выбранный период'
      : `Звонков за период: ${total}`);
    const dist = (data && data.inquiry_distribution) || {};
    summaryTopics.innerHTML = '';
    const topics = Object.entries(dist).sort((a, b) => b[1] - a[1]);
    if (topics.length === 0) {
      setText(summaryTopics, '—');
    } else {
      const max = topics.map((t) => t[1]).reduce((m, v) => Math.max(m, v), 1);
      topics.forEach(([key, cnt]) => {
        summaryTopics.appendChild(summaryBarRow(INQUIRY_TITLES[key] || key, `${cnt}`, cnt / max));
      });
    }
    const top = (data && data.top_entities) || {};
    summaryEntities.innerHTML = '';
    const ents = [
      ...((top.cities || []).map((x) => [`📍 ${x.name}`, x.count])),
      ...((top.formats || []).map((x) => [`📐 ${x.name}`, x.count])),
    ].sort((a, b) => b[1] - a[1]).slice(0, 10);
    if (ents.length === 0) {
      setText(summaryEntities, '—');
    } else {
      const max = ents.map((x) => x[1]).reduce((m, v) => Math.max(m, v), 1);
      ents.forEach(([name, cnt]) => {
        summaryEntities.appendChild(summaryBarRow(name, `${cnt}`, cnt / max));
      });
    }
    const avg = (data && data.checklist_averages) || {};
    summaryChecklist.innerHTML = '';
    SUMMARY_CHECKLIST_LABELS.forEach(([key, label, isPct]) => {
      const v = avg[key];
      if (v === undefined || v === null) return;
      summaryChecklist.appendChild(summaryBarRow(
        label, isPct ? `${v}%` : String(v), isPct ? v / 100 : v / 5));
    });
    if (!summaryChecklist.hasChildNodes()) setText(summaryChecklist, '—');
    const trends = (data && data.weekly_trends) || [];
    summaryTrends.innerHTML = '';
    if (trends.length === 0) {
      setText(summaryTrends, '—');
    } else {
      const max = trends.map((t) => t.calls_count).reduce((m, v) => Math.max(m, v), 1);
      trends.forEach((t) => {
        summaryTrends.appendChild(summaryBarRow(
          t.week || '—', `${t.calls_count}`, t.calls_count / max));
      });
    }
  }

  function fetchSummary() {
    const params = summaryParams();
    if (!params) return;
    setText(summaryBody, 'Загрузка сводки...');
    fetch(`/v1/analytics/summary?${params.toString()}`, { headers: getHeaders() })
      .then(checkAuthResponse).then(res => res.json())
      .then(renderSummary)
      .catch(err => {
        if (err && err.auth) {
          setText(summaryBody, 'Нет доступа: введите токен с правами read.');
          return;
        }
        setText(summaryBody, `Ошибка загрузки сводки: ${err.message || err}`);
      });
  }

  function exportSummaryCsv() {
    const params = summaryParams();
    if (!params) return;
    fetch(`/v1/analytics/export_csv?${params.toString()}`, { headers: getHeaders() })
      .then(checkAuthResponse)
      .then(res => {
        if (!res.ok) throw new Error(`сервер вернул ${res.status}`);
        return res.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `summary-${getNamespace()}-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      })
      .catch(err => {
        if (!(err && err.auth)) showNetBanner(`Не удалось скачать CSV: ${err.message || err}`);
      });
  }

  const btnRefreshSummary = document.getElementById('btnRefreshSummary');
  if (btnRefreshSummary) btnRefreshSummary.addEventListener('click', fetchSummary);
  const summaryFilterForm = document.getElementById('summaryFilterForm');
  if (summaryFilterForm) {
    summaryFilterForm.addEventListener('submit', (e) => { e.preventDefault(); fetchSummary(); });
  }
  const btnExportSummaryCsv = document.getElementById('btnExportSummaryCsv');
  if (btnExportSummaryCsv) btnExportSummaryCsv.addEventListener('click', exportSummaryCsv);

  namespaceInput.addEventListener('change', () => {
    saveAuthInputs();
    if (typeof fetchVoiceHistory === 'function') fetchVoiceHistory();
    if (typeof fetchSummary === 'function') fetchSummary();
  });

  // Старт страницы голосового поиска.
  updateProfileBadge();
  if (getToken() && typeof fetchVoiceHistory === 'function') {
    fetchVoiceHistory();
  }
  if (getToken() && typeof fetchSummary === 'function') {
    fetchSummary();
  }
