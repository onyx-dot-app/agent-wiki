# Outbound email setup

Agent Wiki sends email through one SMTP account that an admin configures at `/admin/email`. Trigger notifications, verification links, and notification emails all go through it. Nothing sends until a host and from address are saved.

The form takes five values: host, port, username, password, and the from address. Every mail provider exposes these. Pick your section below, then use the "Send a test email" button on the same page. It reports success or the exact failure inline.

The password is stored encrypted and is never shown again after saving. Leave the password field blank on later edits to keep the stored value.

## Google Workspace

Use a dedicated service mailbox, not a person's account.

1. In [admin.google.com](https://admin.google.com) go to Directory, then Users, then Add new user. Create something like `wiki@yourdomain.com`. This uses one Workspace seat.
2. Sign in as that user once to finish onboarding.
3. Enable 2-Step Verification for it at [myaccount.google.com/security](https://myaccount.google.com/security).
4. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Copy the 16-character value.

Settings: host `smtp.gmail.com`, port `587`, username and from address both the mailbox address, password the app password.

Gmail rewrites the From header to the authenticated mailbox, so the from address should match the username. Per-mailbox sending is capped by Google (on the order of 2,000 messages per day). If your org enforces security policies, an admin may need to allow app passwords for the account.

## Microsoft 365

1. Create a mailbox such as `wiki@yourdomain.com` in the Microsoft 365 admin center.
2. SMTP AUTH is disabled by default on many tenants. An admin enables it for just this mailbox: Users, Active users, select the mailbox, Mail, Manage email apps, check Authenticated SMTP.

Settings: host `smtp.office365.com`, port `587`, username and from address both the mailbox address, password the mailbox password.

Microsoft has been tightening basic authentication over time. If sends fail with an auth error despite correct credentials, check the tenant's security defaults and Exchange transport settings.

## Amazon SES

1. Verify your sending domain in the SES console (DKIM records).
2. Create SMTP credentials in the SES console under SMTP settings. These are distinct from your IAM keys.
3. If the account is in the SES sandbox, request production access, or verify each recipient while testing.

Settings: host `email-smtp.<region>.amazonaws.com` (for example `email-smtp.us-east-1.amazonaws.com`), port `587`, the generated SMTP username and password, and any from address at your verified domain.

## SendGrid

1. Create an API key with Mail Send permission.
2. Verify a sender identity or your domain in SendGrid.

Settings: host `smtp.sendgrid.net`, port `587`, username the literal string `apikey`, password the API key, from address your verified sender.

## Internal relay or smarthost

If your network has an SMTP relay that accepts mail from internal services without authentication, set the host and port and leave username and password blank. Agent Wiki skips SMTP login when both are empty. Port `465` uses implicit TLS. Any other port negotiates STARTTLS.

## Deliverability

Send from a domain whose SPF and DKIM records cover the provider you chose. Workspace and M365 domains already have this if the domain is your mail domain. For SES and SendGrid the verification step above sets it up. Without it, mail lands in spam or gets rejected.
