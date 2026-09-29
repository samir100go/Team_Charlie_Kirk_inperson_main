import type { Metadata } from "next";
// Self-hosted Geist (bundled from the `geist` package): no Google Fonts download at
// build time, so the image builds and the demo runs offline (project.md §11.3).
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "JALANI · Fuel Operations Center",
  description: "Fuel supply intelligence & resilience platform (simulated environment)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
