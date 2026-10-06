"use client";

import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, BookOpen, Check, FileText, Plus, Search } from "lucide-react";

type Document = { id: string; title: string; source: string; status: string; version: number; updated_at: string };
type Citation = { id: string; document_id: string; title: string; source: string; version: number; excerpt: string };
type Answer = { answer: string; mode: string; citations: Citation[]; missing: string[]; model: string | null };
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function KnowledgePanel({ token, role }: { token: string; role: string }) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const canEdit = ["owner", "admin", "manager"].includes(role);
  const canApprove = ["owner", "admin"].includes(role);

  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: { Authorization: `Bearer ${token}`, ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) },
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
    return data;
  }

  async function load() {
    try { setDocuments(await request<Document[]>("/api/v1/knowledge/documents")); }
    catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { load(); /* refresh when entering the page */ }, [token]);

  async function addDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      await request("/api/v1/knowledge/documents", { method: "POST", body: JSON.stringify(values) });
      setShowAdd(false); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await request("/api/v1/knowledge/documents/upload", { method: "POST", body: new FormData(event.currentTarget) });
      setShowAdd(false); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function approve(id: string) {
    setBusy(true); setError("");
    try { await request(`/api/v1/knowledge/documents/${id}/approve`, { method: "POST" }); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setAnswer(null);
    try {
      const result = await request<Answer>("/api/v1/knowledge/ask", { method: "POST", body: JSON.stringify({ question, idempotency_key: crypto.randomUUID() }) });
      setAnswer(result);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <>
    <div className="page-intro"><div><div className="eyebrow">TRUSTED INFORMATION</div><h1>Knowledge</h1><p>Give your team clear answers grounded in approved documents.</p></div>{canEdit && <button className="primary" onClick={() => setShowAdd(!showAdd)}><Plus size={17}/> Add document</button>}</div>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="knowledge-grid">
      <section className="panel knowledge-ask"><div className="knowledge-icon"><Search size={21}/></div><h2>Ask your knowledge base</h2><p className="muted">Answers use approved sources from this workspace. Citations appear alongside every supported answer.</p><form onSubmit={ask}><label htmlFor="knowledge-question">Your question</label><textarea id="knowledge-question" value={question} onChange={e => setQuestion(e.target.value)} placeholder="e.g. When is the office open?" minLength={3} maxLength={1500} required/><button className="primary" disabled={busy}>{busy ? "Finding sources…" : "Get answer"}<ArrowRight size={16}/></button></form>{answer && <div className="knowledge-result"><span className="result-mode">{answer.mode === "model" ? `AI answer · ${answer.model}` : answer.mode === "extractive" ? "Source excerpt" : "No matching source"}</span>{answer.answer ? <p>{answer.answer}</p> : <p className="muted">{answer.missing.join(" ")}</p>}{answer.citations.length > 0 && <div className="citations"><strong>Sources</strong>{answer.citations.map(c => <div key={c.id} className="citation"><FileText size={16}/><div><strong>{c.title} · v{c.version}</strong><small>{c.source || "Workspace document"}</small><p>{c.excerpt}</p></div></div>)}</div>}</div>}</section>
      <section className="panel knowledge-documents"><div className="panel-head"><h3>Documents</h3><span>{documents.length} total</span></div>{documents.length ? documents.map(d => <div className="knowledge-doc" key={d.id}><div className="doc-icon"><FileText size={18}/></div><div className="doc-main"><strong>{d.title}</strong><small>{d.source || "Workspace document"} · Version {d.version}</small><span className={`doc-status ${d.status}`}>{d.status === "approved" ? <Check size={12}/> : null}{d.status}</span></div>{d.status === "draft" && canApprove && <button disabled={busy} onClick={() => approve(d.id)}>Approve</button>}</div>) : <div className="empty"><div className="empty-icon"><BookOpen size={24}/></div><h3>No knowledge yet</h3><p>Add a document and approve it to start answering questions.</p></div>}</section>
    </div>
    {showAdd && <div className="overlay" onClick={() => setShowAdd(false)}><section className="drawer" onClick={e => e.stopPropagation()}><button className="drawer-close icon-button" onClick={() => setShowAdd(false)} aria-label="Close">×</button><div className="eyebrow">ADD TRUSTED CONTENT</div><h2>New document</h2><p className="muted">Add organization-approved information. It will stay in draft until an owner or admin approves it.</p><form className="drawer-form" onSubmit={addDocument}><label>Title<input name="title" minLength={2} maxLength={200} required placeholder="e.g. Office hours"/></label><label>Source<input name="source" maxLength={300} placeholder="e.g. Official notice, 2026"/></label><label>Content<textarea name="content" minLength={20} maxLength={200000} required placeholder="Paste the information your team may use to answer questions…"/></label><button className="primary full" disabled={busy}>Save draft <ArrowRight size={17}/></button></form><div className="upload-divider">or upload a file</div><form className="drawer-form" onSubmit={uploadDocument}><label>Title<input name="title" minLength={2} maxLength={200} required placeholder="Document title"/></label><label>File<input name="file" type="file" accept=".pdf,.docx,.txt,.md,.html,.htm" required/></label><label>Source<input name="source" maxLength={300} placeholder="Optional source label"/></label><button className="secondary full" disabled={busy}>Upload as draft <ArrowRight size={17}/></button></form></section></div>}
  </>;
}
