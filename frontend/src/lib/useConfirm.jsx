/**
 * useConfirm — promise-based confirmation, so a styled dialog is as easy to
 * call as the native `confirm()` it replaces.
 *
 *   const { confirm, confirmDialog } = useConfirm();
 *
 *   const remove = async () => {
 *     if (!await confirm({
 *       title: "Move to Trash?",
 *       message: "You can restore it from Trash for 30 days.",
 *       confirmLabel: "Move to Trash",
 *     })) return;
 *     ...
 *   };
 *
 *   return (<> ...page... {confirmDialog} </>);
 *
 * Keeping the call site shaped like `if (!await confirm(...)) return;` matters:
 * it's the same shape as the `if (!confirm(...)) return;` lines it replaces, so
 * converting a page can't accidentally invert the condition and delete on
 * cancel.
 */

import { useCallback, useRef, useState } from "react";
import ConfirmDialog from "../components/ConfirmDialog";

export function useConfirm() {
  const [state, setState] = useState(null);   // null = closed
  const [loading, setLoading] = useState(false);
  const resolverRef = useRef(null);

  const confirm = useCallback((options) => {
    setState(options || {});
    return new Promise((resolve) => { resolverRef.current = resolve; });
  }, []);

  const settle = useCallback((answer) => {
    setState(null);
    setLoading(false);
    resolverRef.current?.(answer);
    resolverRef.current = null;
  }, []);

  const confirmDialog = (
    <ConfirmDialog
      open={state !== null}
      title={state?.title || "Are you sure?"}
      message={state?.message}
      confirmLabel={state?.confirmLabel || "Confirm"}
      cancelLabel={state?.cancelLabel || "Cancel"}
      tone={state?.tone || "default"}
      loading={loading}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  );

  return { confirm, confirmDialog, setConfirmLoading: setLoading };
}
