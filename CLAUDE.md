# HiØ Job Alert — Project Specification

## 1. Project Goal

Build a complete, minimal, reliable job-alert application that monitors the Høgskolen i Østfold (HiØ) job listings page:

https://www.hiof.no/om/jobb/

The application must check the page every day at about 12:07 and 14:07 Norwegian local time (not on the full hour, where GitHub often drops scheduled runs) and notify me when a new job listing is discovered.

Notifications must be sent to:

- A Discord channel using a Discord Incoming Webhook.
- My mobile phone using ntfy push notifications.

The project should be free to run within available free-tier limits, require minimal maintenance, and not require my computer to be running.

## 2. Implementation Principles

**Keep it simple. Less is more.**

- Implement the complete project in one pass. Do not stop after planning, providing pseudocode, or explaining what should be done.
- Write only the code necessary to satisfy the requirements.
- Prefer straightforward, readable, maintainable code with clear, descriptive names.
- Avoid unnecessary abstractions, classes, dependencies, configuration options, files, and features.
- Do not overengineer the architecture or introduce infrastructure that is not required.
- Do not build a frontend, dashboard, API server, or separate bot.
- Use a small number of focused tests for essential behavior. Do not create excessive tests.
- Keep documentation concise and practical.
- Make reasonable technical decisions independently. Do not ask questions unless a missing detail genuinely blocks implementation.
- If an implementation detail is uncertain, investigate it before making assumptions.
- Prioritize correctness and reliability over reducing the number of lines of code.
- Complete all work that can be done in the available environment before requesting manual intervention.
- Do not claim that code, tests, notifications, website access, or deployment succeeded unless actually verified.

The goal is the smallest maintainable implementation that genuinely works, not the smallest implementation regardless of reliability.

## 3. Technology Stack

Use the following unless there is a concrete technical reason to choose an alternative:

- **Python 3.12 or newer:** Application logic.
- **GitHub Actions:** Scheduled execution and deployment.
- **requests:** HTTP requests.
- **BeautifulSoup:** HTML parsing, if needed.
- **Discord Incoming Webhook:** Discord notifications.
- **ntfy:** Mobile push notifications.
- **JSON:** Persistent job and notification state.
- **pytest:** A small number of essential automated tests.

Prefer official APIs or feeds over HTML scraping when available.

Use browser automation such as Playwright only if the listings genuinely cannot be retrieved through a simpler method.

Avoid paid services, unnecessary dependencies, Docker, and external infrastructure.

## 4. Investigate the HiØ Website First

Before implementing the scraper, investigate how job listings are delivered by:

https://www.hiof.no/om/jobb/

Previous direct retrieval attempts returned HTTP 403 Forbidden. Verify actual access before choosing an implementation.

Investigate in this order:

1. Check for an official API, feed, or structured data source.
2. Determine whether the listings are available in the returned HTML.
3. Check whether JavaScript loads the listings dynamically.
4. Use browser automation only if necessary.

Do not invent API endpoints, HTML selectors, job fields, or website behavior.

Do not attempt to bypass access controls. If reliable access is unavailable, explain the actual blocker and complete the remaining project components without pretending the scraper works.

Handle HTTP errors, timeouts, unexpected HTML, and empty results safely.

Do not overwrite valid stored state when scraping fails or returns suspiciously incomplete data.

## 5. Job Data

Extract the following information for each listing:

- Job title.
- Direct URL to the listing.
- Stable job ID, if available.
- Publication date.
- Publication time, if the source provides it.
- Department or location, if available.

Publication timestamps must come from the source. Never invent a publication date or time.

If only a date is available, display only the date.

Track the discovery timestamp separately from the original publication timestamp.

Identify jobs using a stable source ID where possible. Otherwise, use a normalized listing URL.

## 6. New Job Detection

The application must remember previously discovered listings across executions.

### First successful run

- Retrieve the current listings.
- Store existing listings as the initial baseline.
- Do not notify me about existing jobs.

### Subsequent runs

1. Retrieve the current listings.
2. Compare them with persistent state.
3. Identify previously unseen listings.
4. Send notifications for new listings.
5. Record notification delivery status.
6. Persist the updated state safely.

Do not notify repeatedly about the same listing.

Handle missing metadata, changed listings, duplicate entries, and temporary website failures sensibly.

A failed or invalid scrape must never be interpreted as proof that all jobs have disappeared.

## 7. Notification Requirements

Send a notification to both Discord and ntfy for each new listing.

Use this format:

**New Job — Høgskolen i Østfold**

Title: [Job title]

Published: [Actual publication date and time, if available]

Link: [Direct job URL]

Make the URL clickable wherever supported.

Keep notifications concise and readable on a mobile screen.

Send a short daily status message to both channels: "all OK" after every run (silent on the phone when OK), and a warning on every failed run, so a missing status message shows that the check did not run. Runs are started by cron-job.org via workflow_dispatch because GitHub never triggered the `schedule`.

Track Discord and ntfy delivery independently.

If one channel succeeds and the other fails, retry the failed channel without unnecessarily resending the successful notification.

Use a limited number of retries with reasonable delays for transient failures.

Acknowledge that exactly-once delivery cannot be guaranteed across external services if a notification is accepted but its response is lost. Avoid introducing complex delivery infrastructure solely to address this edge case.

## 8. Persistent State

GitHub Actions runs in temporary environments. Changes to local files do not automatically persist between executions.

