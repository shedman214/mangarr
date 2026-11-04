import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import ToomicsCookieTest from "../src/components/ToomicsCookieTest";
import { fakeApi, renderWithClient } from "./helpers";

test("tests the entered cookie without saving and clears stale results after editing", async () => {
  const calls = fakeApi(() => ({ body: { status: "signed_in", message: "Cookie accepted" } }));
  const view = renderWithClient(<ToomicsCookieTest cookie="test=private" />);
  await userEvent.click(screen.getByRole("button", { name: "Test cookie" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Cookie accepted");
  expect(calls).toEqual([{ method: "POST", path: "/settings/toomics/test-cookie", body: { session_cookie: "test=private" } }]);
  expect(screen.queryByText("test=private")).not.toBeInTheDocument();
  view.rerender(<ToomicsCookieTest cookie="changed=value" />);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

test("empty cookie cannot be tested", () => {
  renderWithClient(<ToomicsCookieTest cookie=" " />);
  expect(screen.getByRole("button", { name: "Test cookie" })).toBeDisabled();
});

test("failure never displays server request details", async () => {
  fakeApi(() => ({ status: 502, body: { detail: "sensitive request details" } }));
  renderWithClient(<ToomicsCookieTest cookie="••••••••" />);
  await userEvent.click(screen.getByRole("button", { name: "Test cookie" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Could not check");
  expect(screen.queryByText(/sensitive request/)).not.toBeInTheDocument();
});
