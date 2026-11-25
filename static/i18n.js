// i18n — lightweight translation loader for Diabet Logger
//
// Load this script SYNCHRONOUSLY (no `defer`/`async`) before any page code that calls t().
// Page init code should `await i18nReady()` before it renders anything, so text is never
// generated while the dictionary is still empty. After a language switch a `langchange`
// event is dispatched on `document`; pages listen to it to re-render dynamic text.
// Directory this script was loaded from, so it also works when the page is opened from disk.
const _base = (document.currentScript && document.currentScript.src)
  ? document.currentScript.src.replace(/[^/]*$/, '') : '/static/';
let _lang = localStorage.getItem('diabet_lang') || navigator.language.slice(0, 2) || 'bg';
let _strings = {};
let _i18nReady = null;
const _supported = {
  bg: '🇧🇬 Български',
  en: '🇬🇧 English',
  de: '🇩🇪 Deutsch',
  es: '🇪🇸 Español',
  fr: '🇫🇷 Français',
  'pt-PT': '🇵🇹 Português',
  'pt-BR': '🇧🇷 Português (Brasil)',
  it: '🇮🇹 Italiano',
  nl: '🇳🇱 Nederlands',
  pl: '🇵🇱 Polski'
};

const _domReady = document.readyState !== 'loading'
  ? Promise.resolve()
  : new Promise(res => document.addEventListener('DOMContentLoaded', res, { once: true }));

// file:// (demo mode): fetch() of local JSON is blocked, so load lang/<lang>.js instead.
// Only bg.js is shipped (see gen_lang_fallback.py); other languages fall back to it.
function _loadScriptDict(lang) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = _base + 'lang/' + lang + '.js';
    s.onload = () => (window.__I18N && window.__I18N[lang]) ? resolve(window.__I18N[lang]) : reject(new Error('empty'));
    s.onerror = () => reject(new Error('missing ' + lang + '.js'));
    document.head.appendChild(s);
  });
}

async function _fetchStrings(lang) {
  if (location.protocol === 'file:') return _loadScriptDict(lang);
  const res = await fetch(_base + 'lang/' + lang + '.json?v=' + Date.now());
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

async function loadLang(lang) {
  if (!(lang in _supported)) lang = 'bg';
  let strings;
  try {
    strings = await _fetchStrings(lang);
  } catch (e) {
    console.warn('[i18n] failed to load ' + lang + ':', e);
    if (lang === 'bg') return;           // nothing left to fall back to; t() returns keys
    return loadLang('bg');
  }
  _strings = strings;
  _lang = lang;
  try { localStorage.setItem('diabet_lang', lang); } catch (e) {}
  await _domReady;                       // never apply before the markup exists
  applyTranslations();
  document.documentElement.lang = lang;
  const sel = document.getElementById('lang-select');
  if (sel) sel.value = lang;
  document.dispatchEvent(new CustomEvent('langchange', { detail: { lang } }));
}

// Resolves once the first dictionary is loaded and applied. Safe to call many times.
function i18nReady() {
  if (!_i18nReady) _i18nReady = loadLang(_lang);
  return _i18nReady;
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
    const val = _strings[el.getAttribute('data-i18n')];
    if (val) el.textContent = val;
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    const val = _strings[el.getAttribute('data-i18n-html')];
    if (val) el.innerHTML = val;
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const val = _strings[el.getAttribute('data-i18n-placeholder')];
    if (val) el.placeholder = val;
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const val = _strings[el.getAttribute('data-i18n-title')];
    if (val) el.title = val;
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    const val = _strings[el.getAttribute('data-i18n-aria')];
    if (val) el.setAttribute('aria-label', val);
  });
}

function setLang(lang) { _i18nReady = loadLang(lang); return _i18nReady; }

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

_domReady.then(buildLangSelector);
i18nReady();   // start fetching the dictionary immediately
