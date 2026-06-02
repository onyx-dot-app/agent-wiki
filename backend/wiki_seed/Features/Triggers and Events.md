# Triggers and Events

A **trigger** is a natural-language rule attached to a page or a folder. When something changes that matches the rule, the trigger fires and a record lands in the event log (see [Events](#events) below).

## Anatomy

Each trigger has:

- **Scope** — either a single document (doc-scoped) or a directory (folder-scoped).
- **Firing Criteria** — a verifiable condition for if the trigger should fire or not. For example "If the status of a blocker changes" or "When someone assigns me an item to do".
- **Message** — what message to send along when the condition is met.
- **Destination** — where the message goes. **Event Log** (the default) records the fire to the [Events](#events) tab in-app. **Slack** posts the message to a channel via an incoming webhook — available once an admin configures it under **Admin → Slack**. More destinations (email, AI agent platforms, automation apps) are on the roadmap.

> 💡 The **Message** can refer to the contents of the page or to what changed — it doesn't have to be a static string. For example, on a holiday-party planning page you could set the message to *"List the new RSVPs added since the last update"* — every fire produces a different, context-aware summary.

---

## ✨ Creating a trigger

Three ways to start, depending on the scope you want:

- **From a page** — click **+ Trigger** in the page header. The trigger is locked to that single page.
- **From a folder** — open the directory view and click **+ Trigger** there. The trigger covers every page under that folder, so it's the right move for rules like *"any page under `customers/` gets a status change"*.
- **From the Triggers area** in the left sidebar — click **New trigger** and pick whatever scope you want. Handy when you're already managing triggers and don't want to navigate to the page or folder first.

Fill in the four fields from above, choose how often it should check (see below), and save. You can come back and edit any of them later — the trigger keeps a full edit history so you can see how a rule evolved.

---

## ⏱️ When triggers check

Pick the cadence that matches the kind of question your rule is asking:

- **On every change** — the wiki re-checks the rule each time someone (or an agent) edits a page in scope. Best for *"when X changes"* or *"when a new Y appears"* rules.
- **On a schedule** — the wiki checks at a regular cadence (e.g. every Monday at 9am), regardless of whether the page moved. Best for *"is this still stuck after a week?"* or recurring *"weekly digest"* style rules.

---

## 🔍 Inspecting your triggers

Two places to look, depending on what you're trying to learn:

- **Triggers** (sidebar) — the full list of every trigger, its scope, and when it last fired. Click one to edit the rule, change its schedule, or open its **edit history** to see how the rule has changed over time.
- **Events** (sidebar) — every actual fire across all your triggers, with the message that was sent and the reasoning behind the decision. This is where you go to figure out why a rule is too eager, too quiet, or firing on the wrong thing.

---

## ✍️ Writing rules that work

Treat the rule like instructions to a sharp but literal assistant — be specific about what counts as the event, and what doesn't.

| Works well | Avoid | Why |
|------------|-------| --- |
| "If the status field changes from open to done" | "Every Monday summarize this page for me" | The trigger should be set to run on a page change or on Monday, it shouldn't be *described* to run on Monday. |
| "If a new bullet is added under `## Risks`" | "If the last 3 versions introduced more than 5 bullets points" | While the wiki has the entire edit history of every page, the triggers only see the last change. |
| "If new changes indicate we are behind schedule" | "If this page falls out of sync with /sprint-planning.md" | If sprint-planning.md is not within the scope of the trigger, it won't be able to access it. If you set your scope to be a directory with both files, it will work. |

Vague rules give vague firing. If you're not getting what you expected, the history view will usually tell you why — and you can tighten the rule in place.

---

## 📜 Events

Every trigger fire is logged as an **event**: the change it reacted to, the rule that matched, the message that went out, and the reasoning behind the decision. Browse them in the **Events** tab of the sidebar to audit what's happening across your workspace — useful for spot-checking new triggers and for understanding why a notification did (or didn't) arrive.
