import { ContenedorAuth } from "@/components/auth/contenedor-auth";

export default function LayoutAuth({ children }: Readonly<{ children: React.ReactNode }>) {
  return <ContenedorAuth>{children}</ContenedorAuth>;
}
