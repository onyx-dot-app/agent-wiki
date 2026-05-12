# Setup

## 1. 👋 Become an admin

The first person to sign up becomes the **workspace admin** automatically.

**Not an admin?** The next two steps need admin access. Ask your workspace admin to run through them, or ask them to promote you so you can do it yourself.

> Once an admin exists, the workspace won't let you accidentally lock yourself out by removing the last one.

---

## 2. 🤖 Connect an LLM provider

The wiki uses an LLM to evaluate triggers, help with edits, and power the chat assistant. You'll need to give it credentials for one provider.

1. Open your **profile** at the top of the left sidebar.
2. Choose **Admin**, then open **LLM configuration**.
3. Pick a provider (Anthropic, OpenAI, Gemini, or Ollama for local models), paste your API key, and save.

You can switch providers later from the same screen — the wiki picks up the change immediately.

---

## 3. 👥 Invite your teammates

By default only people you've explicitly allowed can sign in.

1. Profile (left sidebar) → **Admin** → **Users**.
2. Add a teammate's email to the allow list.
3. Share the sign-up link with them. When they sign up, they'll land in the workspace as a regular member.

Want to give someone admin rights? You can promote them from the same page after they've signed up.

---

## 4. 📝 Add your own knowledge

The pages you're reading right now are starter content — replace them with **your team's actual notes, projects, and references**. That's where the wiki starts earning its keep.

A good first move:

1. Open **Wiki** from the left sidebar.
2. Hit **+ New folder** and create a top-level area that matches how your team thinks (e.g. `projects/`, `runbooks/`, `customers/`).
3. Click **+ New document** inside it and start writing. A few **starter templates** are available to help you draft a page quickly — or skip them and write something completely custom from scratch.
4. When you're comfortable, **delete the onboarding pages** you no longer need straight from the wiki navigation — no need to open them. The wiki keeps full history, so nothing is truly lost.

> 💡 Tip: you can use **filler sections** to let the wiki capture updates over time. For example, drop a section like:
>
> ```
> ## Decision Log
> Below is a list of all critical decisions made about the project and the timestamps for those decisions.
> ```
>
> …and the wiki will populate it as decisions land, no manual bookkeeping required.

---

You're set. Continue to [Triggers and Events](Features/Triggers%20and%20Events.md) to make the wiki reactive, or jump straight to the [AI Wiki Helper](Features/AI%20Wiki%20Helper.md) and start asking questions.
