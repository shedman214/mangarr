import { useMutation, useQueryClient } from "@tanstack/react-query";
import { setApiKey, signOut } from "../api/client";

export function SidebarLogout({ onLoggedOut = () => window.location.reload() }: { onLoggedOut?: () => void }) {
  const client = useQueryClient();
  const logout = useMutation({
    mutationFn: signOut,
    onSuccess: () => {
      setApiKey("");
      client.clear();
      onLoggedOut();
    },
  });
  return <>
    <button type="button" className="nav-item sidebar-logout" disabled={logout.isPending}
      onClick={() => logout.mutate()}>
      <span className="icon" aria-hidden="true">⇥</span>
      {logout.isPending ? "Logging out…" : "Log out"}
    </button>
    {logout.isError && <div className="sidebar-logout-error" role="alert">
      Could not log out. Please try again.
    </div>}
  </>;
}
