# Wiki Pages

Pages are the heart of the wiki — write notes, decisions, plans, runbooks, customer context, anything your team (or your agents) needs to remember. Every change is captured automatically, so you can edit fearlessly: nothing is ever truly lost.

---

## 📝 Creating pages

Open **Wiki** in the left sidebar and pick where you want the page to live. From the directory view:

- **+ New folder** — make a top-level area (`projects/`, `runbooks/`, `customers/`, whatever fits how your team thinks).
- **+ New document** — create a page inside the current folder. Pick one of the **starter templates** to get a head-start, or start blank and let the [AI Wiki Helper](AI%20Wiki%20Helper.md) draft an outline for you.

Pages are written in markdown — headings, bullet lists, tables, fenced code, task lists, and links all render the way you'd expect.

---

## ✏️ Editing, moving, deleting

- **Edit** — click **Edit** in the page header. Make your changes and save.
- **Move or rename** — drag a page (or a whole folder) onto another folder in the directory view, or use the rename action. The full edit history follows the page across the move.
- **Delete** — use the delete action right from the directory view; no need to open the page first.

---

## 🔍 Search

The search bar (top of the left sidebar) finds pages by content. Plain keywords are fine — no quoting or `path:` syntax required. Results show a short snippet and the page path; click through to read the full page.

The [AI Wiki Helper](AI%20Wiki%20Helper.md) and any connected agents use the same search under the hood, so anything you can find by hand, they can find too.

---

## 📜 History

Every page has a **History** view that lists each change, who made it, and when. You can:

- Open an older version to read it as it was at the time.
- Copy content back into the current version if something was removed by mistake.
- Spot what an agent (or a teammate) changed without having to ask.

---

## 🤝 Sharing and permissions

By default, every page is visible to everyone in your workspace. This default behavior can be changed in the Admin panel.

- Grant **read** or **write** access to specific users or groups.
- Apply the grant to a folder, and it cascades to everything inside — so locking down `customers/secret-deal/` once is enough to cover all the pages under it.

Permissions also affect agents: a connected agent acting on your behalf has the same access you do, no more.

---

## ⚠️ What not to put here

- **Secrets** — passwords, API keys, anything you wouldn't want exported or seen by agents. Edit history sticks around forever, so redactions require system admin and engineering help.
- **Blob storage** — build outputs, log dumps, big CSV exports. The wiki will function file with these files in the system but won't be able to effectively reference them (for now).
