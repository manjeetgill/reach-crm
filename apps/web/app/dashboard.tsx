"use client";

import { useEffect, useState } from "react";

type Dashboard = { messages_today: number; campaigns: number; survey_responses: number; open_tasks: number; open_escalations: number; agent_runs: number; agent_failures: number; outbound_sent: number; outbound_failed: number; channel_distribution: Record<string, number>; interaction_series: { date: string; interactions: number }[] };
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function DashboardInsights({ token }: { token: string }) {
  const [data, setData] = useState<Dashboard | null>(null);
  useEffect(() => {
    fetch(`${API}/api/v1/analytics/dashboard`, { headers: { Authorization: `Bearer ${token}` } }).then(r => r.ok ? r.json() : Promise.reject()).then(setData).catch(() => setData(null));
  }, [token]);
  if (!data) return null;
  const max = Math.max(1, ...data.interaction_series.map(row => row.interactions));
  const channels = Object.entries(data.channel_distribution);
  const channelMax = Math.max(1, ...channels.map(([, count]) => count));
  return <section className="dashboard-insights"><div className="section-heading"><h2>Operations and trends</h2><span>Live workspace data</span></div><div className="insight-metrics"><div><strong>{data.messages_today}</strong><span>Messages today</span></div><div><strong>{data.open_escalations}</strong><span>Human escalations</span></div><div><strong>{data.open_tasks}</strong><span>Open tasks</span></div><div><strong>{data.campaigns}</strong><span>Campaigns</span></div><div><strong>{data.survey_responses}</strong><span>Survey responses</span></div><div><strong>{data.agent_runs}</strong><span>Agent runs</span></div></div><div className="insight-charts"><div className="panel insight-panel"><h3>Interactions · last 14 days</h3><div className="trend-bars" role="img" aria-label="Interactions over the last 14 days">{data.interaction_series.map(row => <div key={row.date} title={`${row.date}: ${row.interactions} interactions`}><span style={{ height: `${Math.max(row.interactions ? 5 : 2, row.interactions / max * 100)}%` }}/></div>)}</div><div className="insight-axis"><span>{data.interaction_series[0]?.date}</span><span>{data.interaction_series.at(-1)?.date}</span></div></div><div className="panel insight-panel"><h3>Channels and delivery</h3>{channels.length ? channels.map(([channel, count]) => <div className="channel-bar" key={channel}><span>{channel}</span><div><i style={{ width: `${count / channelMax * 100}%` }}/></div><strong>{count}</strong></div>) : <p className="muted">No interactions yet.</p>}<div className="insight-delivery"><span>Outbound sent: <strong>{data.outbound_sent}</strong></span><span>Failed: <strong>{data.outbound_failed}</strong></span><span>Agent failures: <strong>{data.agent_failures}</strong></span></div></div></div></section>;
}
