/**
 * actionIcons — shared lucide-react icon lookups for the agentic action layer.
 *
 * The rest of the app renders icons via named lucide-react imports (see
 * AppLayout.jsx, PipelinesPage.jsx, HistoryPage.jsx, etc.) — never emoji.
 * The action catalog (available_actions.icon) stores a lucide icon *name*
 * string per action, kept data-driven so ops can change an action's icon
 * via a migration without a frontend deploy. CatalogIcon renders that name,
 * falling back to a safe default if the name is unrecognised.
 */

import {
  AlertTriangle,
  Ban,
  BarChart3,
  Bot,
  BookOpen,
  Brain,
  Briefcase,
  Calendar,
  CalendarClock,
  Check,
  CheckCircle2,
  CheckSquare,
  ClipboardCheck,
  ClipboardList,
  Clock,
  Eye,
  FileText,
  Inbox,
  Link2,
  Mail,
  MessageCircle,
  Percent,
  Pill,
  Scale,
  Search,
  Send,
  Sparkles,
  Target,
  Wallet,
  X,
  XCircle,
  Zap,
} from "lucide-react";

// Name → component, used to resolve the `icon` string returned by the
// /actions/agent-run/{id}/available and /credentials/services endpoints.
export const ICONS = {
  AlertTriangle,
  Ban,
  BarChart3,
  Bot,
  BookOpen,
  Brain,
  Briefcase,
  Calendar,
  CalendarClock,
  Check,
  CheckCircle2,
  CheckSquare,
  ClipboardCheck,
  ClipboardList,
  Clock,
  Eye,
  FileText,
  Inbox,
  Link2,
  Mail,
  MessageCircle,
  Percent,
  Pill,
  Scale,
  Search,
  Send,
  Sparkles,
  Target,
  Wallet,
  X,
  XCircle,
  Zap,
};

export const DEFAULT_ACTION_ICON = "Zap";
export const DEFAULT_SERVICE_ICON = "Link2";

/** Resolve an icon name (from the DB catalog) to its component, with a safe fallback. */
export function resolveIcon(name, fallback = DEFAULT_ACTION_ICON) {
  return ICONS[name] || ICONS[fallback] || Zap;
}

/** Render a catalog-driven icon by name. Any unrecognised name falls back gracefully. */
export function CatalogIcon({ name, fallback = DEFAULT_ACTION_ICON, ...props }) {
  const Icon = resolveIcon(name, fallback);
  return <Icon {...props} />;
}

// Action-run lifecycle status → icon (used by ActionRunner and ActionHistoryPage)
export const STATUS_ICONS = {
  PENDING: Clock,
  PLANNING: Brain,
  AWAITING_APPROVAL: Eye,
  EXECUTING: Zap,
  COMPLETED: CheckCircle2,
  FAILED: XCircle,
  REJECTED: Ban,
  CANCELLED: Ban,
};

// MCP service → icon (used by CredentialsSettingsPage as a fallback when the
// backend hasn't been given an explicit icon for a newly added service)
export const SERVICE_ICONS = {
  google_calendar: Calendar,
  pharmacy_api: Pill,
  job_board_api: Briefcase,
  accounting_api: BarChart3,
  email_api: Mail,
};
