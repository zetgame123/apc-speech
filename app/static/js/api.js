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
    tokenField.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submit.click();
    });
    nsInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submit.click();
    });
    tokenField.focus();
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
  function getHeaders() {
    return {
      "Authorization": `Bearer ${getToken()}`,
      "Accept": "application/json"
    };
  }
  function getNamespace() {
    return readNamespace();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAuth);
  } else {
    initAuth();
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
