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
    const res = await fetch(`/static/lang/${lang}.json?v=${Date.now()}`);
    if (!res.ok) throw new Error(res.statusText);
    _strings = await res.json();
    _lang = lang;
    localStorage.setItem('diabet_lang', lang);
    applyTranslations();
    const sel = document.getElementById('lang-select');
    if (sel) sel.value = lang;
  } catch (e) {
    console.warn(`[i18n] failed to load ${lang}:`, e);
    if (lang !== 'bg') loadLang('bg');
  }
}

function t(key, fallback) {
  return _strings[key] || fallback || key;
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = t(key);
    if (val !== key) el.textContent = val;
  });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      const val = t(key);
      if (val !== key) el.placeholder = val;
    });
      document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        const val = t(key);
        if (val !== key) el.title = val;
      });
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
          const key = el.getAttribute('data-i18n-html');
          const val = t(key);
          if (val !== key) el.innerHTML = val;
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
