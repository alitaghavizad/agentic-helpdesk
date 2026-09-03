import { useRef, useState } from "react";
import type { RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { LessonSummary, LessonsPage } from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { Badge } from "../../components/Badge";
import type { BadgeTone } from "../../components/Badge";
import { Modal } from "../../components/Modal";
import { Pager } from "../../components/Pager";

function lessonsQueryKey(offset: number) {
  return ["admin", "lessons", offset] as const;
}

const STATUS_TONE: Record<string, BadgeTone> = {
  active: "success",
  archived: "neutral",
};

const CONFIDENCE_TONE: Record<string, BadgeTone> = {
  low: "neutral",
  medium: "warning",
  high: "info",
};

/**
 * The `content_md` editor for one lesson. Only `content_md` is editable
 * here -- `category`, `ticket_id` and `created_by_run_id` are the lesson's
 * provenance and stay off this form entirely (`LessonPatch`'s docstring in
 * app/admin/router.py: an admin correcting the text must not be able to
 * re-attribute the lesson to a different ticket or run).
 */
function EditLessonModal({
  lesson, submitting, error, onCancel, onSave, restoreFocusFallback,
}: {
  lesson: LessonSummary;
  submitting: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (contentMd: string) => void;
  restoreFocusFallback: RefObject<HTMLElement | null>;
}) {
  const [content, setContent] = useState(lesson.content_md);

  return (
    <Modal title={`Edit ${lesson.title}`} onClose={onCancel} restoreFocusFallback={restoreFocusFallback}>
      <label htmlFor="lesson-content" className="mb-1 block text-xs font-medium text-slate-700">
        Content
      </label>
      <textarea
        id="lesson-content"
        aria-label="Lesson content"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        rows={8}
        className="mb-3 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
      />
      {error && (
        <p role="alert" className="mb-2 text-xs text-red-700">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded px-3 py-1.5 text-sm text-slate-600">
          Cancel
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onSave(content)}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Saving…" : "Save"}
        </button>
      </div>
    </Modal>
  );
}

/**
 * Spec 15's Lessons screen: "Lessons with source ticket; edit and archive".
 *
 * Archive is a plain button, not a confirmation modal like Approvals'
 * decide flow -- that modal exists because approving grants a real-world
 * effect with no undo (send an email, grant access); archiving a lesson is
 * the opposite case the backend went out of its way to design for: it is
 * reversible in spirit (the row survives, PATCH can flip status back to
 * `active`) and explicitly idempotent on a second call
 * (app/admin/router.py's `admin_archive_lesson` docstring: "a panel whose
 * delete button errors on a double-click is worse than one that does
 * nothing"). An interstitial here would be friction the backend's own
 * design deliberately avoided needing.
 */
export function Lessons() {
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<LessonSummary | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  // Fallback focus target for the edit modal -- the row's own Edit button
  // stays mounted through a successful save (unlike Approvals' decide,
  // nothing here removes it), so in practice Modal's own restore-to-opener
  // handles this. Passed anyway for the same defensive reason Approvals'
  // ApprovalCard does: the container is guaranteed to exist even if that
  // assumption is ever wrong.
  const containerRef = useRef<HTMLDivElement>(null);

  function patchLessonInCache(updated: LessonSummary) {
    queryClient.setQueryData<LessonsPage>(lessonsQueryKey(offset), (old) =>
      old && { ...old, items: old.items.map((row) => (row.id === updated.id ? updated : row)) },
    );
  }

  const editMutation = useMutation({
    mutationFn: ({ id, content_md }: { id: string; content_md: string }) =>
      admin.patchLesson(id, { content_md }),
    onSuccess: (updated) => {
      setEditError(null);
      patchLessonInCache(updated);
      setEditing(null);
    },
    onError: (error) => setEditError(describeError(error)),
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => admin.archiveLesson(id),
    onSuccess: (result) => {
      // LessonDeleteResult carries only {id, status, archived} -- merge
      // just the status into the row already in cache rather than
      // dropping it, so the row STAYS in the table (the backend archives,
      // it does not delete: a row that vanished here would misrepresent
      // what actually happened).
      queryClient.setQueryData<LessonsPage>(lessonsQueryKey(offset), (old) =>
        old && {
          ...old,
          items: old.items.map((row) => (row.id === result.id ? { ...row, status: result.status } : row)),
        },
      );
    },
  });

  const listQuery = useQuery({
    queryKey: lessonsQueryKey(offset),
    queryFn: () => admin.adminLessons({ offset }),
  });

  const page = listQuery.data;
  const rows = page?.items ?? [];

  const columns: Column<LessonSummary>[] = [
    { key: "title", header: "Title", render: (row) => row.title },
    { key: "category", header: "Category", render: (row) => row.category },
    {
      key: "status",
      header: "Status",
      render: (row) => <Badge tone={STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge>,
    },
    {
      key: "confidence",
      header: "Confidence",
      render: (row) => <Badge tone={CONFIDENCE_TONE[row.confidence] ?? "neutral"}>{row.confidence}</Badge>,
    },
    { key: "ticket_id", header: "Source ticket", render: (row) => row.ticket_id ?? "—" },
    {
      key: "actions",
      header: "Actions",
      render: (row) => (
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              setEditError(null);
              setEditing(row);
            }}
            className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Edit
          </button>
          <button
            type="button"
            disabled={archiveMutation.isPending && archiveMutation.variables === row.id}
            onClick={() => archiveMutation.mutate(row.id)}
            className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Archive
          </button>
        </div>
      ),
    },
  ];

  return (
    <div ref={containerRef} className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-900">Lessons</h1>

      {archiveMutation.isError && (
        <p role="alert" className="text-xs text-red-700">
          {describeError(archiveMutation.error)}
        </p>
      )}

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No lessons recorded yet." />
      ) : (
        <>
          <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
          {page && <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />}
        </>
      )}

      {editing && (
        <EditLessonModal
          lesson={editing}
          submitting={editMutation.isPending}
          error={editError}
          onCancel={() => setEditing(null)}
          onSave={(content_md) => editMutation.mutate({ id: editing.id, content_md })}
          restoreFocusFallback={containerRef}
        />
      )}
    </div>
  );
}
