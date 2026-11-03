// Minimal i18n layer for the static frontend (plan §5.1).
//
// Usage:
//   import { setLocale, getLocale, t } from "./i18n.js";
//   await setLocale("en-US");
//   el.textContent = t("nav.ideas");
//   el.textContent = t("work.count", { count: 3 });
//
// Lookup chain: active locale -> ko-KR default -> key itself.
// A total miss logs console.error and returns the key, so missing
// translations are visible in dev but never blank the UI in prod.
//
// Plural approach (minimal, no library): dictionaries carry plain
// strings with {count} substitution only. When a language needs
// distinct plural forms, authors add explicit keys (e.g. "item.one"
// vs "item.other") and the caller picks by count; see
// docs/i18n-contracts.md. If ICU MessageFormat is ever needed, this
// module is the single place to add it.

const DEFAULT_LOCALE = "ko-KR";
const LOCALES_PATH = "/locales";
const FRAGMENTS_PATH = "/locales/_frag";

let currentLocale = DEFAULT_LOCALE;
// Active-locale strings and default-locale fallback strings.
let active = {};
let fallback = {};
let loaded = {};
let manifestOwners = null;

/**
 * Fetch the fragment manifest (owner list, merge order).
 * Cached after the first successful load.
 */
async function loadManifest() {
  if (!manifestOwners) {
    const resp = await fetch(`${FRAGMENTS_PATH}/manifest.json`);
    if (!resp.ok) {
      throw new Error(`i18n manifest unavailable: HTTP ${resp.status}`);
    }
    const manifest = await resp.json();
    if (!manifest || !Array.isArray(manifest.fragments)) {
      throw new Error("i18n manifest has no fragments array");
    }
    manifestOwners = manifest.fragments;
  }
  return manifestOwners;
}

/**
 * Fetch one locale dictionary: base file plus every manifest-listed
 * owner fragment merged in order (docs/i18n-contracts.md §5).
 * Throws when the base file or manifest is unavailable; a single
 * unreadable fragment is logged and skipped so one owner cannot
 * blank the whole UI. Duplicate keys are logged and keep the
 * first value (base wins over fragments); duplicates are a bug
 * that the test suite rejects.
 */
export async function loadLocale(locale) {
  const resp = await fetch(`${LOCALES_PATH}/${locale}.json`);
  if (!resp.ok) {
    throw new Error(`locale ${locale} unavailable: HTTP ${resp.status}`);
  }
  const dict = await resp.json();
  for (const owner of await loadManifest()) {
    let fragResp;
    try {
      fragResp = await fetch(`${FRAGMENTS_PATH}/${owner}.${locale}.json`);
    } catch (e) {
      console.error(`locale ${locale}: fragment ${owner} fetch failed`, e);
      continue;
    }
    if (!fragResp.ok) {
      console.error(
        `locale ${locale}: fragment ${owner} unavailable: HTTP ${fragResp.status}`,
      );
      continue;
    }
    for (const [key, value] of Object.entries(await fragResp.json())) {
      if (Object.prototype.hasOwnProperty.call(dict, key)) {
        console.error(
          `locale ${locale}: duplicate key ${key} (fragment ${owner})`,
        );
        continue;
      }
      dict[key] = value;
    }
  }
  loaded[locale] = dict;
  return dict;
}

/**
 * Switch the active UI locale. Always keeps the ko-KR fallback loaded
 * (unless ko-KR itself fails, in which case the error propagates).
 * Persists the choice to localStorage ("ui_locale", plan §4.2).
 */
export async function setLocale(locale) {
  const dict = await loadLocale(locale);
  active = dict;
  currentLocale = locale;
  if (locale === DEFAULT_LOCALE) {
    fallback = dict;
  } else {
    try {
      fallback = loaded[DEFAULT_LOCALE] ?? await loadLocale(DEFAULT_LOCALE);
    } catch (e) {
      console.error(`default locale ${DEFAULT_LOCALE} unavailable`, e);
      fallback = {};
    }
  }
  try {
    localStorage.setItem("ui_locale", locale);
  } catch {
    /* private mode etc: locale still applies for this session */
  }
  document.dispatchEvent(
    new CustomEvent("locale-changed", { detail: { locale } }),
  );
  return currentLocale;
}

export function getLocale() {
  return currentLocale;
}

/** Substitute {name} placeholders with params values. */
export function format(template, params = {}) {
  return String(template).replace(/\{(\w+)\}/g, (m, name) =>
    Object.prototype.hasOwnProperty.call(params, name)
      ? String(params[name])
      : m,
  );
}

/**
 * Translate key with optional {name} params.
 * Falls back to ko-KR, then to the key itself + console.error.
 */
export function t(key, params = {}) {
  let template;
  if (Object.prototype.hasOwnProperty.call(active, key)) {
    template = active[key];
  } else if (Object.prototype.hasOwnProperty.call(fallback, key)) {
    template = fallback[key];
  } else {
    console.error(`missing i18n key: ${key} (locale ${currentLocale})`);
    return format(key, params);
  }
  return format(template, params);
}

/** Stored UI locale choice, or "" when none was saved. */
export function storedLocale() {
  try {
    return localStorage.getItem("ui_locale") || "";
  } catch {
    return "";
  }
}
