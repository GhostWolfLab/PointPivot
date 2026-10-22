/**
 * Shared JS utilities for all pages
 */

// ─── API helper ────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch('/api' + path, opts);
  if (resp.status === 401) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = '/login?next=' + next;
    throw new Error('未登录或登录已失效');
  }
  if (!resp.ok) {
    let detail = '';
    try { const j = await resp.json(); detail = j.detail || JSON.stringify(j); } catch { detail = resp.statusText; }
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

// ─── Toast notifications ───────────────────────────
function showToast(message, type = 'success') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    document.body.appendChild(container);
  }
  const bgClass = type === 'error' ? 'text-bg-danger' : type === 'warning' ? 'text-bg-warning' : 'text-bg-success';
  const html = `<div class="toast align-items-center ${bgClass} border-0" role="alert">
    <div class="d-flex"><div class="toast-body">${escapeHtml(message)}</div>
    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div></div>`;
  container.insertAdjacentHTML('beforeend', html);
  const el = container.lastElementChild;
  const toast = new bootstrap.Toast(el, { delay: 4000 });
  toast.show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ─── Format helpers ────────────────────────────────
function fmtTime(ts) {
  if (!ts) return '-';
  return new Date(ts).toLocaleString('zh-CN');
}

function actionBadgeClass(action) {
  const map = { long: 'badge-action-long', short: 'badge-action-short', close_long: 'badge-action-close_long', close_short: 'badge-action-close_short', hold: 'badge-action-hold' };
  return 'badge ' + (map[action] || 'bg-light text-dark');
}

function actionLabel(action) {
  const map = { long: '做多', short: '做空', close_long: '平多', close_short: '平空', hold: '持有' };
  return map[action] || action || '-';
}

function pnlClass(val) {
  if (val > 0) return 'text-success';
  if (val < 0) return 'text-danger';
  return '';
}

function pnlText(val) {
  if (val == null) return '-';
  return (val >= 0 ? '+' : '') + val.toFixed(4);
}

// ─── Loading overlay ───────────────────────────────
function setLoading(el, loading) {
  if (loading) {
    el.dataset.origText = el.innerHTML;
    el.disabled = true;
    el.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>处理中...';
  } else {
    el.disabled = false;
    el.innerHTML = el.dataset.origText || el.innerHTML;
  }
}
