# On Track contributor guide

Read `MUST-DO-FIRST.md` before executing scripts. Read `CONTRIBUTING.md` for the
architecture, privacy boundaries, and domain invariants. Use the commands in
`README.md`; run tests, lint, types, and build before claiming a change works.

Preserve unrelated local work. Use only synthetic fixtures in source and tests.
Private operational notes, credentials, account identifiers, and historical data
must remain outside the publication tree. Scan staged content before commits.

Do not change AGENTS.md without explicit confirmation. Changes to this file need
explicit owner authorization. Confirm the Railway project/environment/service
before the first deployment in a session. A passing local test is not proof that
the deployed application is running the change.
