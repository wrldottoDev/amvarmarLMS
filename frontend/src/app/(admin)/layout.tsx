import { PortalShell } from "@/components/layout/portal-shell";
import { PortalProtegido } from "@/features/auth/portal-protegido";

export default function LayoutAdmin({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <PortalProtegido>
      <PortalShell>{children}</PortalShell>
    </PortalProtegido>
  );
}
