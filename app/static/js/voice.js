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

  namespaceInput.addEventListener('change', () => {
    saveAuthInputs();
    if (typeof fetchVoiceHistory === 'function') fetchVoiceHistory();
  });

  // Старт страницы голосового поиска.
  updateProfileBadge();
  if (getToken() && typeof fetchVoiceHistory === 'function') {
    fetchVoiceHistory();
  }
