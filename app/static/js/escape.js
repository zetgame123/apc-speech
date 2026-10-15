/* Общий хелпер безопасного вывода текста API в дашборде.
 * Правило: весь текст из API (расшифровки, имена, темы) выводится только
 * через textContent либо через escapeHtml() перед innerHTML.
 * Никакого innerHTML с неэкранированными данными, никаких inline-onclick.
 * Файл грузится до основных скриптов страниц; работает и в Node (для тестов).
 */
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/* Безопасно положить текст в элемент (аналог textContent с защитой от null). */
function setText(el, value) {
  if (!el) return;
  el.textContent = value === null || value === undefined ? '' : String(value);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { escapeHtml: escapeHtml, setText: setText };
}
