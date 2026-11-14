import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Card, CardBody } from "@/components/ui/primitives";
import { getSession } from "@/lib/auth";
import { getEnv } from "@/lib/env";
import { t, type Locale } from "@/lib/i18n";
import { LoginForm } from "./LoginForm";

export const metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (await getSession()) redirect("/");
  const sp = await searchParams;
  const locale: Locale = (await cookies()).get("gi_locale")?.value === "ar" ? "ar" : "en";
  const env = getEnv();
  const labels = {
    email: t(locale, "login.email"), password: t(locale, "login.password"), code: t(locale, "login.code"),
    submit: t(locale, "login.submit"), verify: t(locale, "login.verify"),
  };
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="text-xs font-semibold tracking-widest text-primary uppercase">GCC Security Intelligence</div>
          <h1 className="mt-2 text-2xl font-semibold">{t(locale, "login.title")}</h1>
          <p className="mt-1 text-xs text-muted-foreground">Unclassified, public-source monitoring. Not a surveillance or targeting system.</p>
        </div>
        <Card>
          <CardBody>
            {env.authProvider === "supabase" ? (
              <p className="text-sm text-muted-foreground">
                This deployment uses Supabase Auth. Sign in through your organisation&apos;s Supabase login page; the app reads the
                verified <code>sb-access-token</code> session.
              </p>
            ) : (
              <LoginForm step={sp.step === "mfa" ? "mfa" : "password"} labels={labels} />
            )}
          </CardBody>
        </Card>
        {env.demoMode ? (
          <p className="mt-4 text-center text-xs text-muted-foreground">
            Demo mode is on. Demo accounts (synthetic data only) are created by <code>gccintel demo-setup</code>.
          </p>
        ) : null}
      </div>
    </main>
  );
}
