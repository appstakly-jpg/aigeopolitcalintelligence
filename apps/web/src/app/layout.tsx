import type { Metadata } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { getSession } from "@/lib/auth";
import { dir, type Locale } from "@/lib/i18n";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "GCC Security Intelligence", template: "%s · GCC Security Intelligence" },
  description: "Public-source geopolitical and regional security monitoring for the GCC states.",
  robots: { index: false, follow: false },
};

// Applies the "system" theme before paint to avoid a flash of the wrong theme.
const themeScript = `try{var c=document.cookie.match(/(?:^|; )gi_theme=([^;]+)/);var t=c?c[1]:"system";if(t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches)){document.documentElement.classList.add("dark")}}catch(e){}`;

export default async function RootLayout({ children }: { children: ReactNode }) {
  const jar = await cookies();
  const session = await getSession().catch(() => null);
  const locale: Locale = session?.locale ?? (jar.get("gi_locale")?.value === "ar" ? "ar" : "en");
  const theme = jar.get("gi_theme")?.value ?? "system";
  return (
    <html lang={locale} dir={dir(locale)} className={theme === "dark" ? "dark" : undefined} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
