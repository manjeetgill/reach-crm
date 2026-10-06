"use client";
import { useEffect, useId, useRef, useState } from "react";

type Session = { id: string; connect_token: string };
type Widget = { destroy(): void };
type SDK = { init(options: {connectToken: string; container: string; onReady(): void; onError(): void; onSuccess(data: {pluginId: string}): void}): Widget };
let sdkPromise: Promise<void> | undefined;
function loadSDK() {
  if ((window as unknown as {ChakraWhatsappConnect?: SDK}).ChakraWhatsappConnect) return Promise.resolve();
  if (!sdkPromise) sdkPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://embed.chakrahq.com/whatsapp-partner-connect/v1_0_1/sdk.js";
    script.onload = () => resolve();
    script.onerror = () => { sdkPromise = undefined; script.remove(); reject(new Error("Could not load WhatsApp authorization. Your saved setup is retained.")); };
    document.head.appendChild(script);
  });
  return sdkPromise;
}
export default function WhatsAppConnect({request, onPrepared}: {request(path: string, body?: unknown): Promise<any>; onPrepared(): Promise<void>}) {
  const [state, setState] = useState("NOT_STARTED");
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const containerId = 'chakra-connect-' + useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const container = useRef<HTMLDivElement>(null);
  const widget = useRef<Widget | null>(null);
  const alive = useRef(true);
  async function refresh() {
    try { const value = await request("/authorization"); if (alive.current) {setState(value.state); setAvailable(value.available); setNotice("");} }
    catch (e) { if (alive.current) setNotice((e as Error).message); }
  }
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; widget.current?.destroy(); }; }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function connect() {
    setBusy(true); setNotice("");
    try {
      const session: Session = await request("/authorization", {});
      setState("CONNECTING"); await loadSDK();
      if (!alive.current || !container.current) return;
      widget.current?.destroy();
      const sdk = (window as unknown as {ChakraWhatsappConnect: SDK}).ChakraWhatsappConnect;
      widget.current = sdk.init({connectToken: session.connect_token, container: `#${containerId}`,
        onReady: () => setNotice("Complete Facebook authorization and your phone verification below."),
        onError: () => setNotice("Authorization interrupted. Refresh status before continuing."),
        onSuccess: async data => {
          try { const result = await request("/authorization/complete", {plugin_id: data.pluginId}); if (alive.current) {setState(result.state); setNotice("Authorization received. Your setup team will verify the number before activation.");} }
          catch (e) { if (alive.current) setNotice((e as Error).message); }
        }});
    } catch (e) { setNotice((e as Error).message); }
    finally { setBusy(false); }
  }
  async function prepare() {
    setBusy(true);
    try { await request("/authorization/prepare", {}); await onPrepared(); setNotice("Agent prepared. Finish the delivery test and enable replies when ready."); }
    catch (e) { setNotice((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="panel phase5-detail" style={{marginTop:20}}><h2>Authorize WhatsApp</h2>
    <p>Connect the business account that owns your saved number. You complete Facebook authorization and any phone verification; we handle the technical setup.</p>
    <p>Status: {state.replaceAll("_", " ").toLowerCase()}</p>
    <button className="secondary" disabled={busy} onClick={()=>void refresh()}>Check authorization</button>{" "}
    {available && ["NOT_STARTED", "CONNECTING"].includes(state) && <button className="primary" disabled={busy} onClick={()=>void connect()}>{state === "CONNECTING" ? "Continue WhatsApp connection" : "Connect WhatsApp"}</button>}
    {state === "CONNECTED" && <button className="primary" disabled={busy} onClick={()=>void prepare()}>Prepare my WhatsApp agent</button>}
    {!available && <p>Your setup team needs to enable provider onboarding for this workspace.</p>}
    <p role="status">{notice}</p><div id={containerId} ref={container}/>
  </section>;
}
