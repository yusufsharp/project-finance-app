'use strict';
const $ = (id) => document.getElementById(id);
const state = { operations: [], page: 1, loaded: false, loading: false, editing: null, deleting: null, saving: false, deletingBusy: false };
const PAGE_SIZE = 8;
const currency = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 });
const money = (value) => currency.format(value / 100);
const cents = (row) => Math.round(Number(row.amount) * 100);
const dateFormatter = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' });
const longDateFormatter = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
let toastTimer;
let lastFocused;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#i-' + name);
  svg.setAttribute('aria-hidden', 'true');
  svg.append(use);
  return svg;
}
function notify(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 18000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers }, cache: 'no-store' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Сервис временно недоступен. Повторите попытку.');
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Сервис не ответил вовремя. Обновите список перед повторной записью.');
    if (error instanceof TypeError) throw new Error('Нет связи с сервером. Проверьте соединение и повторите попытку.');
    throw error;
  } finally { clearTimeout(timeout); }
}
async function load() {
  if (state.loading) return false;
  state.loading = true;
  $('refresh').disabled = true;
  $('sync-status').textContent = 'Обновляем данные';
  try {
    const result = await api('/operations');
    state.operations = result.operations;
    state.loaded = true;
    $('load-error').hidden = true;
    $('sync-status').classList.remove('offline');
    $('sync-status').replaceChildren(element('span'), document.createTextNode('Данные обновлены'));
    render();
    return true;
  } catch (error) {
    $('load-error').hidden = false;
    $('load-error').querySelector('span').textContent = error.message;
    $('sync-status').classList.add('offline');
    $('sync-status').replaceChildren(element('span'), document.createTextNode(state.loaded ? 'Данные могут быть устаревшими' : 'Нет связи с сервером'));
    if (!state.loaded) {
      $('results-count').textContent = 'Не удалось загрузить данные';
      $('income-count').textContent = 'Данные недоступны';
      $('expense-count').textContent = 'Данные недоступны';
      $('stats').setAttribute('aria-busy', 'false');
      $('category-bars').replaceChildren(element('p', 'panel-caption', 'Данные пока недоступны. Повторите загрузку.'));
    }
    return false;
  } finally {
    state.loading = false;
    $('refresh').disabled = false;
    $('loading').hidden = true;
  }
}
function periodRows() {
  const period = $('period').value;
  const now = new Date();
  let from;
  if (period === 'month') from = new Date(now.getFullYear(), now.getMonth(), 1);
  else if (period === '30days') { from = new Date(now); from.setDate(now.getDate() - 29); from.setHours(0, 0, 0, 0); }
  return state.operations.filter((row) => !from || (new Date(row.created_at) >= from && new Date(row.created_at) <= now));
}
function render() {
  const rows = periodRows();
  const incomes = rows.filter((r) => r.kind === 'income');
  const expenses = rows.filter((r) => r.kind === 'expense');
  const income = incomes.reduce((sum, r) => sum + cents(r), 0);
  const expense = expenses.reduce((sum, r) => sum + cents(r), 0);
  $('balance').textContent = money(income - expense);
  $('income').textContent = money(income);
  $('expense').textContent = money(expense);
  $('income-count').textContent = `${incomes.length} ${plural(incomes.length)} за период`;
  $('expense-count').textContent = `${expenses.length} ${plural(expenses.length)} за период`;
  $('stats').setAttribute('aria-busy', 'false');
  $('nav-count').textContent = state.operations.length;
  const byCategory = new Map();
  for (const row of expenses) byCategory.set(row.category, (byCategory.get(row.category) || 0) + cents(row));
  let categories = [...byCategory.entries()].sort((a, b) => b[1] - a[1]);
  if (categories.length > 5) categories = [...categories.slice(0, 4), ['Остальные категории', categories.slice(4).reduce((sum, [, amount]) => sum + amount, 0)]];
  $('category-bars').replaceChildren();
  for (const [name, amount] of categories) {
    const block = element('div');
    const line = element('div', 'category-line');
    const label = element('span', '', name);
    label.title = name;
    line.append(label, element('strong', '', money(amount)));
    const meter = element('meter', 'category-meter');
    meter.min = 0; meter.max = expense; meter.value = amount;
    meter.setAttribute('aria-label', `${name}: ${money(amount)}, ${Math.round(amount / expense * 100)}% расходов`);
    block.append(line, meter);
    $('category-bars').append(block);
  }
  if (!categories.length) $('category-bars').append(element('p', 'panel-caption', 'Пока нет расходов за этот период. Добавьте первую покупку, чтобы увидеть распределение.'));
  $('category-total').textContent = money(expense);
  renderTable(rows);
}
function plural(count) {
  if (count % 10 === 1 && count % 100 !== 11) return 'операция';
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return 'операции';
  return 'операций';
}
function renderTable(period = periodRows()) {
  const query = $('search').value.trim().toLocaleLowerCase('ru-RU');
  const kind = $('kind-filter').value;
  const rows = period.filter((r) => (kind === 'all' || r.kind === kind) && r.category.toLocaleLowerCase('ru-RU').includes(query));
  state.page = Math.min(state.page, Math.max(1, Math.ceil(rows.length / PAGE_SIZE)));
  const start = (state.page - 1) * PAGE_SIZE;
  $('operation-rows').replaceChildren();
  for (const row of rows.slice(start, start + PAGE_SIZE)) {
    const tr = element('tr');
    const main = element('td');
    const name = element('div', 'operation-name');
    const badge = element('span', 'operation-icon ' + row.kind);
    badge.append(icon('arrow'));
    const copy = element('div', 'operation-copy');
    const category = element('strong', '', row.category);
    category.title = row.category;
    copy.append(category, element('small', '', row.kind === 'income' ? 'Доход' : 'Расход'));
    name.append(badge, copy); main.append(name);
    const date = new Date(row.created_at);
    const dateCell = element('td', 'date-cell', dateFormatter.format(date));
    dateCell.title = longDateFormatter.format(date);
    const amountCell = element('td', 'amount-cell operation-amount ' + row.kind, `${row.kind === 'income' ? '+' : '−'} ${money(cents(row))}`);
    const actionsCell = element('td');
    const actions = element('div', 'row-actions');
    const edit = element('button', 'icon-button');
    edit.append(icon('edit')); edit.title = 'Редактировать';
    edit.setAttribute('aria-label', 'Редактировать: ' + row.category);
    edit.addEventListener('click', () => openForm(row));
    const remove = element('button', 'icon-button remove');
    remove.append(icon('trash')); remove.title = 'Удалить';
    remove.setAttribute('aria-label', 'Удалить: ' + row.category);
    remove.addEventListener('click', () => openDelete(row));
    actions.append(edit, remove); actionsCell.append(actions);
    tr.append(main, dateCell, amountCell, actionsCell);
    $('operation-rows').append(tr);
  }
  $('results-count').textContent = `${rows.length} ${plural(rows.length)}${query || kind !== 'all' ? ' по вашим условиям' : ' за выбранный период'}`;
  $('empty-state').hidden = rows.length > 0;
  const first = state.operations.length === 0;
  $('empty-title').textContent = first ? 'Начнём с первой операции' : 'Ничего не нашлось';
  $('empty-text').textContent = first ? 'Добавьте доход или расход — здесь появится ваша история.' : 'Попробуйте другую категорию, тип операции или период.';
  $('empty-add').hidden = !first;
  $('pagination').hidden = !rows.length;
  $('page-description').textContent = `${start + 1}–${Math.min(start + PAGE_SIZE, rows.length)} из ${rows.length}`;
  $('previous').disabled = state.page <= 1;
  $('next').disabled = start + PAGE_SIZE >= rows.length;
}
function setView() {
  const view = location.hash === '#operations' ? 'operations' : 'overview';
  document.body.classList.toggle('operations-view', view === 'operations');
  for (const link of document.querySelectorAll('[data-view]')) {
    link.classList.toggle('active', link.dataset.view === view);
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  $('breadcrumb-view').textContent = view === 'overview' ? 'Обзор' : 'Операции';
  $('page-title').replaceChildren(document.createTextNode(view === 'overview' ? 'Деньги под контролем' : 'Каждая операция на виду'), element('span', '', '.'));
  $('page-subtitle').textContent = view === 'overview' ? 'Всё, что нужно знать о ваших финансах, — здесь.' : 'Добавляйте, редактируйте и находите нужные записи.';
  // The selected period remains visible in both views to explain filtered results.
  $('overview').classList.toggle('table-period', view === 'operations');
}
function openForm(row = null) {
  lastFocused = document.activeElement;
  state.editing = row;
  $('operation-form').reset();
  $('category-input').setCustomValidity('');
  $('form-error').hidden = true;
  $('dialog-title').textContent = row ? 'Редактировать операцию' : 'Новая операция';
  $('save-operation').textContent = row ? 'Сохранить изменения' : 'Добавить операцию';
  $('operation-form').elements.kind.value = row ? row.kind : 'expense';
  $('amount-input').value = row ? row.amount : '';
  $('category-input').value = row ? row.category : '';
  $('operation-dialog').querySelector('.form-hint').textContent = row ? 'Дата исходной операции останется прежней.' : 'Дата операции сохранится автоматически.';
  $('operation-dialog').showModal();
  $('amount-input').focus();
}
function closeForm() { if (!state.saving) $('operation-dialog').close(); }
$('operation-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.saving) return;
  const data = new FormData(event.currentTarget);
  const payload = { amount: data.get('amount'), category: data.get('category').trim(), kind: data.get('kind') };
  if (!payload.category) { $('category-input').setCustomValidity('Введите название категории'); $('category-input').reportValidity(); return; }
  state.saving = true;
  const editing = state.editing;
  const button = $('save-operation');
  const original = button.textContent;
  button.disabled = true; button.textContent = 'Сохраняем…';
  $('form-error').hidden = true;
  for (const input of $('operation-form').querySelectorAll('input')) input.disabled = true;
  try {
    const row = await api(editing ? '/operations/' + editing.id : '/operations', { method: editing ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    // A successful mutation is reflected immediately, even if the next GET fails.
    state.operations = [row, ...state.operations.filter((r) => r.id !== row.id)].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
    state.page = 1; render();
    $('operation-dialog').close();
    notify(editing ? 'Изменения сохранены' : 'Операция добавлена');
    await load();
  } catch (error) { $('form-error').textContent = error.message; $('form-error').hidden = false; }
  finally {
    state.saving = false; button.disabled = false; button.textContent = original;
    for (const input of $('operation-form').querySelectorAll('input')) input.disabled = false;
  }
});
$('category-input').addEventListener('input', () => $('category-input').setCustomValidity(''));
function openDelete(row) {
  state.deleting = row; lastFocused = document.activeElement;
  $('delete-description').textContent = `${row.category} · ${money(cents(row))}. Запись будет удалена без возможности отмены.`;
  $('delete-error').hidden = true;
  $('delete-dialog').showModal();
}
$('confirm-delete').addEventListener('click', async () => {
  if (state.deletingBusy) return;
  state.deletingBusy = true; $('confirm-delete').disabled = true;
  $('delete-error').hidden = true;
  try {
    const id = state.deleting.id;
    await api('/operations/' + id, { method: 'DELETE' });
    state.operations = state.operations.filter((r) => r.id !== id);
    render(); $('delete-dialog').close(); notify('Операция удалена');
    await load();
  } catch (error) { $('delete-error').textContent = error.message; $('delete-error').hidden = false; }
  finally { state.deletingBusy = false; $('confirm-delete').disabled = false; }
});
$('cancel-delete').addEventListener('click', () => { if (!state.deletingBusy) $('delete-dialog').close(); });
$('delete-dialog').addEventListener('cancel', (e) => { if (state.deletingBusy) e.preventDefault(); });
$('operation-dialog').addEventListener('cancel', (e) => { if (state.saving) e.preventDefault(); });
for (const dialog of [$('operation-dialog'), $('delete-dialog')]) dialog.addEventListener('close', () => {
  const target = lastFocused && lastFocused.isConnected ? lastFocused : $('add-operation');
  target.focus();
});
$('add-operation').addEventListener('click', () => openForm());
$('empty-add').addEventListener('click', () => openForm());
$('close-dialog').addEventListener('click', closeForm);
$('cancel-dialog').addEventListener('click', closeForm);
$('refresh').addEventListener('click', load);
$('retry').addEventListener('click', load);
for (const id of ['period', 'kind-filter', 'search']) $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { state.page = 1; if (state.loaded) render(); });
$('previous').addEventListener('click', () => { state.page--; renderTable(); });
$('next').addEventListener('click', () => { state.page++; renderTable(); });
window.addEventListener('hashchange', setView);
$('today').textContent = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(new Date());
$('year').textContent = new Date().getFullYear();
setView();
load();
