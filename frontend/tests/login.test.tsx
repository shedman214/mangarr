import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginPage } from "../src/components/LoginPage";
import { LoginSettings } from "../src/components/LoginSettings";
import { fakeApi, renderWithClient } from "./helpers";

const off = { enabled: false, username: "", disabled_by_environment: false, fixed_api_key: false };

describe("Sign-in page", () => {
  it("signs in and shows a refused sign-in", async () => {
    let attempts = 0;
    const calls = fakeApi((method, path) => {
      if (method === "POST" && path === "/auth/login") return ++attempts === 1 ? { status: 401, body: { detail: "Incorrect username or password" } } : { status: 204 };
    });
    const onSignedIn = vi.fn();
    renderWithClient(<LoginPage onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "wrong password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password");
    expect(onSignedIn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(calls[0].body).toEqual({ username: "admin", password: "wrong password" });
  });
});

describe("Settings → Login", () => {
  it("turns login on only with a matching password of at least 8 characters", async () => {
    const calls = fakeApi((method, path) => {
      if (path === "/settings/login" && method === "GET") return { body: off };
      if (path === "/settings/login" && method === "PUT") return { body: { ...off, enabled: true, username: "admin", apiKey: "n".repeat(32) } };
    });
    renderWithClient(<LoginSettings />);
    expect(await screen.findByText(/Login is/)).toHaveTextContent("Login is off.");
    const turnOn = screen.getByRole("button", { name: "Turn on login" });
    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    expect(screen.getByText("Use at least 8 characters.")).toBeInTheDocument();
    expect(turnOn).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Password"));
    await userEvent.type(screen.getByLabelText("Password"), "long enough");
    await userEvent.type(screen.getByLabelText("Repeat password"), "long enougH");
    expect(turnOn).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Repeat password"));
    await userEvent.type(screen.getByLabelText("Repeat password"), "long enough");
    expect(turnOn).toBeEnabled();
    await userEvent.click(turnOn);
    expect(await screen.findByText(/This browser stays signed in/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ enabled: true, username: "admin", new_password: "long enough", current_password: "" });
  });

  it("asks for the current password before turning login off", async () => {
    const calls = fakeApi((method, path) => {
      if (method === "GET") return { body: { ...off, enabled: true, username: "admin" } };
      if (method === "PUT") return { body: { ...off, username: "admin" } };
    });
    renderWithClient(<LoginSettings />);
    const turnOff = await screen.findByRole("button", { name: "Turn off login" });
    expect(turnOff).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Current password"), "long enough");
    await userEvent.click(turnOff);
    expect(await screen.findByText("Login turned off.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ enabled: false, current_password: "long enough", new_password: "" });
  });

  it("warns when a fixed MANGARR_API_KEY keeps the API open", async () => {
    fakeApi(() => ({ body: { ...off, fixed_api_key: true } }));
    renderWithClient(<LoginSettings />);
    expect(await screen.findByText(/MANGARR_API_KEY is set/)).toBeInTheDocument();
  });
});
