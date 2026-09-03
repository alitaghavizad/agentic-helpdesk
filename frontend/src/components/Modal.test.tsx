import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./Modal";

/**
 * Modal's focus trap and restore -- added when Approvals.tsx became "the
 * approvals modal" D5 deferred a decision on
 * (docs/superpowers/specs/2026-09-02-admin-panel-frontend-design.md): a
 * decide flow confirming a real-world, no-undo side effect (send an email,
 * grant system access) is exactly the case D5 said to revisit for.
 */

function Harness({ onClose }: { onClose: () => void }) {
  return (
    <div>
      <button type="button">Outside before</button>
      <Modal title="Test dialog" onClose={onClose}>
        <button type="button">First</button>
        <button type="button">Second</button>
      </Modal>
      <button type="button">Outside after</button>
    </div>
  );
}

describe("Modal", () => {
  it("focuses the dialog panel itself on open, not a guessed descendant", async () => {
    render(<Harness onClose={vi.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveFocus();
  });

  it("wraps Tab from the last focusable element back to the first, without escaping to the page behind it", async () => {
    const user = userEvent.setup();
    render(<Harness onClose={vi.fn()} />);

    const closeButton = screen.getByRole("button", { name: "Close" });
    // Tab order inside the dialog: Close (header) -> First -> Second, per
    // DOM order -- so the last stop before wrapping is "Second".
    const second = screen.getByRole("button", { name: "Second" });

    // Panel itself is focused (tabIndex=-1, not in tab order); the first
    // Tab moves into the dialog's own first focusable child.
    await user.tab();
    expect(closeButton).toHaveFocus();
    await user.tab();
    await user.tab();
    expect(second).toHaveFocus();
    await user.tab();
    // Wrapped back to the dialog's own first focusable element (Close),
    // not out to "Outside after".
    expect(closeButton).toHaveFocus();
  });

  it("wraps Shift+Tab from the first focusable element to the last", async () => {
    const user = userEvent.setup();
    render(<Harness onClose={vi.fn()} />);

    const closeButton = screen.getByRole("button", { name: "Close" });
    const second = screen.getByRole("button", { name: "Second" });
    closeButton.focus();

    await user.tab({ shift: true });
    expect(second).toHaveFocus();
  });

  it("restores focus to the element that had it before the dialog opened, once it closes", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    expect(opener).toHaveFocus();

    const onClose = vi.fn();
    const { unmount } = render(
      <Modal title="Test dialog" onClose={onClose}>
        <button type="button">Confirm</button>
      </Modal>,
    );

    expect(screen.getByRole("dialog")).toHaveFocus();
    unmount();

    expect(opener).toHaveFocus();
    document.body.removeChild(opener);
  });

  it("still closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Modal title="Test dialog" onClose={onClose}><button type="button">Confirm</button></Modal>);

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a backdrop click but not on a click inside the panel", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Modal title="Test dialog" onClose={onClose}><button type="button">Confirm</button></Modal>);

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("dialog").parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
