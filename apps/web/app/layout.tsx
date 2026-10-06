import type { Metadata } from "next";
import "./style.css";
export const metadata: Metadata = { title: "Reach CRM", description: "Community engagement workspace" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
