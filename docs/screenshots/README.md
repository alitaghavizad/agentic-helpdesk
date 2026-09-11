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

To retake one: start the backend (`make dev`) and the frontend
(`npm run dev`), sign in as the seeded admin, set the theme, and screenshot
at 1280×800. The theme is whatever `localStorage.helpdesk.theme` holds —
`"light"` or `"dark"`, or the nav bar's own toggle.

Nothing here is generated at build time, and nothing reads these files at
runtime; they are documentation only. Keep them in step with the UI when a
screen changes shape, and keep each one under a few hundred KB.
