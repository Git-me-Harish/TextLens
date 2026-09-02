/**
 * ActionHistoryPage
 *
 * Full-page view of the user's action run history.
 * Route: /actions/history
 */

import { useState, useEffect, useCallback, Fragment } from "react";
import toast from "react-hot-toast";
import { formatDistanceToNow } from "date-fns";
import api from "../lib/api";
import { Badge, Spinner, Select } from "../components/ui";

const STATUS_BADGE = {
  PENDING: "default",
  PLANNING: "processing",
  AWAITING_APPROVAL: "warning",
  EXECUTING: "processing",
  COMPLETED: "success",
  FAILED: "danger",
  REJECTED: "default",
  CANCELLED: "default",
};

const DOMAIN_LABELS = {
  healthcare: "Healthcare", hr: "Career", finance: "Finance",
  legal: "Legal", government: "Government", education: "Education",
};

const PAGE_SIZE = 15;

// Expanded detail row — same pattern as HistoryPage.jsx's ExpandedRow
function ExpandedRow({ run, colSpan }) {
  return (
    <tr>
      <td colSpan={colSpan} style={{ padding: "0.75rem 1rem 1rem", background: run.error_message ? "var(--danger-light)" : "var(--paper)" }}>
        {run.error_message ? (
          <div style={{ fontSize: "0.82rem", color: "var(--danger)" }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Error</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.78rem", opacity: 0.9 }}>{run.error_message}</div>
          </div>
        ) : run.action_result ? (
          <div style={{ fontSize: "0.82rem", color: "var(--ink-secondary)" }}>
            <div style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--ink-muted)", marginBottom: 6 }}>
              Result
            </div>
            <p style={{ lineHeight: 1.5 }}>{run.action_result.summary}</p>
            {run.action_result.next_steps?.length > 0 && (
              <ul style={{ marginTop: 8, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 3 }}>
                {run.action_result.next_steps.map((s, i) => (
                  <li key={i} style={{ fontSize: "0.8rem", color: "var(--ink-muted)" }}>{s}</li>
                ))}
              </ul>
            )}
            <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: "0.75rem", color: "var(--ink-muted)" }}>
              <span>Run ID: <code className="font-mono">{run.id.slice(0, 8)}…</code></span>
              {run.completed_at && <span>Completed {new Date(run.completed_at).toLocaleTimeString()}</span>}
            </div>
          </div>
        ) : (
          <p style={{ fontSize: "0.82rem", color: "var(--ink-muted)" }}>No result yet.</p>
        )}
      </td>
    </tr>
  );
}

export default function ActionHistoryPage() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [filters, setFilters] = useState({ domain: "", status: "" });

  const fetchRuns = useCallback(
    async (newOffset = 0, append = false) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          limit: PAGE_SIZE,
          offset: newOffset,
          ...(filters.domain && { domain: filters.domain }),
          ...(filters.status && { status: filters.status }),
        });
        const { data } = await api.get(`/actions/?${params}`);
        setRuns((prev) => (append ? [...prev, ...data] : data));
        setHasMore(data.length === PAGE_SIZE);
        setOffset(newOffset);
      } catch {
        toast.error("Failed to load action history.");
      } finally {
        setLoading(false);
      }
    },
    [filters]
  );

  useEffect(() => { fetchRuns(0, false); }, [fetchRuns]);

  const handleFilterChange = (key, val) => {
    setFilters((prev) => ({ ...prev, [key]: val }));
  };

  const toggleExpand = (id) => setExpandedId((prev) => (prev === id ? null : id));
  const COL = 6;

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Action History</h1>
        <p className="page-subtitle">All agentic actions performed on your documents</p>
      </div>

      {/* Filters */}
      <div style={{ display: "flex", gap: 10, marginBottom: "1.25rem", flexWrap: "wrap" }}>
        <Select
          minWidth={170}
          aria-label="Filter by domain"
          value={filters.domain}
          onChange={(e) => handleFilterChange("domain", e.target.value)}
          options={[
            { value: "", label: "All domains" },
            ...Object.entries(DOMAIN_LABELS).map(([value, label]) => ({ value, label })),
          ]}
        />
        <Select
          minWidth={190}
          aria-label="Filter by status"
          value={filters.status}
          onChange={(e) => handleFilterChange("status", e.target.value)}
          options={[
            { value: "", label: "All statuses" },
            // Underscores read as machine output in a menu the user reads.
            ...Object.keys(STATUS_BADGE).map((s) => ({
              value: s,
              label: s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " "),
            })),
          ]}
        />
        {(filters.domain || filters.status) && (
          <button className="btn btn-ghost btn-sm" onClick={() => setFilters({ domain: "", status: "" })}>
            Clear filters
          </button>
        )}
        <button className="btn btn-ghost btn-sm" onClick={() => fetchRuns(0, false)}>
          Refresh
        </button>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        {loading && runs.length === 0 ? (
          <div style={{ padding: "3rem", textAlign: "center" }}><Spinner /></div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-title">No action runs found</div>
            <div className="empty-state-desc">Start an action from any document result page</div>
          </div>
        ) : (
          <table className="jobs-table">
            <thead>
              <tr>
                <th>Action</th>
                <th>Domain</th>
                <th>Status</th>
                <th>Tool calls</th>
                <th>Created</th>
                <th style={{ width: 40 }}></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <Fragment key={run.id}>
                  <tr
                    onClick={() => toggleExpand(run.id)}
                    style={{ cursor: "pointer", background: expandedId === run.id ? "var(--paper)" : undefined }}
                  >
                    <td style={{ fontWeight: 500, color: "var(--ink)" }}>
                      {run.action_type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                    </td>
                    <td><span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{DOMAIN_LABELS[run.domain] || run.domain}</span></td>
                    <td><Badge variant={STATUS_BADGE[run.status] || "default"}>{run.status}</Badge></td>
                    <td style={{ color: "var(--ink-muted)" }}>
                      {run.total_tool_calls > 0 ? `${run.total_tool_calls} tool${run.total_tool_calls !== 1 ? "s" : ""}` : "—"}
                    </td>
                    <td style={{ fontSize: "0.76rem", color: "var(--ink-muted)" }}>
                      {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
                    </td>
                    <td style={{ color: "var(--ink-muted)" }}>{expandedId === run.id ? "▲" : "▼"}</td>
                  </tr>
                  {expandedId === run.id && <ExpandedRow run={run} colSpan={COL} />}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {hasMore && !loading && runs.length > 0 && (
        <div style={{ textAlign: "center", marginTop: "1.5rem" }}>
          <button className="btn btn-outline btn-sm" onClick={() => fetchRuns(offset + PAGE_SIZE, true)}>
            Load more
          </button>
        </div>
      )}
      {loading && runs.length > 0 && (
        <div style={{ textAlign: "center", marginTop: "1.5rem" }}><Spinner size={20} /></div>
      )}
    </div>
  );
}
