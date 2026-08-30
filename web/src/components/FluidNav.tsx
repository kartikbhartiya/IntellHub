"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useI18n, LANGUAGES } from "@/lib/i18n";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  Folders,
  Map as MapIcon,
  Network,
  Siren,
  Boxes,
  ShieldAlert,
  Brain,
  Users,
  Timer,
  GitBranch,
  Lock,
} from "lucide-react";

// `key` drives translation; `label` is the English source and the fallback.
const NAV: { href: string; label: string; key: string; icon: any }[] = [
  { href: "/", label: "Overview", key: "nav.overview", icon: LayoutDashboard },
  { href: "/cases", label: "Cases", key: "nav.cases", icon: Folders },
  { href: "/map", label: "Map", key: "nav.map", icon: MapIcon },
  { href: "/network", label: "Network", key: "nav.network", icon: Network },
  { href: "/analytics", label: "Analytics", key: "nav.analytics", icon: Brain },
  { href: "/intel", label: "Registry", key: "nav.registry", icon: Boxes },
  { href: "/officers", label: "Officers", key: "nav.officers", icon: Users },
  { href: "/lifecycle", label: "Lifecycle", key: "nav.lifecycle", icon: GitBranch },
  { href: "/pendency", label: "Pendency", key: "nav.pendency", icon: Timer },
  { href: "/dispatch", label: "Dispatch", key: "nav.dispatch", icon: Siren },
  { href: "/access", label: "Access", key: "nav.access", icon: Lock },
];

const spring = { type: "spring" as const, stiffness: 420, damping: 26 };

export default function FluidNav() {
  const { lang, setLang, t } = useI18n();
  const pathname = usePathname() || "/";
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    // The pill is wider than a phone: twelve destinations plus the crest. Centring it
    // made it overflow BOTH edges, which pushed the crest and the first two links off
    // the left of the screen with no way to reach them — verified at 375px, where the
    // nav's right edge sat at 562px.
    //
    // So on narrow screens it aligns left and scrolls inside its own container,
    // keeping the crest visible and every destination reachable; from md up, where it
    // fits, it centres as before. `overscroll-x-contain` stops a swipe along the nav
    // from turning into a page-level back gesture.
    <header
      id="fluid-nav"
      className="fixed inset-x-0 top-0 z-40 flex justify-start overflow-x-auto overflow-y-hidden overscroll-x-contain px-4 pb-3 pt-5 no-scrollbar md:justify-center md:overflow-visible"
    >
      <motion.nav
        aria-label={t("a11y.primaryNav", "Primary")}
        initial={{ y: -28, opacity: 0, scale: 0.96 }}
        animate={{ y: 0, opacity: 1, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 22, delay: 0.05 }}
        className="flex shrink-0 items-center gap-1 rounded-full border border-black/[0.07] bg-white/85 p-1.5 pl-2.5 pr-1.5 shadow-[0_18px_50px_-15px_rgba(17,20,32,0.16)] backdrop-blur-xl"
      >
        {/* Brand crest */}
        <Link prefetch={false} href="/" className="group flex items-center gap-2.5 pr-2.5">
          <motion.span
            whileHover={{ scale: 1.1, rotate: -6 }}
            whileTap={{ scale: 0.92 }}
            transition={spring}
            className="relative grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-brand-soft to-brand-deep shadow-[0_4px_16px_-3px_rgba(176,125,22,0.6)]"
          >
            <ShieldAlert className="h-[18px] w-[18px] text-black/85" strokeWidth={2.2} />
            <span className="pointer-events-none absolute inset-0 rounded-full ring-1 ring-inset ring-white/30" />
          </motion.span>
          <span className="hidden flex-col leading-none sm:flex">
            <span className="text-display text-[13px] font-bold tracking-wide text-ink">
              KSP IntelHub
            </span>
            <span className="text-[9px] font-medium uppercase tracking-[0.22em] text-brand/90">
              SCRB · Karnataka
            </span>
          </span>
        </Link>

        <div className="mx-1 h-6 w-px bg-black/10" />

        <ul className="flex items-center gap-0.5">
          {NAV.map(({ href, label, key, icon: Icon }, i) => {
            const active = isActive(href);
            return (
              <motion.li
                key={href}
                className="relative"
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...spring, delay: 0.12 + i * 0.035 }}
              >
                <motion.div whileHover={{ scale: 1.06, y: -1 }} whileTap={{ scale: 0.94 }} transition={spring}>
                  <Link prefetch={false}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    // 43x31 on a phone: comfortably past WCAG AA's 24x24 floor, but
                    // under the 44x44 target size the AAA guideline asks for, and
                    // these are icon-only at that width so there is no text to aim
                    // at either. Raised on touch widths only; the desktop pill keeps
                    // its proportions.
                    className={`group relative flex min-h-[44px] items-center justify-center gap-2 rounded-full px-3.5 py-2 text-[13px] font-medium transition-colors duration-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:min-h-0 ${
                      active ? "text-black" : "text-muted hover:text-ink"
                    }`}
                  >
                    {active && (
                      <motion.span
                        layoutId="nav-pill"
                        transition={{ type: "spring", stiffness: 480, damping: 30 }}
                        className="absolute inset-0 -z-10 rounded-full bg-gradient-to-b from-brand-soft to-brand shadow-[0_6px_20px_-5px_rgba(176,125,22,0.7)]"
                      />
                    )}
                    <motion.span
                      className="grid place-items-center"
                      whileHover={{ rotate: active ? 0 : 8 }}
                      transition={spring}
                    >
                      <Icon className="h-[15px] w-[15px]" strokeWidth={2} aria-hidden="true" />
                    </motion.span>
                    {/* The visible label is hidden below md, so carry an accessible
                        name that survives at every width. */}
                    <span className="hidden md:inline">{t(key, label)}</span>
                    <span className="sr-only md:hidden">{t(key, label)}</span>
                  </Link>
                </motion.div>
              </motion.li>
            );
          })}
        </ul>

        {/* Language switch. Karnataka Police work in Kannada; an English-only tool
            excludes the people expected to live in it. */}
        <div
          role="group"
          aria-label="Language"
          className="ml-1 flex items-center gap-0.5 rounded-full border border-black/[0.07] bg-black/[0.02] p-0.5"
        >
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              type="button"
              onClick={() => setLang(l.code)}
              aria-pressed={lang === l.code}
              aria-label={`Switch interface language to ${l.native}`}
              title={l.native}
              // 28px square on a phone, which is a real control at roughly the size
              // of a fingertip's error margin. Given a 44px target on touch widths
              // like the nav links; the desktop pill keeps the compact form.
              className={`h-11 min-w-11 rounded-full px-2 text-[11.5px] font-semibold transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand md:h-7 md:min-w-7 ${
                lang === l.code
                  ? "bg-brand text-black"
                  : "text-muted hover:text-ink"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </motion.nav>
    </header>
  );
}
