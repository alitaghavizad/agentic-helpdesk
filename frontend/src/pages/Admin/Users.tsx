import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as admin from "../../api/endpoints/admin";
import type { UserPatch, UserSummary, UsersPage } from "../../api/endpoints/admin";
import { StateBlock, describeError } from "../../components/StateBlock";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { Badge } from "../../components/Badge";
import { Pager } from "../../components/Pager";

const ROLES = ["guest", "employee", "helpdesk", "admin"] as const;
const CLEARANCES = ["standard", "sensitive", "privileged"] as const;

function usersQueryKey(offset: number) {
  return ["admin", "users", offset] as const;
}

/**
 * `employee_ref`/`helpdesk_ref` are the two seed populations
 * (backend/app/admin/router.py's `admin_users` -- also the derivation for
 * `dev_seed`); a real user row can carry neither, one, or (never in
 * practice, but the type allows it) both. Joining whichever are set is
 * simpler than a screen that assumes exactly one.
 */
function refsOf(row: UserSummary): string {
  const refs = [row.employee_ref, row.helpdesk_ref].filter((ref): ref is string => Boolean(ref));
  return refs.length > 0 ? refs.join(", ") : "—";
}

/**
 * Spec 15's Users screen: "126 accounts; role and clearance editable;
 * dev-seed badge". Editing either field sends a `PATCH` for that field
 * alone (an omitted field means "leave alone" server-side -- see
 * `UserPatch`'s docstring in app/admin/router.py) and re-renders the row
 * from `UserPatchResult`, the response the backend widened in Task 0
 * specifically so this screen never needs a second fetch to show its own
 * edit.
 *
 * Paginated rather than fetched whole: the seed puts 126 rows in this
 * table, and `queries.clamp_limit` caps a single page at 200 anyway, so an
 * unpaginated fetch would just be one oversized request standing in for
 * what `Pager` already does correctly. No `limit` is sent on the request
 * itself -- the server's own default (50) governs page size, and `Pager`
 * paginates off whatever `limit` the response actually carries, not a
 * client-side assumption of what that default is.
 */
export function Users() {
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: usersQueryKey(offset),
    queryFn: () => admin.adminUsers({ offset }),
  });

  const patchMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: UserPatch }) => admin.patchUser(id, patch),
    onSuccess: (result) => {
      // Patches the currently-viewed page's cache from the PATCH response
      // directly -- role and clearance only, everything else on the row is
      // untouched -- rather than refetching the list. A second fetch here
      // would also be wrong on its own terms: the offset that produced this
      // row may no longer contain it once a role/clearance edit changes
      // nothing about ordering, so a refetch could not even be trusted to
      // still show the row that was just edited.
      queryClient.setQueryData<UsersPage>(usersQueryKey(offset), (old) =>
        old && {
          ...old,
          items: old.items.map((row) =>
            row.id === result.id ? { ...row, role: result.role, clearance: result.clearance } : row,
          ),
        },
      );
    },
  });

  const page = listQuery.data;
  const rows = page?.items ?? [];

  const columns: Column<UserSummary>[] = [
    { key: "username", header: "Username", render: (row) => row.username },
    { key: "full_name", header: "Full name", render: (row) => row.full_name },
    { key: "email", header: "Email", render: (row) => row.email },
    {
      key: "role",
      header: "Role",
      render: (row) => (
        <select
          aria-label={`Role for ${row.username}`}
          value={row.role}
          onChange={(event) =>
            patchMutation.mutate({ id: row.id, patch: { role: event.target.value as UserPatch["role"] } })
          }
          className="rounded border border-slate-300 px-1 py-0.5 text-xs"
        >
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {role}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: "clearance",
      header: "Clearance",
      render: (row) => (
        <select
          aria-label={`Clearance for ${row.username}`}
          value={row.clearance ?? ""}
          onChange={(event) =>
            patchMutation.mutate({
              id: row.id,
              patch: { clearance: event.target.value as UserPatch["clearance"] },
            })
          }
          className="rounded border border-slate-300 px-1 py-0.5 text-xs"
        >
          {/* A row with no clearance on record needs a selectable value
              that maps to nothing real -- PATCH has no way to explicitly
              set clearance back to null (an omitted field means "leave
              alone", per UserPatch's docstring), so this option exists
              purely so the <select> has a valid initial value to show. */}
          {row.clearance === null && (
            <option value="" disabled>
              —
            </option>
          )}
          {CLEARANCES.map((clearance) => (
            <option key={clearance} value={clearance}>
              {clearance}
            </option>
          ))}
        </select>
      ),
    },
    { key: "department", header: "Department", render: (row) => row.department ?? "—" },
    { key: "refs", header: "Refs", render: (row) => refsOf(row) },
    {
      key: "dev_seed",
      header: "Seed",
      render: (row) => (row.dev_seed ? <Badge tone="warning">dev seed</Badge> : null),
    },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-900">Users</h1>

      {patchMutation.isError && (
        <p role="alert" className="text-xs text-red-700">
          {describeError(patchMutation.error)}
        </p>
      )}

      {listQuery.isLoading ? (
        <StateBlock status="loading" />
      ) : listQuery.isError ? (
        <StateBlock status="error" message={describeError(listQuery.error)} />
      ) : rows.length === 0 ? (
        <StateBlock status="empty" emptyLabel="No user accounts." />
      ) : (
        <>
          <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
          {page && <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />}
        </>
      )}
    </div>
  );
}