Choose the simplest safe persistent-storage solution.

Preferred option: a version-controlled JSON state file in a private GitHub repository, provided updates can be implemented safely.

Requirements:

- Preserve job history between runs.
- Preserve per-channel notification status.
- Avoid corrupting state during interrupted executions.
- Avoid overwriting concurrent updates.
- Use narrowly scoped credentials for any repository writes.
- Never store secrets in the state file.
- Do not commit generated logs, credentials, or temporary files.

If another free solution is materially simpler or more reliable, choose it and briefly explain why.

Do not introduce a database server unless genuinely necessary.

## 9. Scheduling and GitHub Actions

Use GitHub Actions for automated execution.

Requirements:

- Run daily at 12:07 and 14:07 in the `Europe/Oslo` time zone.
- Account for daylight-saving time.
- Prefer native time-zone-aware scheduling if supported by the selected GitHub Actions configuration.
- Do not silently use a fixed UTC schedule that shifts by an hour during part of the year.
- Include `workflow_dispatch` for manual execution.
- Use the minimum permissions required.
- Keep within available free-tier limits where possible.

Scheduled workflows may be delayed by GitHub. Do not promise execution at the exact minute.

## 10. Secrets and Configuration

Use GitHub Actions repository secrets for sensitive configuration.

Required secrets:

- `DISCORD_WEBHOOK_URL`
- `NTFY_TOPIC`

Add additional secrets only when necessary, such as an ntfy access token if authentication is configured.

Never hardcode credentials into source code or commit them to the repository.

Never print secrets in logs or include them in error messages.

Include a minimal `.gitignore` and `.env.example` only where useful.

The ntfy topic should be difficult to guess and treated as a secret. Do not use a publicly guessable topic for private notifications.

## 11. Project Structure

Use a small, understandable project structure, for example:

```text
hiof-job-alert/
├── .github/
│   └── workflows/
│       └── daily-check.yml
├── src/
│   ├── main.py
│   ├── scraper.py
│   ├── notifier.py
│   └── storage.py
├── tests/
│   └── test_core.py
├── data/
│   └── seen_jobs.json
├── requirements.txt
├── .gitignore
├── .env.example
└── README.md
```

This is a suggested structure, not a requirement to create every file. Simplify it if fewer files produce a clearer implementation.

Do not create empty modules, unnecessary directories, or files without a concrete purpose.

## 12. Essential Testing

Write only the tests needed to verify critical behavior.

Cover the following where applicable:

- Parsing job listings.
- Detecting a new listing.
- Registering existing jobs without notifying on the first run.
- Preventing duplicate notifications.
- Preserving state between runs.
- Handling failed scrapes without corrupting state.
- Tracking Discord and ntfy delivery independently.

Use mocked HTTP responses and test fixtures. Tests must not send real notifications or depend on the live HiØ website.

Run the tests and fix failures before declaring completion.

Validate the GitHub Actions configuration and check for accidental secrets or unnecessary files.

Do not create an extensive test framework or unnecessary test coverage for trivial code.

## 13. Documentation

Create a concise `README.md` containing only:

1. A brief explanation of what the project does.
2. A description of the project structure and the purpose of each file and folder.
3. The minimum setup steps for GitHub, Discord, ntfy, and repository secrets.
4. Instructions for running the application manually and checking its logs.
5. Any essential limitations.

Keep documentation short, accurate, and aligned with the actual implementation.

Do not create a separate technical report, extensive tutorial, changelog, or redundant documentation unless genuinely necessary.

## 14. Execution Instructions

Complete the project in one pass.

Work through the following sequence without stopping for unnecessary confirmation:

1. Investigate the HiØ website and determine the viable data source.
2. Choose the simplest suitable architecture.
3. Implement all necessary source files and configuration.
4. Implement persistent state and notification handling.
5. Configure the GitHub Actions workflow.
6. Write and run essential tests.
7. Verify the implementation and configuration as far as the environment permits.
8. Write the concise README.
9. Provide a short completion summary.

Use available tools to create, modify, and test actual files when possible.

If access to the website is blocked, do not fabricate successful results. Complete all independent components and identify precisely what remains blocked.

If GitHub credentials, webhook URLs, or manual account configuration are required, do not ask me to disclose secrets in chat. Complete the implementation first and provide the exact steps I must perform.

## 15. Definition of Done

The project is complete when:

- The application code is implemented.
- The scraper uses a verified and permitted retrieval method, or its genuine blocker is explicitly documented.
- New listings are detected without repeatedly notifying about existing jobs.
- Job history persists between executions.
- Discord and ntfy notifications are implemented with independent delivery tracking.
- The daily schedule uses the correct Norwegian local time and handles daylight-saving changes.
- Essential tests pass.
- The GitHub Actions workflow is configured and validated.
- The README accurately explains the files, setup, and operation.
- No unnecessary code, dependencies, documentation, or infrastructure remain.

Do not claim live deployment or successful delivery unless verified.

## 16. Final Response

After completing the implementation, provide a concise summary containing:

- What was created.
- The main technical decisions.
- Which tests and checks actually passed.
- Any genuine limitations or unresolved blockers.
- The exact manual steps required before the application can run automatically.

Do not repeat the full source code or README in the final response.

**Final priority: deliver the simplest maintainable implementation that meets the requirements and works reliably. Complete the work rather than merely describing it.**
