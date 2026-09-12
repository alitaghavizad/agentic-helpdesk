# Screenshots

The images the root `README.md` and `frontend/README.md` embed.

Captured from the app running locally against a seeded database, at a
1280×800 viewport, alternating themes so both are visible:

| file | screen | theme |
| --- | --- | --- |
| `login.png` | `/login` | light |
| `admin-overview.png` | `/admin` | light |
| `chat.png` | `/chat`, one conversation open | dark |
| `traces.png` | `/admin/traces`, one run's waterfall with two spans expanded | dark |
| `tickets.png` | `/admin/tickets` | light |
| `approvals.png` | `/admin/approvals` | dark |

Every shot is taken as the seeded admin except `chat.png`: the chat
transcript belongs to the employee who had the conversation (`alex.hart34`),
and admin's own conversation list does not contain it.

To retake one: start the backend (`make dev`) and the frontend
(`npm run dev`), sign in, set the theme, and screenshot at 1280×800. The
theme is whatever `localStorage.helpdesk.theme` holds — `"light"` or
`"dark"`, or the nav bar's own toggle.

Four of these screens are only worth photographing against a database that
has agent output in it. `tickets`, `approvals`, `chat` and `traces` all
render their (correct, and correctly tested) empty state on a freshly
seeded database, because `backend/app/db/seed.py` creates user accounts and
nothing else — tickets, approval requests, lessons and spans arrive
organically from live agent runs. Populate those before retaking, or the
images will document empty states rather than the screens.

Nothing here is generated at build time, and nothing reads these files at
runtime; they are documentation only. Keep them in step with the UI when a
screen changes shape, and keep each one under a few hundred KB.
