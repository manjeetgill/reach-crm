"use client";
import { FormEvent, useEffect, useState } from "react";
import { Activity, AlertTriangle, ArrowRight, BarChart3, BookOpen, Building2, Check, ChevronRight, ClipboardList, ContactRound, LayoutDashboard, ListTodo, LogOut, Megaphone, Menu, MessageCircle, Plus, Search, SearchCheck, ShieldCheck, Users, Wallet, X } from "lucide-react";
import KnowledgePanel from "./knowledge";
import PhaseFourPanel from "./phase4";
import PhaseFivePanel from "./phase5";
import DashboardInsights from "./dashboard";
import WhatsAppAgentPanel from "./whatsapp-agent";

type User = { id: string; organization_id: string; name: string; email: string; role: string };
type Contact = { id: string; first_name: string; last_name: string; phone: string | null; email: string | null; language: string | null; status: string; created_at: string };
type Consent = { id: string; channel: string; status: string; source: string; captured_at: string };
type Interaction = { id: string; channel: string; type: string; direction: string; timestamp: string };
type Conversation = { id: string; contact_id: string; channel: string; status: string; started_at: string };
type Message = { id: string; content: string; direction: string; status: string; timestamp: string };
type SuggestedReply = { answer: string; mode: string; citations: { title: string; version: number; excerpt: string }[]; missing: string[] };
type Overview = { contacts: number; active_conversations: number; messages: number; interactions: number; consent_records: number };
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [token, setToken] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [page, setPage] = useState<"overview" | "contacts" | "conversations" | "knowledge" | "campaigns" | "surveys" | "tasks" | "analytics" | "hygiene" | "escalations" | "usage" | "workspace" | "whatsapp">("overview");
  const [mode, setMode] = useState<"login" | "signup">("signup");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Contact | null>(null);
  const [detail, setDetail] = useState<{consents: Consent[]; interactions: Interaction[]; conversations: Conversation[]} | null>(null);
  const [thread, setThread] = useState<{conversation: Conversation; messages: Message[]} | null>(null);
  const [suggestion, setSuggestion] = useState<SuggestedReply | null>(null);
  const [draftMessage, setDraftMessage] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [newContact, setNewContact] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [orgId, setOrgId] = useState("");
  const [workspaces, setWorkspaces] = useState<{ id: string; name: string; type: string }[]>([]);

  async function request<T>(path: string, options: RequestInit = {}, auth = token): Promise<T> {
    const headers: Record<string, string> = { ...(options.headers as Record<string,string> || {}) };
    if (auth) headers.Authorization = `Bearer ${auth}`;
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API}${path}`, { ...options, headers });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
    return data;
  }
  async function refresh(auth = token) {
    try {
      const [a,b,c] = await Promise.all([
        request<Overview>("/api/v1/analytics/overview", {}, auth),
        request<{items: Contact[]}>("/api/v1/contacts?limit=100", {}, auth),
        request<Conversation[]>("/api/v1/conversations", {}, auth)
      ]);
      setOverview(a); setContacts(b.items); setConversations(c); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => {
    setOrgId(localStorage.getItem("reach_org_id") || "");
    const saved = localStorage.getItem("reach_token");
    if (!saved) return;
    request<User>("/api/v1/auth/me", {}, saved).then(u => { setToken(saved); setUser(u); refresh(saved); }).catch(() => localStorage.removeItem("reach_token"));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!token || page !== "contacts") return;
    const timer = setTimeout(() => request<{items: Contact[]}>(`/api/v1/contacts?limit=100&q=${encodeURIComponent(query)}`).then(r => setContacts(r.items)).catch(e => setError(e.message)), 250);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, page, token]);

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const path = mode === "signup" ? "/api/v1/organizations" : "/api/v1/auth/login";
      const result = await request<{token: string; user: User}>(path, {method: "POST", body: JSON.stringify(values)}, "");
      localStorage.setItem("reach_token", result.token); localStorage.setItem("reach_org_id", result.user.organization_id); setToken(result.token); setUser(result.user); await refresh(result.token);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function findWorkspaces(event: React.MouseEvent<HTMLButtonElement>) {
    const form = event.currentTarget.form;
    if (!form) return;
    const values = new FormData(form);
    const email = String(values.get("email") || "");
    const password = String(values.get("password") || "");
    if (!email || !password) { setError("Enter your email and password to find workspaces."); return; }
    setBusy(true); setError("");
    try {
      const found = await request<{id: string; name: string; type: string}[]>("/api/v1/auth/workspaces", {method:"POST", body:JSON.stringify({email, password})}, "");
      setWorkspaces(found); setOrgId(found[0]?.id || "");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function logout() { localStorage.removeItem("reach_token"); setToken(""); setUser(null); setSelected(null); setThread(null); }
  async function openContact(c: Contact) { setSelected(c); setThread(null); try { const d = await request<{consents: Consent[]; interactions: Interaction[]; conversations: Conversation[]}>(`/api/v1/contacts/${c.id}`); setDetail(d); } catch (e) { setError((e as Error).message); } }
  async function openThread(c: Conversation) { try { setThread(await request(`/api/v1/conversations/${c.id}`)); setSelected(null); setSuggestion(null); setDraftMessage(""); } catch (e) { setError((e as Error).message); } }
  async function openConversationById(id: string) { try { setThread(await request(`/api/v1/conversations/${id}`)); setSelected(null); setSuggestion(null); setDraftMessage(""); setPage("conversations"); } catch (e) { setError((e as Error).message); } }
  async function suggestReply() {
    if (!thread) return;
    setSuggesting(true); setSuggestion(null); setError("");
    try {
      const result = await request<SuggestedReply>(`/api/v1/conversations/${thread.conversation.id}/suggest-reply`, {method:"POST", body:JSON.stringify({idempotency_key:crypto.randomUUID()})});
      setSuggestion(result);
    } catch (e) { setError((e as Error).message); } finally { setSuggesting(false); }
  }
  async function escalateThread() {
    if (!thread) return;
    try { await request(`/api/v1/conversations/${thread.conversation.id}/escalate`, {method:"POST", body:JSON.stringify({reason:"Operator requested human review", priority:"high"})}); await openThread(thread.conversation); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  async function createContact(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); const values = Object.fromEntries([...new FormData(event.currentTarget)].filter(([, value]) => String(value).trim() !== "")); try { await request("/api/v1/contacts", {method:"POST", body:JSON.stringify(values)}); setNewContact(false); await refresh(); setPage("contacts"); } catch(e) {setError((e as Error).message);} finally {setBusy(false);} }

  async function recordConsent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return;
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try { await request(`/api/v1/contacts/${selected.id}/consents`, {method:"POST", body:JSON.stringify(values)}); await openContact(selected); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  async function startConversation() {
    if (!selected) return;
    try { const conversation = await request<Conversation>("/api/v1/conversations", {method:"POST", body:JSON.stringify({contact_id:selected.id, channel:"web"})}); await refresh(); await openThread(conversation); }
    catch (e) { setError((e as Error).message); }
  }
  async function sendThreadMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!thread) return;
    const content = draftMessage.trim();
    if (!content) return;
    try { await request(`/api/v1/conversations/${thread.conversation.id}/messages`, {method:"POST", body:JSON.stringify({content})}); await openThread(thread.conversation); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  if (!user) return <main className="auth-shell"><div className="auth-art"><div className="brand brand-light"><span className="brand-mark">R</span> reach<span className="brand-dot">.</span></div><div className="auth-art-content"><div className="eyebrow light">A better way to stay connected</div><h1>Every connection<br/>deserves a <em>conversation.</em></h1><p>One calm place to understand your community, keep conversations moving, and make every interaction count.</p><div className="art-decoration"><span/><span/><span/></div></div><div className="auth-art-footer">Thoughtful engagement at every scale.</div></div><div className="auth-panel"><div className="auth-card"><div className="eyebrow">WELCOME TO REACH</div><h2>{mode === "signup" ? "Create your workspace" : "Welcome back"}</h2><p className="muted">{mode === "signup" ? "Bring your people and conversations together." : "Sign in to your engagement workspace."}</p><form key={mode} onSubmit={authenticate}>{mode === "signup" ? <><label>Organization name<input name="name" placeholder="Your organization" required minLength={2}/></label><label>Your name<input name="owner_name" placeholder="Your full name" required minLength={2}/></label><label>Work email<input type="email" name="owner_email" placeholder="you@organization.com" required/></label></> : <><label>Organization ID<input name="organization_id" placeholder="Workspace ID" value={orgId} onChange={e => setOrgId(e.target.value)} required/></label>{workspaces.length > 0 && <label>Your workspaces<select value={orgId} onChange={e => setOrgId(e.target.value)}>{workspaces.map(w => <option key={w.id} value={w.id}>{w.name} · {w.type}</option>)}</select></label>}<label>Email<input type="email" name="email" placeholder="you@organization.com" required/></label></>}<label>Password<input type="password" name="password" minLength={mode === "signup" ? 12 : 1} placeholder="••••••••••••" required/></label>{error && <div className="error" role="alert">{error}</div>}<button className="primary full" disabled={busy}>{busy ? "Please wait…" : mode === "signup" ? "Create workspace" : "Sign in"}<ArrowRight size={17}/></button>{mode === "login" && <button className="auth-find" type="button" disabled={busy} onClick={findWorkspaces}>Find my workspaces</button>}</form><div className="auth-switch">{mode === "signup" ? "Already have a workspace?" : "New to Reach?"} <button onClick={() => {setMode(mode === "signup" ? "login" : "signup"); setError("");}}>{mode === "signup" ? "Sign in" : "Get started"}</button></div></div></div></main>;

  const nav = [{key:"overview", label:"Overview", icon:LayoutDashboard}, {key:"contacts", label:"Contacts", icon:Users}, {key:"conversations", label:"Conversations", icon:MessageCircle}, {key:"whatsapp", label:"WhatsApp agent", icon:MessageCircle}, {key:"knowledge", label:"Knowledge", icon:BookOpen}, {key:"campaigns", label:"Campaigns", icon:Megaphone}, {key:"surveys", label:"Surveys", icon:ClipboardList}, {key:"tasks", label:"Follow-ups", icon:ListTodo}, {key:"analytics", label:"Analytics", icon:BarChart3}, {key:"hygiene", label:"Data hygiene", icon:SearchCheck}, {key:"escalations", label:"Escalations", icon:AlertTriangle}, {key:"usage", label:"Usage", icon:Wallet}, {key:"workspace", label:"Workspace", icon:Building2}] as const;
  return <div className="app-shell"><aside className={`sidebar ${mobileNav ? "open" : ""}`}><div className="brand"><span className="brand-mark">R</span> reach<span className="brand-dot">.</span></div><div className="workspace-label">WORKSPACE</div><nav aria-label="Main navigation">{nav.map(n => <button key={n.key} className={page === n.key ? "nav active" : "nav"} onClick={() => {setPage(n.key); setSelected(null); setThread(null); setMobileNav(false);}}><n.icon size={19}/>{n.label}{page === n.key && <span className="nav-indicator"/>}</button>)}</nav><div className="sidebar-bottom"><div className="workspace-id"><span>Workspace ID</span><button title="Copy workspace ID" onClick={() => navigator.clipboard.writeText(user.organization_id)}>{user.organization_id.slice(0,8)}… <span>Copy</span></button></div><div className="sidebar-help"><ShieldCheck size={19}/><strong>Your data, your space</strong><p>Conversations and contacts stay within your organization.</p></div><button className="account" onClick={logout}><span className="avatar">{user.name.slice(0,1).toUpperCase()}</span><span><strong>{user.name}</strong><small>{user.role}</small></span><LogOut size={17}/></button></div></aside><div className="main-area"><header className="topbar"><button className="mobile-menu icon-button" onClick={() => setMobileNav(!mobileNav)} aria-label="Toggle menu"><Menu size={22}/></button><div className="breadcrumbs">Workspace <ChevronRight size={15}/> <strong>{page[0].toUpperCase() + page.slice(1)}</strong></div><div className="topbar-right"><span className="status-dot"/> All systems ready <span className="top-avatar">{user.name.slice(0,1).toUpperCase()}</span></div></header><main className="content">{error && <div className="error dismissible" role="alert">{error}<button onClick={() => setError("")}><X size={16}/></button></div>}{page === "overview" && <><div className="page-intro"><div><div className="eyebrow">YOUR WORKSPACE AT A GLANCE</div><h1>Good to see you, {user.name.split(" ")[0]} <span className="wave">✳</span></h1><p>Here’s what’s happening across your community today.</p></div><button className="secondary" onClick={() => refresh()}><Activity size={16}/> Refresh data</button></div><div className="hero-card"><div><div className="hero-kicker">MADE FOR MEANINGFUL ENGAGEMENT</div><h2>People first.<br/><em>Always.</em></h2><p>Every contact is a relationship worth remembering. Start by adding your people, then bring their conversations into focus.</p><button onClick={() => {setPage("contacts"); setNewContact(true);}}>Add your first contact <ArrowRight size={17}/></button></div><div className="hero-graphic"><div className="orb orb-one"/><div className="orb orb-two"/><div className="orb orb-three"/><div className="hero-lines"/></div></div><div className="section-heading"><h2>At a glance</h2><span>Live workspace metrics</span></div><div className="stats-grid"><Stat icon={<ContactRound/>} label="Total contacts" value={overview?.contacts} accent="purple"/><Stat icon={<MessageCircle/>} label="Active conversations" value={overview?.active_conversations} accent="orange"/><Stat icon={<Activity/>} label="Interactions" value={overview?.interactions} accent="blue"/><Stat icon={<ShieldCheck/>} label="Consent records" value={overview?.consent_records} accent="green"/></div><div className="overview-lower"><div className="panel"><div className="panel-head"><h3>Recent contacts</h3><button onClick={() => setPage("contacts")}>View all <ArrowRight size={15}/></button></div>{contacts.length ? contacts.slice(0,4).map(c => <button className="recent-row" key={c.id} onClick={() => {setPage("contacts"); openContact(c);}}><span className="contact-avatar">{initials(c)}</span><span className="recent-main"><strong>{c.first_name} {c.last_name}</strong><small>{c.email || c.phone || "No contact details"}</small></span><ChevronRight size={16}/></button>) : <Empty title="Your community starts here" description="Add a contact to begin building a shared history." action={() => {setPage("contacts");setNewContact(true);}}/>}</div><div className="panel next-panel"><div className="panel-head"><h3>Getting started</h3></div><Step done={true} text="Create your workspace"/><Step done={contacts.length > 0} text="Add your first contact"/><Step done={(overview?.consent_records || 0) > 0} text="Record channel consent"/><Step done={conversations.length > 0} text="Start a conversation"/><p className="next-note">A thoughtful foundation makes every conversation easier.</p></div></div><DashboardInsights token={token}/></>}{page === "whatsapp" && <WhatsAppAgentPanel token={token} role={user.role}/>} {page === "knowledge" && <KnowledgePanel token={token} role={user.role}/>} {(["campaigns", "surveys", "tasks", "analytics"] as const).includes(page as "campaigns" | "surveys" | "tasks" | "analytics") && <PhaseFourPanel section={page as "campaigns" | "surveys" | "tasks" | "analytics"} token={token} role={user.role} contacts={contacts}/>} {(["hygiene", "escalations", "usage", "workspace"] as const).includes(page as "hygiene" | "escalations" | "usage" | "workspace") && <PhaseFivePanel section={page as "hygiene" | "escalations" | "usage" | "workspace"} token={token} role={user.role} onCreateWorkspace={() => { logout(); setMode("signup"); }} onOpenConversation={openConversationById}/>} {page === "contacts" && <><div className="page-intro"><div><div className="eyebrow">YOUR COMMUNITY</div><h1>Contacts</h1><p>Keep the people behind every conversation in focus.</p></div><button className="primary" onClick={() => setNewContact(true)}><Plus size={17}/> Add contact</button></div><div className="list-panel"><div className="list-toolbar"><div className="search"><Search size={19}/><input aria-label="Search contacts" placeholder="Search by name, email or phone…" value={query} onChange={e => setQuery(e.target.value)}/></div><span>{contacts.length} contacts</span></div>{contacts.length ? <div className="table-wrap"><table><thead><tr><th>NAME</th><th>CONTACT</th><th>LANGUAGE</th><th>STATUS</th><th/></tr></thead><tbody>{contacts.map(c => <tr key={c.id} onClick={() => openContact(c)}><td><div className="table-name"><span className="contact-avatar">{initials(c)}</span><strong>{c.first_name} {c.last_name}</strong></div></td><td>{c.email || c.phone || "—"}</td><td>{c.language || "—"}</td><td><span className="pill">{c.status}</span></td><td><ChevronRight size={16}/></td></tr>)}</tbody></table></div> : <Empty title={query ? "No matching contacts" : "No contacts yet"} description={query ? "Try another name, email or phone number." : "Create the first contact in your workspace."} action={() => setNewContact(true)}/>}</div></>}{page === "conversations" && <><div className="page-intro"><div><div className="eyebrow">STAY IN THE LOOP</div><h1>Conversations</h1><p>Every exchange, together in one place.</p></div></div><div className="list-panel">{conversations.length ? conversations.map(c => <button key={c.id} className="conversation-row" onClick={() => openThread(c)}><span className="conversation-icon"><MessageCircle size={20}/></span><span><strong>{contacts.find(p => p.id === c.contact_id)?.first_name || "Contact"}</strong><small>{c.channel} · {new Date(c.started_at).toLocaleDateString()}</small></span><span className="pill">{c.status}</span><ChevronRight size={17}/></button>) : <Empty title="No conversations yet" description="Conversations will appear here as messages arrive through connected channels."/>}</div></>}</main></div>{(selected || thread || newContact) && <div className="overlay" onClick={() => {setSelected(null);setThread(null);setNewContact(false);}}><section className="drawer" onClick={e => e.stopPropagation()}><button className="drawer-close icon-button" onClick={() => {setSelected(null);setThread(null);setNewContact(false);}} aria-label="Close"><X size={20}/></button>{newContact && <><div className="eyebrow">GROW YOUR COMMUNITY</div><h2>Add contact</h2><p className="muted">Create a profile to keep their interactions in one place.</p><form className="drawer-form" onSubmit={createContact}><label>First name<input name="first_name" required/></label><label>Last name<input name="last_name"/></label><label>Phone<input name="phone" type="tel"/></label><label>Email<input name="email" type="email"/></label><label>Language<input name="language" placeholder="e.g. English"/></label><button className="primary full" disabled={busy}>Save contact <ArrowRight size={17}/></button></form></>}{selected && <><div className="profile-head"><span className="profile-avatar">{initials(selected)}</span><div><div className="eyebrow">CONTACT PROFILE</div><h2>{selected.first_name} {selected.last_name}</h2><span className="pill">{selected.status}</span></div></div><div className="profile-section"><h3>Contact details</h3><Info label="Email" value={selected.email}/><Info label="Phone" value={selected.phone}/><Info label="Language" value={selected.language}/></div><div className="profile-section"><h3>Actions</h3><button className="primary" onClick={startConversation}><MessageCircle size={16}/> Start web conversation</button></div><div className="profile-section"><h3>Channel consent</h3>{detail?.consents.length ? detail.consents.map(c => <div className="detail-row" key={c.id}><span>{c.channel}</span><strong>{c.status === "opt_in" ? "Opted in" : "Opted out"}</strong></div>) : <p className="muted">No consent has been recorded.</p>}<form className="consent-form" onSubmit={recordConsent}><select name="channel" aria-label="Channel"><option value="web">Web</option><option value="whatsapp">WhatsApp</option><option value="voice">Voice</option><option value="email">Email</option><option value="sms">SMS</option></select><select name="status" aria-label="Consent status"><option value="opt_in">Opt in</option><option value="opt_out">Opt out</option></select><input name="source" placeholder="Source, e.g. signed form" required minLength={2}/><button type="submit">Record</button></form></div><div className="profile-section"><h3>Recent interactions</h3>{detail?.interactions.length ? detail.interactions.map(i => <div className="detail-row" key={i.id}><span>{i.channel} · {i.type}</span><small>{new Date(i.timestamp).toLocaleDateString()}</small></div>) : <p className="muted">No interactions yet.</p>}</div></>}{thread && <><div className="eyebrow">CONVERSATION</div><h2>{contacts.find(c => c.id === thread.conversation.contact_id)?.first_name || "Contact"}</h2><p className="muted">{thread.conversation.channel} · {thread.conversation.status}</p>{thread.conversation.status !== "waiting" && thread.messages.length > 0 && <button className="secondary" onClick={escalateThread}><AlertTriangle size={16}/> Escalate to human</button>}<div className="messages">{thread.messages.length ? thread.messages.map(m => <div className={`message ${m.direction}`} key={m.id}><p>{m.content}</p><small>{new Date(m.timestamp).toLocaleString()} · {m.status}</small></div>) : <p className="muted">No messages in this conversation.</p>}</div>{thread.messages.some(m => m.direction === "inbound") && <div className="reply-assistant"><button className="secondary" onClick={suggestReply} disabled={suggesting}><BookOpen size={16}/>{suggesting ? "Finding sources…" : "Suggest reply from knowledge"}</button>{suggestion && <div className="reply-suggestion"><span className="result-mode">{suggestion.mode}</span><p>{suggestion.answer || suggestion.missing.join(" ") || "No supported answer found."}</p>{suggestion.citations.map((c, i) => <div className="reply-source" key={i}><strong>{c.title} · v{c.version}</strong><small>{c.excerpt}</small></div>)}{suggestion.answer && thread.conversation.channel === "web" && <button className="secondary" onClick={() => setDraftMessage(suggestion.answer)}>Use as draft</button>}</div>}</div>}{thread.conversation.channel === "web" && <form className="send-form" onSubmit={sendThreadMessage}><input name="content" placeholder="Write a message…" aria-label="Message" value={draftMessage} onChange={e => setDraftMessage(e.target.value)} required/><button type="submit"><ArrowRight size={17}/></button></form>}</>}</section></div>}</div>;
}
function initials(c: Contact) { return `${c.first_name[0] || ""}${c.last_name[0] || ""}`.toUpperCase(); }
function Stat({icon,label,value,accent}:{icon:React.ReactNode;label:string;value:number|undefined;accent:string}) { return <div className="stat"><span className={`stat-icon ${accent}`}>{icon}</span><span className="stat-value">{value ?? "—"}</span><span className="stat-label">{label}</span></div>; }
function Step({done,text}:{done:boolean;text:string}) { return <div className="step"><span className={done ? "step-icon done" : "step-icon"}>{done ? <Check size={14}/> : null}</span><span>{text}</span></div>; }
function Info({label,value}:{label:string;value:string|null}) { return <div className="detail-row"><span>{label}</span><strong>{value || "—"}</strong></div>; }
function Empty({title,description,action}:{title:string;description:string;action?:()=>void}) { return <div className="empty"><div className="empty-icon"><Users size={24}/></div><h3>{title}</h3><p>{description}</p>{action && <button onClick={action}>Add contact <ArrowRight size={15}/></button>}</div>; }
