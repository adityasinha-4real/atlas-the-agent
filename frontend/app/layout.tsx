import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ATLAS",
  description: "Agentic AI runtime — plan, act, reflect, recover.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
