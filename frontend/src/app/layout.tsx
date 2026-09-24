import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Proveedores } from "@/components/proveedores";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "AMVARMAR LMS",
    template: "%s | AMVARMAR LMS",
  },
  description: "Plataforma de gestión logística de AMVARMAR.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>
        <Proveedores>{children}</Proveedores>
      </body>
    </html>
  );
}
