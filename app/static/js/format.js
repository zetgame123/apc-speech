/* Форматирование времени/дат для дашборда (общее, без DOM-данных). */
  function formatTime(sec) {
    if (isNaN(sec)) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}`;
  }

/* Преобразует значение datetime-local в ISO-строку. Пусто -> ''. Мусор -> null. */
function toIsoOrNull(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}
/* Названия типов обращений — из config/taxonomy.yaml; значений call_result —
 * из VALID_CALL_RESULTS (app/llm/local_adapter.py). Неизвестные слаги
 * показываются как есть, а не прячутся. */
const INQUIRY_TITLES = {
  pricing: 'Стоимость и прайс',
  availability: 'Занятость и свободные периоды',
  inventory_search: 'Подбор мест и инвентаря',
  specs: 'Технические требования',
  order_status: 'Статус заказа и брони',
  production: 'Производство и монтаж',
  documents_billing: 'Документы и бухгалтерия',
  complaint: 'Претензии и проблемы',
  partnership_supply: 'Сотрудничество и инвентарь партнёров',
  programmatic_digital: 'Диджитал и программатик',
  spam_irrelevant: 'Спам и нецелевые звонки',
  other: 'Прочее',
};
const CALL_RESULT_TITLES = {
  success_deal: 'Успех / сделка',
  follow_up_scheduled: 'Назначен следующий шаг',
  info_provided: 'Информация предоставлена',
  client_declined: 'Клиент отказался',
  unclear_spam: 'Неясно / спам',
};
const AGREEMENT_WHO = { manager: 'Менеджер', client: 'Клиент', both: 'Оба' };
