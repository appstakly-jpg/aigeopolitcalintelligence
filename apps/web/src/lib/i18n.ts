// UI chrome strings in English and Arabic. Content (incident titles, summaries) is shown in the
// language it was reported in, with the stored English translation where one exists.
export type Locale = "en" | "ar";

const en = {
  "nav.overview": "Overview",
  "nav.feed": "Live Feed",
  "nav.countries": "Countries",
  "nav.map": "Map",
  "nav.incidents": "Incidents",
  "nav.risk": "Risk Analysis",
  "nav.maritime": "Maritime & Energy",
  "nav.reports": "Intelligence Reports",
  "nav.alerts": "Alerts",
  "nav.sources": "Sources",
  "nav.analyst": "AI Analyst",
  "nav.admin": "Administration",
  "nav.settings": "Settings",
  "nav.reviews": "Review queue",
  "top.search": "Search incidents, reports, sources…",
  "top.range": "Date range",
  "top.signout": "Sign out",
  "top.theme": "Theme",
  "top.language": "العربية",
  "common.lastUpdated": "Last updated",
  "common.evidence": "Evidence",
  "common.severity": "Severity",
  "common.verification": "Verification",
  "common.reliability": "Source reliability",
  "common.country": "Country",
  "common.category": "Category",
  "common.none": "Nothing to show for this filter and date range.",
  "common.viewAll": "View all",
  "demo.banner": "DEMO MODE: every record shown in this organisation is synthetic test data, not real news, incidents or sources.",
  "login.title": "Sign in",
  "login.email": "Email",
  "login.password": "Password",
  "login.code": "Authentication code",
  "login.submit": "Sign in",
  "login.verify": "Verify",
  "overview.title": "GCC security overview",
  "overview.incidents": "Incidents in range",
  "overview.highSeverity": "Severity 4–5",
  "overview.pendingReview": "Awaiting analyst review",
  "overview.disputed": "Disputed or single-source",
  "overview.alerts": "Alerts awaiting review",
  "overview.sources": "Healthy sources",
} as const;

export type MessageKey = keyof typeof en;

const ar: Partial<Record<MessageKey, string>> = {
  "nav.overview": "نظرة عامة",
  "nav.feed": "التغذية المباشرة",
  "nav.countries": "الدول",
  "nav.map": "الخريطة",
  "nav.incidents": "الحوادث",
  "nav.risk": "تحليل المخاطر",
  "nav.maritime": "الملاحة والطاقة",
  "nav.reports": "التقارير الاستخباراتية",
  "nav.alerts": "التنبيهات",
  "nav.sources": "المصادر",
  "nav.analyst": "المحلل الذكي",
  "nav.admin": "الإدارة",
  "nav.settings": "الإعدادات",
  "nav.reviews": "قائمة المراجعة",
  "top.search": "ابحث في الحوادث والتقارير والمصادر…",
  "top.range": "النطاق الزمني",
  "top.signout": "تسجيل الخروج",
  "top.theme": "المظهر",
  "top.language": "English",
  "common.lastUpdated": "آخر تحديث",
  "common.evidence": "الأدلة",
  "common.severity": "الخطورة",
  "common.verification": "التحقق",
  "common.reliability": "موثوقية المصدر",
  "common.country": "الدولة",
  "common.category": "الفئة",
  "common.none": "لا توجد نتائج لهذا المرشح والنطاق الزمني.",
  "common.viewAll": "عرض الكل",
  "demo.banner": "وضع العرض التجريبي: جميع السجلات في هذه المنظمة بيانات اختبار اصطناعية، وليست أخباراً أو حوادث أو مصادر حقيقية.",
  "login.title": "تسجيل الدخول",
  "login.email": "البريد الإلكتروني",
  "login.password": "كلمة المرور",
  "login.code": "رمز التحقق",
  "login.submit": "دخول",
  "login.verify": "تحقق",
  "overview.title": "نظرة عامة على أمن الخليج",
  "overview.incidents": "الحوادث في النطاق",
  "overview.highSeverity": "خطورة ٤–٥",
  "overview.pendingReview": "بانتظار مراجعة المحلل",
  "overview.disputed": "متنازع عليها أو من مصدر واحد",
  "overview.alerts": "تنبيهات بانتظار المراجعة",
  "overview.sources": "مصادر سليمة",
};

export function t(locale: Locale, key: MessageKey): string {
  return (locale === "ar" ? ar[key] : undefined) ?? en[key];
}

export function dir(locale: Locale): "rtl" | "ltr" {
  return locale === "ar" ? "rtl" : "ltr";
}

export const GCC = ["SA", "AE", "QA", "KW", "BH", "OM"] as const;
export const CONTEXT_COUNTRIES = ["IR", "IQ", "YE", "IL"] as const;

export const COUNTRY_NAMES: Record<string, { en: string; ar: string }> = {
  SA: { en: "Saudi Arabia", ar: "السعودية" },
  AE: { en: "United Arab Emirates", ar: "الإمارات" },
  QA: { en: "Qatar", ar: "قطر" },
  KW: { en: "Kuwait", ar: "الكويت" },
  BH: { en: "Bahrain", ar: "البحرين" },
  OM: { en: "Oman", ar: "عُمان" },
  IR: { en: "Iran", ar: "إيران" },
  IQ: { en: "Iraq", ar: "العراق" },
  YE: { en: "Yemen", ar: "اليمن" },
  IL: { en: "Israel", ar: "إسرائيل" },
};

export function countryName(code: string | null | undefined, locale: Locale = "en"): string {
  if (!code) return "Regional / unattributed";
  const c = COUNTRY_NAMES[code.trim().toUpperCase()];
  return c ? c[locale] : code;
}
