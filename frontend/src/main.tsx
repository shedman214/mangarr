import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { initClient, LoginRequiredError } from "./api/client";
import { LoginPage } from "./components/LoginPage";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

const rootEl = document.getElementById("root")!;

initClient()
  .then(() => {
    ReactDOM.createRoot(rootEl).render(
      <React.StrictMode>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </React.StrictMode>,
    );
  })
  .catch((err) => {
    if (err instanceof LoginRequiredError) {
      ReactDOM.createRoot(rootEl).render(<LoginPage onSignedIn={() => window.location.reload()} />);
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    ReactDOM.createRoot(rootEl).render(
      <div style={{ padding: 40, fontFamily: "sans-serif", color: "#e1e2e6" }}>
        <h2>Mangarr backend unreachable</h2>
        <p>{message}</p>
      </div>,
    );
  });
