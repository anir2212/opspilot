# Chat adapters: local setup and limits

The API delivers only when `OPSPILOT_CHAT_DELIVERY=1`. The default is `0`; no live message is sent during tests. Keep tokens in ignored `.env`, never in shell history or committed files.

## Slack

Create a Slack app with Socket Mode, a bot token, an app token, and access to a channel. Grant message and history permissions needed by your own workspace and subscribe to message events; configure the three Block Kit action IDs in the app. Set `OPSPILOT_SLACK_BOT_TOKEN`, `OPSPILOT_SLACK_APP_TOKEN`, and `OPSPILOT_SLACK_CHANNEL`. Run `make slack` as a separate local process. The API's `publish_incident` posts one message per persisted fingerprint and stores the returned channel and thread timestamp. Replies in that thread call the existing OpsPilot follow-up endpoint and return its cited answer. The buttons acknowledge the local incident or draft a ticket/change request. Drafts require separate approval in the web console before the mock ITSM changes.

## Microsoft Teams

The adapter uses the current Microsoft Teams Python SDK 2.1 rather than the retired Bot Framework Python SDK. `make teams-dev` starts a loopback-only bot on port 3978 with unauthenticated requests **for the local Microsoft 365 Agents Playground only**. The SDK registers `/api/messages`; `incident <fingerprint>` retrieves a persisted OpsPilot thread and sends an Adaptive Card. Follow-up messages in that conversation call the same API. Card actions acknowledge or create pending drafts. Use the [official Teams SDK quickstart](https://learn.microsoft.com/en-us/microsoftteams/platform/teams-sdk/getting-started/quickstart) to run the local playground.

For a real tenant, register an app and configure `CLIENT_ID`, `CLIENT_SECRET`, and `TENANT_ID` as described in [Teams app authentication](https://learn.microsoft.com/en-us/microsoftteams/platform/teams-sdk/essentials/app-authentication). Leave `OPSPILOT_TEAMS_DEV_UNAUTH=0`. A proactive delivery requires a previously established `OPSPILOT_TEAMS_CONVERSATION_ID`, a running authenticated bot, `OPSPILOT_TEAMS_BOT_URL`, and a shared local `OPSPILOT_TEAMS_INTERNAL_TOKEN`. The API calls the bot's token-protected `/internal/deliver`; the bot uses `teams.send` and persists a chat-thread mapping. A Teams tenant, channel installation, and external delivery credentials were unavailable in this workspace, so no real Teams post or emulator recording is claimed.

## Design caveats

The Slack publish path checks for an existing thread before posting; concurrent workers could still race before the database insert. Production needs an outbox with a unique reservation and retry state. The Teams bot tracks one incident mapping per conversation only loosely; an enterprise bot should persist a conversation-to-incident selection and handle multiple active incidents explicitly. The local token for the internal Teams route protects the route, but use service authentication and network policy in a deployment. Neither chat adapter can directly approve an OpsPilot pending action.
