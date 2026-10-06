"use client";

import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, BarChart3, Check, ClipboardList, ListTodo, Megaphone, Plus } from "lucide-react";

type Contact = { id: string; first_name: string; last_name: string };
type Campaign = { id: string; name: string; description: string; channel: string; content: string; status: string; recipients: number; recipient_statuses: Record<string, number> };
type Question = { id: string; label: string; type: "text" | "choice" | "rating"; required: boolean; options: string[]; optionsText?: string };
type Survey = { id: string; name: string; description: string; questions: Question[]; status: string; responses: number };
type Task = { id: string; title: string; description: string; status: string; created_at: string };
type Report = { total_responses: number; breakdown: Record<string, Record<string, number | null>> };
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function PhaseFourPanel({ section, token, role, contacts }: { section: "campaigns" | "surveys" | "tasks" | "analytics"; token: string; role: string; contacts: Contact[] }) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [surveys, setSurveys] = useState<Survey[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedContacts, setSelectedContacts] = useState<string[]>([]);
  const [content, setContent] = useState("");
  const [objective, setObjective] = useState("");
  const [draftChannel, setDraftChannel] = useState("web");
  const [questions, setQuestions] = useState<Question[]>([{ id: "q1", label: "", type: "text", required: true, options: [] }]);
  const [answerSurvey, setAnswerSurvey] = useState<Survey | null>(null);
  const [report, setReport] = useState<{ survey: Survey; data: Report } | null>(null);
  const [analyticsQuestion, setAnalyticsQuestion] = useState("");
  const [analyticsAnswer, setAnalyticsAnswer] = useState<{ answer: string; metrics: Record<string, number>; breakdown?: Record<string, number>; series?: { date: string; interactions: number }[] } | null>(null);
  const canManage = ["owner", "admin", "manager"].includes(role);
  const canApprove = ["owner", "admin"].includes(role);

  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API}${path}`, { ...options, headers: { Authorization: `Bearer ${token}`, ...(options.body ? { "Content-Type": "application/json" } : {}) } });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
    return data;
  }
  async function load() {
    try {
      if (section === "campaigns") setCampaigns(await request<Campaign[]>("/api/v1/campaigns"));
      if (section === "surveys") setSurveys(await request<Survey[]>("/api/v1/surveys"));
      if (section === "tasks") setTasks(await request<Task[]>("/api/v1/tasks"));
      setError("");
    } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { load(); /* section changed */ }, [section, token]);
  async function action(path: string) {
    setBusy(true); setError("");
    try { await request(path, { method: "POST" }); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function draftContent() {
    setBusy(true); setError("");
    try {
      const result = await request<{ content: string }>("/api/v1/campaigns/content-draft", { method: "POST", body: JSON.stringify({ objective, channel: draftChannel }) });
      setContent(result.content);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      await request("/api/v1/campaigns", { method: "POST", body: JSON.stringify({ ...values, content, contact_ids: selectedContacts }) });
      setShowCreate(false); setSelectedContacts([]); setContent(""); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function createSurvey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const prepared = questions.map(({ optionsText, ...question }) => ({ ...question, options: question.type === "choice" ? (optionsText || "").split(",").map(x => x.trim()).filter(Boolean) : [] }));
      await request("/api/v1/surveys", { method: "POST", body: JSON.stringify({ ...values, questions: prepared }) });
      setShowCreate(false); setQuestions([{ id: "q1", label: "", type: "text", required: true, options: [] }]); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function recordResponse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!answerSurvey) return;
    setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    const answers: Record<string, string | number> = {};
    for (const question of answerSurvey.questions) {
      const value = String(form.get(question.id) || "").trim();
      if (value) answers[question.id] = question.type === "rating" ? Number(value) : value;
    }
    try {
      await request(`/api/v1/surveys/${answerSurvey.id}/responses`, { method: "POST", body: JSON.stringify({ contact_id: form.get("contact_id"), answers }) });
      setAnswerSurvey(null); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function showReport(survey: Survey) {
    try { setReport({ survey, data: await request<Report>(`/api/v1/surveys/${survey.id}/report`) }); setError(""); }
    catch (e) { setError((e as Error).message); }
  }
  async function askAnalytics(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try { setAnalyticsAnswer(await request("/api/v1/analytics/query", { method: "POST", body: JSON.stringify({ question: analyticsQuestion }) })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function updateQuestion(index: number, patch: Partial<Question>) { setQuestions(current => current.map((q, i) => i === index ? { ...q, ...patch } : q)); }

  const titles = { campaigns: ["Campaigns", "Create and review messages before they reach your community."], surveys: ["Surveys", "Collect structured feedback and see the results."], tasks: ["Follow-ups", "Keep unanswered conversations from slipping away."], analytics: ["Analytics", "Ask for counts from your workspace data."] };
  return <>
    <div className="page-intro"><div><div className="eyebrow">ENGAGEMENT WORKFLOWS</div><h1>{titles[section][0]}</h1><p>{titles[section][1]}</p></div>{canManage && ["campaigns", "surveys"].includes(section) && <button className="primary" onClick={() => setShowCreate(true)}><Plus size={17}/> New {section === "campaigns" ? "campaign" : "survey"}</button>}</div>
    {error && <div className="error" role="alert">{error}</div>}
    {section === "campaigns" && <div className="phase-list">{campaigns.length ? campaigns.map(c => <section className="panel phase-card" key={c.id}><div className="phase-heading"><span className="phase-icon"><Megaphone size={19}/></span><div><h2>{c.name}</h2><small>{c.channel} · {c.recipients} selected contacts</small></div><span className="pill">{c.status.replaceAll("_", " ")}</span></div><p>{c.description || c.content}</p><div className="phase-preview">{c.content}</div><div className="phase-meta">{Object.entries(c.recipient_statuses).map(([status, count]) => <span key={status}>{status.replaceAll("_", " ")}: {count}</span>)}</div><div className="phase-actions">{c.status === "draft" && canManage && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/campaigns/${c.id}/submit`)}>Submit for approval</button>}{c.status === "pending_approval" && canApprove && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/campaigns/${c.id}/approve`)}>Approve</button>}{c.status === "approved" && canApprove && <button className="primary" disabled={busy} onClick={() => action(`/api/v1/campaigns/${c.id}/start`)}>Start campaign <ArrowRight size={16}/></button>}{c.status === "running" && canApprove && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/campaigns/${c.id}/pause`)}>Pause</button>}{c.status === "paused" && canApprove && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/campaigns/${c.id}/resume`)}>Resume</button>}</div></section>) : <div className="panel phase-empty"><Megaphone size={26}/><h2>No campaigns yet</h2><p>Create a draft, select contacts, and submit it for approval.</p></div>}</div>}
    {section === "surveys" && <div className="phase-list">{surveys.length ? surveys.map(s => <section className="panel phase-card" key={s.id}><div className="phase-heading"><span className="phase-icon"><ClipboardList size={19}/></span><div><h2>{s.name}</h2><small>{s.questions.length} questions · {s.responses} responses</small></div><span className="pill">{s.status}</span></div><p>{s.description}</p><div className="phase-actions">{s.status === "draft" && canApprove && <button className="secondary" disabled={busy} onClick={() => action(`/api/v1/surveys/${s.id}/publish`)}>Publish</button>}{s.status === "active" && <button className="secondary" onClick={() => setAnswerSurvey(s)}>Record response</button>}<button className="secondary" onClick={() => showReport(s)}>View report</button></div></section>) : <div className="panel phase-empty"><ClipboardList size={26}/><h2>No surveys yet</h2><p>Build a survey to start collecting structured feedback.</p></div>}</div>}
    {section === "tasks" && <><div className="phase-actions phase-toolbar"><button className="secondary" disabled={busy} onClick={() => action("/api/v1/follow-ups/scan")}>Scan unanswered conversations</button><small>Messages awaiting a reply for 24 hours create a task.</small></div><div className="phase-list">{tasks.length ? tasks.map(t => <section className="panel phase-card" key={t.id}><div className="phase-heading"><span className="phase-icon"><ListTodo size={19}/></span><div><h2>{t.title}</h2><small>{new Date(t.created_at).toLocaleString()}</small></div><span className="pill">{t.status}</span></div><p>{t.description}</p>{t.status === "open" && <button className="secondary" onClick={() => action(`/api/v1/tasks/${t.id}/complete`)}><Check size={16}/> Mark complete</button>}</section>) : <div className="panel phase-empty"><ListTodo size={26}/><h2>No follow-ups yet</h2><p>Unanswered conversations will appear here after 24 hours.</p></div>}</div></>}
    {section === "analytics" && <section className="panel phase-analytics"><span className="phase-icon"><BarChart3 size={20}/></span><h2>Ask your workspace</h2><p>Ask for counts, channel distribution, or interaction trends over the last 14 days. Answers use controlled workspace metrics.</p><form onSubmit={askAnalytics}><input value={analyticsQuestion} onChange={e => setAnalyticsQuestion(e.target.value)} placeholder="Show interaction trends over the last 14 days" required minLength={3}/><button className="primary" disabled={busy}>Get answer <ArrowRight size={16}/></button></form>{analyticsAnswer && <div className="phase-report"><strong>{analyticsAnswer.answer}</strong>{Object.entries(analyticsAnswer.metrics).map(([key, value]) => <div key={key}>{key}: {value}</div>)}{analyticsAnswer.breakdown && Object.entries(analyticsAnswer.breakdown).map(([key, value]) => <div key={key}>{key}: {value}</div>)}{analyticsAnswer.series && <div className="phase-trend">{analyticsAnswer.series.map(row => <span key={row.date} title={`${row.date}: ${row.interactions}`} style={{ height: `${Math.min(100, Math.max(5, row.interactions * 10))}px` }}/>)}</div>}</div>}</section>}
    {showCreate && <div className="overlay" onClick={() => setShowCreate(false)}><section className="drawer" onClick={e => e.stopPropagation()}><button className="drawer-close icon-button" onClick={() => setShowCreate(false)} aria-label="Close">×</button>{section === "campaigns" ? <><div className="eyebrow">REVIEWED OUTREACH</div><h2>New campaign</h2><p className="muted">A campaign stays in draft until an owner or admin approves it. Starting it is a separate action.</p><form className="drawer-form" onSubmit={createCampaign}><label>Name<input name="name" minLength={2} maxLength={200} required/></label><label>Description<input name="description" maxLength={2000}/></label><label>Channel<select name="channel" value={draftChannel} onChange={e => setDraftChannel(e.target.value)}><option value="web">Web chat</option><option value="whatsapp">WhatsApp (configured engine required)</option></select></label><label>Objective for template draft<input value={objective} onChange={e => setObjective(e.target.value)} placeholder="e.g. The community meeting is on Friday"/></label><button type="button" className="secondary" disabled={busy || objective.length < 5} onClick={draftContent}>Create content draft</button><label>Message<textarea value={content} onChange={e => setContent(e.target.value)} minLength={5} maxLength={2000} required/></label><div className="phase-contact-select"><strong>Recipients · {selectedContacts.length} selected</strong>{contacts.map(c => <label key={c.id}><input type="checkbox" checked={selectedContacts.includes(c.id)} onChange={e => setSelectedContacts(current => e.target.checked ? [...current, c.id] : current.filter(id => id !== c.id))}/>{c.first_name} {c.last_name}</label>)}</div><button className="primary full" disabled={busy || !selectedContacts.length}>Save draft <ArrowRight size={17}/></button></form></> : <><div className="eyebrow">LISTEN TO YOUR COMMUNITY</div><h2>New survey</h2><form className="drawer-form" onSubmit={createSurvey}><label>Name<input name="name" minLength={2} maxLength={200} required/></label><label>Description<input name="description" maxLength={2000}/></label>{questions.map((q, i) => <div className="phase-question" key={q.id}><strong>Question {i + 1}</strong><label>Label<input value={q.label} onChange={e => updateQuestion(i, { label: e.target.value })} minLength={3} maxLength={300} required/></label><label>Type<select value={q.type} onChange={e => updateQuestion(i, { type: e.target.value as Question["type"], options: [], optionsText: "" })}><option value="text">Text</option><option value="choice">Choice</option><option value="rating">Rating 1–5</option></select></label>{q.type === "choice" && <label>Options, separated by commas<input value={q.optionsText || ""} onChange={e => updateQuestion(i, { optionsText: e.target.value })} required/></label>}<label className="phase-check"><input type="checkbox" checked={q.required} onChange={e => updateQuestion(i, { required: e.target.checked })}/> Required</label></div>)}<button type="button" className="secondary" disabled={questions.length >= 20} onClick={() => setQuestions(current => [...current, { id: `q${current.length + 1}`, label: "", type: "text", required: true, options: [] }])}>Add question</button><button className="primary full" disabled={busy}>Save survey draft <ArrowRight size={17}/></button></form></>}</section></div>}
    {answerSurvey && <div className="overlay" onClick={() => setAnswerSurvey(null)}><section className="drawer" onClick={e => e.stopPropagation()}><button className="drawer-close icon-button" onClick={() => setAnswerSurvey(null)} aria-label="Close">×</button><div className="eyebrow">SURVEY RESPONSE</div><h2>{answerSurvey.name}</h2><form className="drawer-form" onSubmit={recordResponse}><label>Contact<select name="contact_id" required><option value="">Select a contact</option>{contacts.map(c => <option key={c.id} value={c.id}>{c.first_name} {c.last_name}</option>)}</select></label>{answerSurvey.questions.map(q => <label key={q.id}>{q.label}{q.type === "text" ? <textarea name={q.id} required={q.required} maxLength={2000}/> : <select name={q.id} required={q.required}><option value="">Select an answer</option>{(q.type === "rating" ? ["1", "2", "3", "4", "5"] : q.options).map(v => <option key={v} value={v}>{v}</option>)}</select>}</label>)}<button className="primary full" disabled={busy}>Save response</button></form></section></div>}
    {report && <div className="overlay" onClick={() => setReport(null)}><section className="drawer" onClick={e => e.stopPropagation()}><button className="drawer-close icon-button" onClick={() => setReport(null)} aria-label="Close">×</button><div className="eyebrow">SURVEY REPORT</div><h2>{report.survey.name}</h2><p>{report.data.total_responses} responses</p>{report.survey.questions.map(q => <div className="phase-report" key={q.id}><strong>{q.label}</strong>{Object.entries(report.data.breakdown[q.id] || {}).map(([key, value]) => <div key={key}>{key}: {value ?? "—"}</div>)}</div>)}</section></div>}
  </>;
}
