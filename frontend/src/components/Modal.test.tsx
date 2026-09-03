import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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

  it("does not re-capture the wrong focus target when its onClose prop changes identity while it stays open", async () => {
    // Reproduces the Approvals decide-flow bug directly, isolated from
    // Approvals.tsx: clicking Confirm there flips `decideMutation.isPending`
    // to true, re-rendering the row -- which hands this Modal a brand new
    // `onClose` closure (every caller writes `onClose={() => ...}` inline)
    // while the dialog stays open, AND disables the row's own opener
    // button in that same render. With `[onClose]` in the effect's deps,
    // that re-render tore the effect down and re-ran it: cleanup tried to
    // refocus the (now-disabled) opener, which silently failed, so the new
    // setup re-captured `previouslyFocused` as whatever was ACTUALLY
    // focused at that moment instead -- a stand-in "Distractor" button
    // here, the Confirm button itself in the real flow. On the dialog's
    // real close, THAT wrong element -- not the true original opener --
    // is what regained focus.
    function ReRenderHarness() {
      const [open, setOpen] = useState(false);
      const [openerDisabled, setOpenerDisabled] = useState(false);
      return (
        <div>
          <button type="button" disabled={openerDisabled} onClick={() => setOpen(true)}>
            Opener
          </button>
          <button type="button" onClick={() => setOpenerDisabled(true)}>
            Distractor
          </button>
          {open && (
            <Modal title="Test dialog" onClose={() => setOpen(false)}>
              <button type="button" onClick={() => setOpen(false)}>
                Confirm
              </button>
            </Modal>
          )}
        </div>
      );
    }

    const user = userEvent.setup();
    render(<ReRenderHarness />);

    await user.click(screen.getByRole("button", { name: "Opener" }));
    await screen.findByRole("dialog");

    // Simulates the mid-decision re-render: something else (Approvals'
    // `decideMutation.isPending` flipping true) disables the opener AND
    // hands Modal a fresh `onClose` identity, all in the same render,
    // while the dialog is still open.
    await user.click(screen.getByRole("button", { name: "Distractor" }));
    expect(screen.getByRole("button", { name: "Opener" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Confirm" }));

    // The true original opener -- not the "Distractor" that only happened
    // to be focused at the moment of the corrupting re-render -- is what
    // this dialog opened from, and is where focus belongs (even though it
    // is now disabled and so cannot actually receive it back -- what
    // matters here is that the WRONG element, Distractor, does not).
    expect(screen.getByRole("button", { name: "Distractor" })).not.toHaveFocus();
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
