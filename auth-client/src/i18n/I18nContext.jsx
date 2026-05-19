import { createContext, useContext, useState, useCallback } from 'react';
import { translations } from './translations';

const I18nContext = createContext();

const STORAGE_KEY = 'synthcrypto_lang';

export const LANGUAGES = [
  { code: 'en', label: 'English', flag: 'EN' },
  { code: 'hi', label: 'हिन्दी', flag: 'HI' },
  { code: 'es', label: 'Español', flag: 'ES' },
  { code: 'fr', label: 'Français', flag: 'FR' },
  { code: 'de', label: 'Deutsch', flag: 'DE' },
  { code: 'zh', label: '中文', flag: 'ZH' },
  { code: 'ja', label: '日本語', flag: 'JA' },
  { code: 'ar', label: 'العربية', flag: 'AR' },
];

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) || 'en'; } catch { return 'en'; }
  });

  const setLang = useCallback((code) => {
    setLangState(code);
    try { localStorage.setItem(STORAGE_KEY, code); } catch {}
  }, []);

  const t = useCallback((key) => {
    const val = translations[lang]?.[key];
    if (val !== undefined) return val;
    return translations['en']?.[key] ?? key;
  }, [lang]);

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) return { lang: 'en', setLang: () => {}, t: (k) => k };
  return ctx;
}
