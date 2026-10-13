/* Shared theme controller; runs in the head to avoid a flash of the wrong theme. */
(() => {
  'use strict';
  const key = 'odoobi.desktop.theme';
  const choices = ['system', 'light', 'dark', 'ocean'];
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  const valid = value => choices.includes(value) ? value : 'system';
  try { preference = valid(localStorage.getItem(key)); } catch (_) {}
  function apply() {
    const resolved = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    const root = document.documentElement;
    root.dataset.theme = resolved;
    root.dataset.bsTheme = resolved === 'dark' ? 'dark' : 'light';
    root.style.colorScheme = resolved === 'dark' ? 'dark' : 'light';
    document.querySelectorAll('[data-theme-picker]').forEach(select => { select.value = preference; });
    const status = document.querySelector('[data-theme-status]');
    if (status) status.textContent = 'Tema aktif: ' + ({light: 'Terang', dark: 'Gelap', ocean: 'Biru Laut'}[resolved]);
  }
  apply();
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { preference = valid(event.newValue); apply(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-picker]').forEach(select => {
      select.addEventListener('change', () => {
        preference = valid(select.value);
        try { localStorage.setItem(key, preference); } catch (_) {}
        apply();
      });
    });
  });
})();
