# Connecting Workflows

Send trigger fires to **Zapier**, **n8n**, **Make**, **Pipedream**, or any HTTP endpoint, so a change in the wiki can kick off a workflow you already run somewhere else.

This is the other half of trigger destinations. A Slack or email destination sends a written message for a person to read. A workflow destination sends a structured event instead, machine-readable JSON your automation can branch on. You provide the trigger, the platform provides the rest.

---

## What we send

When a trigger fires, the wiki POSTs one JSON event to the URL you register. The body is the same for every platform:

```json
{
  "event": "trigger.fire",
  "trigger_id": "trg_abc123",
  "trigger_kind": "delta",
  "doc_path": "Projects/Roadmap.md",
  "sha": "9f3c1a…",
  "change_kind": "edit",
  "fired_at": "2026-07-09T01:00:00Z",
  "actor": "Nik",
  "summary": "The rendered description of what changed and why it matters.",
  "reason": "the trigger's condition that matched",
  "routing_tag": "roadmap",
  "fields": { "team": "growth" }
}
```

`summary` is the human-readable description of the change. `routing_tag` and `fields` come from the endpoint you set up, so a receiver can tell triggers apart when several point at one URL. The doc body and the diff are not included.

Every POST carries an **`X-AgentWiki-Signature`** header. It is an HMAC-SHA256 of the exact body under your endpoint's signing secret, so a receiver can confirm the call came from us. Verifying it is optional but recommended for anything that acts on the payload.

---

## Register the endpoint

You set a webhook up once and reuse it across triggers, the same as an email address or a Slack channel.

1. Open **Settings → Connectors** and add a **Webhook** endpoint.
2. Give it a name you'll recognize, paste the URL from your platform (below), add an optional routing tag, and hit **Add**. A signing secret is generated for you.
3. Hit **Test**. This POSTs a sample so the platform can learn the field shape before a real trigger points at it.
4. In the New Trigger panel, pick the endpoint under **Then Send**.

---

## 🔗 Zapier

Zapier calls its inbound webhook a **Catch Hook**.

1. In Zapier, create a Zap. For the trigger, choose **Webhooks by Zapier** and the **Catch Hook** event.
2. Zapier shows a **Custom Webhook URL** like `https://hooks.zapier.com/hooks/catch/…`. Copy it.
3. Register it as a webhook endpoint (above) and hit **Test**.
4. Back in Zapier, click **Test trigger**. The sample event appears with every field, and you can map `summary`, `doc_path`, `actor`, and the rest into your action steps.

Note: Catch Hook is a Zapier Premium feature, so Zapier may prompt you to start a trial.

## 🔗 n8n

Use the **Webhook** node.

1. Add a **Webhook** node as the workflow's trigger. Set the method to **POST**.
2. Copy the node's **Production URL** (or the Test URL while you build).
3. Register it as a webhook endpoint and hit **Test**, then run the node once so n8n captures the sample.
4. Wire the captured fields into the rest of your workflow.

## 🔗 Make

Use a **Custom webhook** trigger.

1. Add the **Webhooks → Custom webhook** module and create a new hook.
2. Copy the address Make generates.
3. Register it as a webhook endpoint and hit **Test** so Make can "determine the data structure."
4. Map the fields into the following modules.

## 🔗 Pipedream

Use an **HTTP / Webhook** trigger.

1. Create a workflow with the **New HTTP / Webhook Requests** trigger.
2. Copy the endpoint URL Pipedream assigns.
3. Register it as a webhook endpoint and hit **Test** to generate a sample event.
4. Reference `steps.trigger.event.body` in the following steps.

> The exact button and field names for n8n, Make, and Pipedream change over time. If a step here does not match what you see, check that platform's current docs. The wiki side (register, send test, map fields) is the same for all of them.

---

## Verifying the signature

For anything that acts on the payload, confirm the `X-AgentWiki-Signature` header before trusting the body. Recompute the HMAC over the raw request body with your endpoint's signing secret and compare.

```python
import hmac, hashlib

def is_from_agent_wiki(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Compute the HMAC over the exact bytes you received, before any JSON re-serialization, or the digest will not match.
