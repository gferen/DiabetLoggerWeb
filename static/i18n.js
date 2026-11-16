// i18n — lightweight translation loader for Diabet Logger
let _lang = localStorage.getItem('diabet_lang') || navigator.language.slice(0, 2) || 'bg';
let _strings = {};
const _supported = {
  bg: '🇧🇬 Български',
  en: '🇬🇧 English',
  de: '🇩🇪 Deutsch',
  es: '🇪🇸 Español',
  fr: '🇫🇷 Français',
  pt: '🇧🇷 Português',
  it: '🇮🇹 Italiano',
  nl: '🇳🇱 Nederlands',
  pl: '🇵🇱 Polski'
};

async function loadLang(lang) {
  if (!(lang in _supported)) lang = 'bg';
  try {
    const res = await fetch('/static/lang/' + lang + '.json?v=' + Date.now());
    if (!res.ok) throw new Error(res.statusText);
    _strings = await res.json();
    _lang = lang;
    localStorage.setItem('diabet_lang', lang);
    applyTranslations(); if (typeof render === 'function') render();
    document.documentElement.lang = lang;
    const sel = document.getElementById('lang-select');
    if (sel) sel.value = lang;
  } catch (e) {
    console.warn('[i18n] failed to load ' + lang + ':', e);
    if (lang !== 'bg') loadLang('bg');
  }
}

function t(key, params) {
  let s = _strings[key] || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), v);
    }
  }
  return s;
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = _strings[key];
    if (val) el.textContent = val;
  });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      const val = _strings[key];
      if (val) el.placeholder = val;
    });
      document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        const val = _strings[key];
        if (val) el.title = val;
      });
}

function setLang(lang) { loadLang(lang); }

function buildLangSelector() {
  const sel = document.getElementById('lang-select');
  if (!sel) return;
  sel.innerHTML = '';
  for (const [code, name] of Object.entries(_supported)) {
    const opt = document.createElement('option');
    opt.value = code;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  sel.value = _lang;
  sel.addEventListener('change', () => setLang(sel.value));
}

document.addEventListener('DOMContentLoaded', () => {
  buildLangSelector();
  loadLang(_lang);
});
