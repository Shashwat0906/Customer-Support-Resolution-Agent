import { useEffect, useState } from "react";
import { api, type Principal, setSession, storedPrincipal } from "./api/client";
import { Badge } from "./components/ui";
import { useHashRoute } from "./hooks";
import { ApprovalDetail } from "./pages/ApprovalDetail";
import { CustomerHome } from "./pages/CustomerHome";
import { Login } from "./pages/Login";
import { Ops } from "./pages/Ops";
import { AllTickets, ApprovalQueue } from "./pages/ReviewerHome";
import { TicketDetail } from "./pages/TicketDetail";

function ApiStatus() {
  const [state, setState] = useState<"ok" | "down" | "checking">("checking");
  useEffect(() => {
    let alive = true;
    const check = () => api.health().then((h) => alive && setState(h.status === "ok" ? "ok" : "down"))
      .catch(() => alive && setState("down"));
    check();
    const t = window.setInterval(check, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  return <Badge tone={state === "ok" ? "ok" : state === "down" ? "danger" : ""}>
    {state === "ok" ? "API online" : state === "down" ? "API unreachable" : "Checking API"}</Badge>;
}

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(storedPrincipal());
  const [route, navigate] = useHashRoute();

  function logout() {
    setSession(null, null);
    setPrincipal(null);
    navigate("/");
  }

  const reviewer = principal?.kind === "staff";
  const parts = route.split("/").filter(Boolean);
  let page: JSX.Element;
  if (!principal) page = <Login onLogin={(p) => { setPrincipal(p); navigate(p.kind === "staff" ? "/approvals" : "/"); }} />;
  else if (parts[0] === "tickets" && parts[1]) page = <TicketDetail key={parts[1]} ticketId={parts[1]} principal={principal} />;
  else if (reviewer && parts[0] === "approvals" && parts[1]) page = <ApprovalDetail key={parts[1]} approvalId={parts[1]} principal={principal} />;
  else if (reviewer && parts[0] === "tickets") page = <AllTickets />;
  else if (reviewer && parts[0] === "ops") page = <Ops />;
  else if (reviewer) page = <ApprovalQueue principal={principal} />;
  else page = <CustomerHome customerId={principal.id} navigate={navigate} />;

  const link = (path: string, text: string) => (
    <a href={`#${path}`} className={route === path || (path !== "/" && route.startsWith(path)) ? "active" : ""}>{text}</a>
  );

  return (
    <>
      <header className="topbar">
        <div className="brand"><img src="/favicon.svg" alt="" /> VoltCart Support <small>Resolution Agent</small></div>
        {principal && (
          <nav aria-label="Main">
            {reviewer ? <>{link("/approvals", "Approvals")}{link("/tickets", "Tickets")}{link("/ops", "Demo ops")}</>
              : link("/", "My support")}
          </nav>
        )}
        <span className="spacer" />
        <ApiStatus />
        {principal && <span className="row small"><strong>{principal.name}</strong>
          <span className="muted">{reviewer ? principal.role.replace("_", " ") : principal.id}</span>
          <button onClick={logout}>Switch user</button></span>}
      </header>
      <main>{page}</main>
    </>
  );
}
