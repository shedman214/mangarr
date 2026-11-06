import { expect, test, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Sidebar from "../src/components/Sidebar";
import { SidebarLogout } from "../src/components/SidebarLogout";
import { LoginSettings } from "../src/components/LoginSettings";
import { fakeApi, renderWithClient } from "./helpers";

test("Log out appears directly after Settings only when login is enabled", async () => {
  fakeApi((method, path) => path === "/settings/login" ? { body: { enabled: true } }
    : path === "/queue" ? { body: [] } : undefined);
  renderWithClient(<Sidebar onSearch={() => {}} />);
  const button = await screen.findByRole("button", { name: "Log out" });
  expect(screen.getByRole("link", { name: /Settings/ }).nextElementSibling).toBe(button);
});

test("Log out stays hidden when login is off", async () => {
  const calls = fakeApi((method, path) => path === "/settings/login" ? { body: { enabled: false } }
    : path === "/queue" ? { body: [] } : undefined);
  renderWithClient(<Sidebar onSearch={() => {}} />);
  await waitFor(() => expect(calls.some(c => c.path === "/settings/login")).toBe(true));
  expect(screen.queryByRole("button", { name: "Log out" })).not.toBeInTheDocument();
});

test("keyboard logout ends the session before leaving the app", async () => {
  const calls = fakeApi((method, path) => method === "POST" && path === "/auth/logout" ? { status: 204 } : undefined);
  const left = vi.fn();
  renderWithClient(<SidebarLogout onLoggedOut={left} />);
  screen.getByRole("button", { name: "Log out" }).focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(left).toHaveBeenCalledTimes(1));
  expect(calls).toEqual([{ method: "POST", path: "/auth/logout", body: undefined }]);
});

test("a failed logout stays signed in and offers another attempt", async () => {
  let attempt = 0;
  fakeApi(() => ++attempt === 1 ? { status: 503 } : { status: 204 });
  const left = vi.fn();
  renderWithClient(<SidebarLogout onLoggedOut={left} />);
  await userEvent.click(screen.getByRole("button", { name: "Log out" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not log out. Please try again.");
  expect(left).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Log out" }));
  await waitFor(() => expect(left).toHaveBeenCalledTimes(1));
});

test("the existing Settings sign-out also reports a refused logout", async () => {
  fakeApi((method) => method === "GET"
    ? { body: { enabled: true, username: "admin", disabled_by_environment: false, fixed_api_key: false } }
    : { status: 503 });
  renderWithClient(<LoginSettings />);
  await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not log out. Please try again.");
  expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
});
