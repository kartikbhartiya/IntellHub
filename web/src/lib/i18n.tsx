"use client";

/**
 * Bilingual UI — English and Kannada.
 *
 * Karnataka Police operate in Kannada. A platform for the SCRB that is English-only
 * is not "mostly translated", it is unusable for a share of the people expected to
 * work in it for hours a day, and government digital services here are expected to
 * be bilingual.
 *
 * Before this, "bilingual" meant one thing: the voice console passed a `lang` hint
 * to the LLM so the *answer* came back in Kannada. Every page, heading, table column,
 * filter and empty state was English, `<html lang>` was hardcoded to "en", and the
 * webfonts loaded `subsets: ["latin"]` only — so even Kannada text from the model
 * fell back to whatever the OS had.
 *
 * A NOTE ON THE TRANSLATIONS
 * --------------------------
 * The Kannada below covers navigation and common UI chrome, where the vocabulary is
 * ordinary. It has NOT been reviewed by a native speaker, and police terminology has
 * conventions that general translation gets wrong — the difference between a
 * "chargesheet" and a "final report", or which word a Kannada-speaking officer
 * actually uses for "pendency". Shipping machine-translated legal vocabulary into a
 * police tool and calling it localised would be the same overclaim this codebase has
 * spent its effort removing.
 *
 * So: a missing key falls back to English rather than to a placeholder, `missingKeys()`
 * reports what is uncovered, and the dictionary is deliberately small and honest
 * rather than large and unverified. Domain vocabulary needs a Kannada-speaking officer
 * before it goes near a real deployment.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

export type Lang = "en" | "kn";

export const LANGUAGES: { code: Lang; label: string; native: string }[] = [
  { code: "en", label: "EN", native: "English" },
  { code: "kn", label: "ಕ", native: "ಕನ್ನಡ" },
];

const STORAGE_KEY = "ksp.lang";

/**
 * Only keys whose Kannada is ordinary vocabulary are listed. Anything absent falls
 * back to English on purpose — see the note above.
 */
const KN: Record<string, string> = {
  // navigation
  "nav.overview": "ಅವಲೋಕನ",
  "nav.cases": "ಪ್ರಕರಣಗಳು",
  "nav.map": "ನಕ್ಷೆ",
  "nav.network": "ಜಾಲ",
  "nav.analytics": "ವಿಶ್ಲೇಷಣೆ",
  "nav.models": "ಮಾದರಿಗಳು",
  "nav.registry": "ನೋಂದಣಿ",
  "nav.officers": "ಅಧಿಕಾರಿಗಳು",
  "nav.lifecycle": "ಜೀವನಚಕ್ರ",
  "nav.pendency": "ಬಾಕಿ",
  "nav.dispatch": "ನಿಯೋಜನೆ",
  "nav.access": "ಪ್ರವೇಶ",
  "nav.cctv": "ಸಿಸಿಟಿವಿ",
  "nav.copilot": "ಸಹಾಯಕ",
  "nav.more": "ಇನ್ನಷ್ಟು",

  // common chrome
  "common.district": "ಜಿಲ್ಲೆ",
  "common.districts": "ಜಿಲ್ಲೆಗಳು",
  "common.status": "ಸ್ಥಿತಿ",
  "common.search": "ಹುಡುಕಿ",
  "common.filter": "ಶೋಧಕ",
  "common.all": "ಎಲ್ಲಾ",
  "common.open": "ತೆರೆದ",
  "common.closed": "ಮುಚ್ಚಿದ",
  "common.loading": "ಲೋಡ್ ಆಗುತ್ತಿದೆ…",
  "common.export": "ರಫ್ತು",
  "common.signIn": "ಸೈನ್ ಇನ್",
  "common.signOut": "ಸೈನ್ ಔಟ್",
  "common.date": "ದಿನಾಂಕ",
  "common.name": "ಹೆಸರು",
  "common.total": "ಒಟ್ಟು",
  "common.unavailable": "ಲಭ್ಯವಿಲ್ಲ",
  "common.skipToContent": "ಮುಖ್ಯ ವಿಷಯಕ್ಕೆ ಹೋಗಿ",

  // a11y / accessible names
  "a11y.primaryNav": "ಮುಖ್ಯ ಸಂಚಾರ",
  "a11y.sendQuery": "ಪ್ರಶ್ನೆ ಕಳುಹಿಸಿ",
  "a11y.voiceOn": "ಧ್ವನಿ ಆನ್ ಮಾಡಿ",
  "a11y.voiceOff": "ಧ್ವನಿ ಆಫ್ ಮಾಡಿ",
};

const DICTIONARIES: Record<Lang, Record<string, string>> = { en: {}, kn: KN };

/** English source strings. `t(key, fallback)` also accepts an inline fallback. */
const EN_DEFAULTS: Record<string, string> = {
  "nav.overview": "Overview",
  "nav.cases": "Cases",
  "nav.map": "Map",
  "nav.network": "Network",
  "nav.analytics": "Analytics",
  "nav.models": "Models",
  "nav.registry": "Registry",
  "nav.officers": "Officers",
  "nav.lifecycle": "Lifecycle",
  "nav.pendency": "Pendency",
  "nav.dispatch": "Dispatch",
  "nav.access": "Access",
  "nav.cctv": "CCTV",
  "nav.copilot": "Copilot",
  "nav.more": "More",
  "common.skipToContent": "Skip to main content",
  "a11y.primaryNav": "Primary",
};

const _missing = new Set<string>();

/** Keys requested at runtime that have no Kannada entry — the honest to-do list. */
export function missingKeys(): string[] {
  return [..._missing].sort();
}

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, fallback?: string) => string;
}

const I18nContext = createContext<I18nValue>({
  lang: "en",
  setLang: () => {},
  t: (key, fallback) => fallback ?? EN_DEFAULTS[key] ?? key,
});

const _listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  _listeners.add(cb);
  // A second tab changing the preference should not leave this one stale.
  window.addEventListener("storage", cb);
  return () => {
    _listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

function readStoredLang(): Lang {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "kn" || stored === "en" ? stored : "en";
  } catch {
    return "en";   // private mode — English is a fine default
  }
}

/** The prerendered HTML is built without a browser, so it is always English. */
function serverLang(): Lang {
  return "en";
}

export function LangProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, readStoredLang, serverLang);

  // Keep <html lang> honest. It was hardcoded to "en", so a screen reader announced
  // Kannada text with English phonemes — which is worse than not translating at all.
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* preference simply does not persist */
    }
    // The store is the source of truth; tell the subscribers to re-read it.
    _listeners.forEach((cb) => cb());
  }, []);

  const t = useCallback(
    (key: string, fallback?: string) => {
      const english = fallback ?? EN_DEFAULTS[key] ?? key;
      if (lang === "en") return english;
      const translated = DICTIONARIES[lang][key];
      if (!translated) {
        _missing.add(key);
        return english;      // fall back to English, never to a placeholder
      }
      return translated;
    },
    [lang]
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
