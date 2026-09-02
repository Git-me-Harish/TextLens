/**
 * domainIcons — lucide icon per pipeline domain, shared between the sidebar
 * nav (AppLayout.jsx) and the domain picker (PipelinesPage.jsx).
 *
 * The domain picker used to render every domain as the same shape — a
 * colored square with a plain dot inside — differing only by color. That's
 * not really an icon, and it's inconsistent with literally everywhere else
 * in the app, which resolves a real lucide icon per domain (see
 * AppLayout.jsx's sidebar links, actionIcons.jsx's action catalog). This
 * reuses the exact icons the sidebar already uses, so the domain a user
 * picks here is drawn with the same glyph they clicked in the sidebar to
 * get here.
 */

import {
  Building2, Cpu, FileText, GraduationCap, HeartPulse,
  Scale, TrendingUp, Truck, Briefcase,
} from "lucide-react";

export const DOMAIN_ICONS = {
  finance:    TrendingUp,
  healthcare: HeartPulse,
  legal:      Scale,
  logistics:  Truck,
  hr:         Briefcase,
  education:  GraduationCap,
  government: Building2,
  // No sidebar entry for "general" — it's the auto-detect fallback, not a
  // domain a user picks deliberately — so it gets its own sensible default
  // rather than borrowing another domain's icon.
  general:    FileText,
};

export const DEFAULT_DOMAIN_ICON = Cpu;

export function DomainIcon({ domain, ...props }) {
  const Icon = DOMAIN_ICONS[domain] || DEFAULT_DOMAIN_ICON;
  return <Icon {...props} />;
}
