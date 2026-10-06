"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, ArrowRight, Building2, Check, SearchCheck, ShieldCheck } from "lucide-react";

type HygieneIssue = { id: string; kind: string; detail: string; contact_name: string; related_contact_name: string | null; status: string };
type Escalation = { id: string; conversation_id: string; reason: string; priority: string; summary: string; status: string; created_at: string };
type Usage = { measured_events: Record<string, number>; total_messages: number; total_agent_runs: number; total_knowledge_queries: number; billing_status: string; amount_due: number | null };
type Organization = { id: string; name: string; type: string; created_at: string };
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function PhaseFivePanel({ section, token, role, onCreateWorkspace, onOpenConversation }: { section: "hygiene" | "escalations" | "usage" | "workspace"; token: string; role: string; onCreateWorkspace: () => void; onOpenConversation: (id: string) => void }) {
  const [issues, setIssues] = useState<HygieneIssue[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canReview = ["owner", "admin", "manager"].includes(role);

  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API}${path}`, { ...options, headers: { Authorization: `Bearer ${token}` } });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
    return data;
  }
  async function load() {
    try {
      if (section === "hygiene") setIssues(await request<HygieneIssue[]>("/api/v1/hygiene/issues"));
      if (section === "escalations") setEscalations(await request<Escalation[]>("/api/v1/escalations"));
      if (section === "usage") setUsage(await request<Usage>("/api/v1/usage/summary"));
      if (section === "workspace") setOrganization(await request<Organization>("/api/v1/organizations/current"));
      setError("");
    } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { load(); /* section changed */ }, [section, token]);
  async function action(path: string, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await request(path, { method: "POST" }); setNotice(success); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function scan() {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await request<{ contacts_scanned: number; issues_created: Record<string, number> }>("/api/v1/hygiene/scan", { method: "POST" });
      setNotice(`Scanned ${result.contacts_scanned} contacts. ${Object.values(result.issues_created).reduce((a, b) => a + b, 0)} new findings.`);
      await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const titles = { hygiene: ["Data hygiene", "Review duplicate, invalid, and stale contacts before making changes."], escalations: ["Escalations", "Messages needing a human response appear here."], usage: ["Usage", "Measured workspace activity. Billing is not configured."], workspace: ["Workspace", "See your organization and create another workspace."] };
  return <>
    <div className="page-intro"><div><div className="eyebrow">WORKSPACE OPERATIONS</div><h1>{titles[section][0]}</h1><p>{titles[section][1]}</p></div>{section === "hygiene" && canReview && <button className="primary" disabled={busy} onClick={scan}><SearchCheck size={17}/> Scan contacts</button>}</div>
    {error && <div className="error" role="alert">{error}</div>}{notice && <div className="phase5-notice" role="status">{notice}</div>}
    {section === "hygiene" && <div className="phase-list">{issues.length ? issues.map(issue => <section className="panel phase-card" key={issue.id}><div className="phase-heading"><span className="phase-icon"><SearchCheck size={19}/></span><div><h2>{issue.kind.replaceAll("_", " ")}</h2><small>{issue.contact_name}{issue.related_contact_name ? ` · ${issue.related_contact_name}` : ""}</small></div><span className="pill">{issue.status}</span></div><p>{issue.detail}</p><div className="phase-actions">{canReview && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/hygiene/issues/${issue.id}/dismiss`, "Finding dismissed.")}>Dismiss finding</button>}</div></section>) : <div className="panel phase-empty"><SearchCheck size={26}/><h2>No open findings</h2><p>Run a scan to review possible duplicates, invalid details, and stale contacts.</p></div>}</div>}
    {section === "escalations" && <div className="phase-list">{escalations.length ? escalations.map(item => <section className="panel phase-card" key={item.id}><div className="phase-heading"><span className="phase-icon"><AlertTriangle size={19}/></span><div><h2>{item.reason.replaceAll("_", " ")}</h2><small>{new Date(item.created_at).toLocaleString()}</small></div><span className="pill">{item.priority}</span></div><p>{item.summary}</p><div className="phase-actions"><button className="secondary" onClick={() => onOpenConversation(item.conversation_id)}>Open conversation <ArrowRight size={15}/></button>{["owner", "admin", "manager", "operator"].includes(role) && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/escalations/${item.id}/resolve`, "Escalation resolved.")}><Check size={15}/> Resolve</button>}</div></section>) : <div className="panel phase-empty"><ShieldCheck size={26}/><h2>No open escalations</h2><p>Explicit requests for a person and urgent terms create a review task.</p></div>}</div>}
    {section === "usage" && <div className="phase5-usage"><div className="stats-grid"><div className="stat"><span className="stat-icon purple"><Activity/></span><strong className="stat-value">{usage?.total_messages ?? "—"}</strong><span className="stat-label">Messages</span></div><div className="stat"><span className="stat-icon blue"><Activity/></span><strong className="stat-value">{usage?.total_agent_runs ?? "—"}</strong><span className="stat-label">Agent runs</span></div><div className="stat"><span className="stat-icon green"><Activity/></span><strong className="stat-value">{usage?.total_knowledge_queries ?? "—"}</strong><span className="stat-label">Knowledge queries</span></div><div className="stat"><span className="stat-icon orange"><Activity/></span><strong className="stat-value">—</strong><span className="stat-label">Amount due</span></div></div><section className="panel phase5-detail"><h2>Metered events</h2>{usage && Object.entries(usage.measured_events).length ? Object.entries(usage.measured_events).map(([kind, count]) => <div className="detail-row" key={kind}><span>{kind.replaceAll("_", " ")}</span><strong>{count}</strong></div>) : <p className="muted">No metered events yet.</p>}<p className="muted">No prices, invoices, or payment processing are configured.</p></section></div>}
    {section === "workspace" && <section className="panel phase5-detail"><span className="phase-icon"><Building2 size={20}/></span><h2>{organization?.name || "Your workspace"}</h2><div className="detail-row"><span>Type</span><strong>{organization?.type || "—"}</strong></div><div className="detail-row"><span>Workspace ID</span><strong>{organization?.id || "—"}</strong></div><div className="detail-row"><span>Created</span><strong>{organization?.created_at ? new Date(organization.created_at).toLocaleDateString() : "—"}</strong></div><p className="muted">You can create another independent workspace with the same email. On sign-in, use “Find my workspaces” to select it.</p><button className="secondary" onClick={onCreateWorkspace}>Create another workspace <ArrowRight size={16}/></button></section>}
  </>;
}
