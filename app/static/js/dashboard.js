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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1
        }
      });
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

  // Состояние списка: фильтры уходят в параметры существующей ручки
  // GET /v1/calls (q, manager_id, inquiry_type, date_from, date_to, cursor, limit).
  // Сервер всегда сортирует по дате (новые сначала); переключатель порядка
  // применяется локально к загруженным строкам.
  const callsState = { items: [], nextCursor: null, sortDir: 'desc', loading: false };
  const callsFilterForm = document.getElementById('callsFilterForm');
  const callsFilterError = document.getElementById('callsFilterError');
  const callsCount = document.getElementById('callsCount');
  const btnMoreCalls = document.getElementById('btnMoreCalls');
  const btnSortDir = document.getElementById('btnSortDir');
  const btnResetFilters = document.getElementById('btnResetFilters');

  function readCallsFilter() {
    const val = (id) => {
      const el = document.getElementById(id);
      return el ? el.value.trim() : '';
    };
    const filter = {
      q: val('fltQ'),
      manager_id: val('fltManager'),
      inquiry_type: val('fltType'),
      date_from: val('fltFrom'),
      date_to: val('fltTo'),
      limit: val('fltLimit') || '15',
    };
    if (filter.date_from) filter.date_from = new Date(filter.date_from).toISOString();
    if (filter.date_to) filter.date_to = new Date(filter.date_to).toISOString();
    return filter;
  }

  function appendCallRow(c) {
    const tr = document.createElement('tr');
    const durStr = c.duration_sec ? `${Math.round(c.duration_sec)} с` : '-';
    const dtStr = c.created_at ? new Date(c.created_at).toLocaleString('ru-RU') : '-';
    const safeId = escapeHtml(c.call_id);
    const safeExternalId = c.external_id ? escapeHtml(c.external_id) : '<span class="text-muted">-</span>';
    const safeManagerId = c.manager_id ? escapeHtml(c.manager_id) : 'Автоматически';
    const answerBadge = c.is_answering_machine
      ? '<span class="badge bg-danger">Автоответчик</span>'
      : '<span class="badge bg-success">Живой разговор</span>';
    tr.innerHTML = `
      <td><code>${safeId.substring(0, 8)}...</code></td>
      <td>${safeExternalId}</td>
      <td><span class="badge bg-light text-dark border">${safeManagerId}</span></td>
      <td>${durStr}</td>
      <td>${answerBadge}</td>
      <td class="small text-muted">${escapeHtml(dtStr)}</td>
      <td>
        <button class="btn btn-outline-primary btn-sm py-0 px-2" data-call-id="${safeId}">
          <i class="bi bi-play-circle me-1"></i> Открыть
        </button>
      </td>
    `;
    callsTableBody.appendChild(tr);
  }

  function renderCallsList() {
    const rows = callsState.sortDir === 'asc' ? [...callsState.items].reverse() : callsState.items;
    callsTableBody.innerHTML = '';
    if (rows.length === 0) {
      callsTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">Нет звонков по заданным фильтрам</td></tr>';
    } else {
      rows.forEach(appendCallRow);
    }
    const shown = `Показано: ${callsState.items.length}`;
    setText(callsCount, callsState.nextCursor ? `${shown}. Есть ещё — нажмите «Загрузить ещё».` : `${shown}. Это все.`);
    btnMoreCalls.classList.toggle('d-none', !callsState.nextCursor);
  }

  function loadCallsPage(append) {
    if (callsState.loading) return;
    const filter = readCallsFilter();
    if (filter.date_from && filter.date_to && filter.date_from > filter.date_to) {
      setText(callsFilterError, '«Период с» позже, чем «Период по». Исправьте даты.');
      return;
    }
    setText(callsFilterError, '');
    callsState.loading = true;
    if (!append) {
      callsTableBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-3">Загрузка звонков...</td></tr>';
    }
    btnMoreCalls.disabled = true;
    const params = new URLSearchParams({ namespace: getNamespace(), limit: filter.limit });
    if (filter.q) params.set('q', filter.q);
    if (filter.manager_id) params.set('manager_id', filter.manager_id);
    if (filter.inquiry_type) params.set('inquiry_type', filter.inquiry_type);
    if (filter.date_from) params.set('date_from', filter.date_from);
    if (filter.date_to) params.set('date_to', filter.date_to);
    if (append && callsState.nextCursor) params.set('cursor', callsState.nextCursor);
    fetch(`/v1/calls?${params.toString()}`, { headers: getHeaders() })
      .then(checkAuthResponse).then(res => res.json())
      .then(data => {
        callsState.loading = false;
        btnMoreCalls.disabled = false;
        if (!append) callsState.items = [];
        (data.items || []).forEach((c) => callsState.items.push(c));
        callsState.nextCursor = data.next_cursor || null;
        renderCallsList();
      })
      .catch(err => {
        callsState.loading = false;
        btnMoreCalls.disabled = false;
        if (append) return;
        const msg = (err && err.auth)
          ? "Нет доступа: введите токен с правами read."
          : `Ошибка загрузки: ${escapeHtml(err.message || err)}`;
        callsTableBody.innerHTML = `<tr><td colspan="7" class="text-center text-danger py-3">${msg}</td></tr>`;
        setText(callsCount, '');
        btnMoreCalls.classList.add('d-none');
      });
  }

  function refreshCallsList() {
    callsState.nextCursor = null;
    loadCallsPage(false);
  }

  if (callsFilterForm) {
    callsFilterForm.addEventListener('submit', (e) => {
      e.preventDefault();
      refreshCallsList();
    });
  }
  if (btnResetFilters) {
    btnResetFilters.addEventListener('click', () => {
      if (callsFilterForm) callsFilterForm.reset();
      setText(callsFilterError, '');
      refreshCallsList();
    });
  }
  if (btnMoreCalls) {
    btnMoreCalls.addEventListener('click', () => loadCallsPage(true));
  }
  if (btnSortDir) {
    btnSortDir.addEventListener('click', () => {
      callsState.sortDir = callsState.sortDir === 'desc' ? 'asc' : 'desc';
      setText(btnSortDir, callsState.sortDir === 'desc' ? 'Порядок: новые → старые' : 'Порядок: старые → новые');
      renderCallsList();
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

  namespaceInput.addEventListener('change', () => {
    saveAuthInputs();
    if (typeof refreshCallsList === 'function') refreshCallsList();
  });

  // Старт страницы звонков.
  updateProfileBadge();
  if (getToken() && typeof refreshCallsList === 'function') {
    refreshCallsList();
  }
