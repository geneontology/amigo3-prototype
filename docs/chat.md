# Agentic research chat

The `/chat` page connects to an ACP agent through the local FastAPI server. It
supports multi-turn sessions, streamed responses, Markdown and record links,
plans, tool input/output, explicit permission choices, cancellation, a session
list with switching and deletion, session/global approval policies, a workspace
file browser, and downloadable JSON transcripts. The existing browser links to it from its
main navigation. Chat is optional; search does not need an agent or Node.js.
[Optional GitHub login](login.md) gates chat while keeping public browsing available.

## Run with Claude

After building an AmiGO dataset as described in the README:

```bash
uv sync --extra chat
# Requires Node.js 22+; the adapter uses the Claude Agent SDK.
npm install -g @agentclientprotocol/claude-agent-acp
# Authenticate Claude using your existing Claude setup, or supply the provider's
# supported credentials in the server environment.
uv run --extra chat amigo3 serve --backend duckdb --chat-command 'claude-agent-acp'
```

Open http://127.0.0.1:8030/chat. The same option works with the Solr backend:
chat always queries the integration DuckDB for the **server's pinned build**,
including when `published.json` points to a different build from `current.json`.
The adapter runs on the server machine, with that user's provider credentials.
Provider charges and authentication belong to the chosen agent.

`--chat-command` accepts an executable and arguments, parsed without a shell.
For another ACP v1 stdio agent, replace the command. Use absolute paths for
file arguments, because agents start in a temporary working directory. The
browser cannot choose executables, working directories, or MCP servers.
Authentication methods advertised by an agent are shown if session creation
fails; terminal-based authentication may need to be completed outside the page.

## Protocol and tools

```text
Chat page ← ACP JSON-RPC / WebSocket → FastAPI bridge
                                         ↕ newline-delimited ACP / stdio
                                      ACP agent
                                         ↕ MCP / stdio
                                 AmiGO DuckDB tool server
                                         ↓
                                 pinned integration.duckdb
```

Each browser tab keeps a reconnect key in sessionStorage. Its server session owns
one adapter process and one event history, independently of the WebSocket. `initialize`,
`authenticate`, `session/new`, `session/prompt`, `session/cancel`, agent updates,
and permission request/reply messages cross the bridge. The bridge supplies the
session working directory and the `amigo3-duckdb` MCP server. The UI does not
advertise client filesystem or terminal capabilities. Permission requests are
shown as explicit choices from the agent. By default, every request waits for a
decision; users can enable automatic approval for a session or globally.
Only one prompt can be in flight. Navigation and reload detach the WebSocket;
the agent can keep working, and pending permissions follow the selected policy.
Reconnecting restores messages, plans, tool activity, pending permission choices,
and the current turn state, then reattaches to the same process. New session keeps
the previous conversation available in the Sessions sidebar. Explicit deletion,
server shutdown, or 30 minutes continuously disconnected terminates the adapter
process group and removes its temporary directory.

The MCP server can also be used directly by other agents:

```bash
uv run --extra chat python -m amigo3.chat_tools --build /absolute/path/to/data/builds/BUILD_ID
```

It provides:

- `database_schema(table?)`: table and column discovery, optionally for one table.
- `query_sql(sql, limit=100)`: one SELECT or WITH query, up to 500 rows and 100KB
  of row data, with column types, SQL, build ID, and explicit truncation.
- `build_manifest()`: counts, source versions, and checksum provenance.
- `ui_skill(name="ui-links")`: the packaged UI integration skill for constructing
  enrichment, record and search links, with parameter definitions and examples.

SQL runs against a read-only attachment using a private DuckDB connection.
External access is disabled, configuration is locked, queries are interrupted
after 15 seconds, and memory/threads are limited. Writes, multi-statement SQL,
file/URL reads, and extension loading are rejected. These restrictions apply to
**the database tools**, not to all capabilities of the selected ACP agent.
Agents can have their own filesystem, shell, or network tools; their permissions
remain governed by the agent and its ACP permission requests.

## Moving between chat and components

The agent is instructed to read the `ui-links` skill before offering links to UI
operations. The skill lives in `src/amigo3/skills/ui-links.md` and is served through
MCP, so it works with ACP agents without relying on adapter-specific skill discovery.
It describes how to carry real query results into enrichment, including organism,
background, evidence and method choices, URL encoding, and handling incomplete or
oversized gene lists. Links use the server's pinned build, not historical snapshots.

For example, `/enrichment?genes=GENE1%2CGENE2&species=human&aspect=F&run=0`
prefills the form for review. The user clicks **Run enrichment** to execute it.
Existing links without `run=0` retain their automatic execution behavior.

**Chat about this** appears on annotation search results, evidence dialogs and
gene-page annotations when chat is enabled and the user can access it. With GitHub
login configured, it requires sign-in; local installations without login keep their
existing chat access. The action opens a fresh session with an editable draft,
leaving previous conversations and drafts intact. No prompt is sent until **Send**.

The handoff URL is `/chat?annotation={id}&build={build_id}&from={page_path}&breadcrumbs={json}`,
with percent-encoded values. Chat fetches the annotation from the record API and
includes its full record, build ID, source page URL (including filters/hash), and
breadcrumbs in the draft. A stale build or unavailable record produces an error;
external source-page URLs are rejected. Navigation context is treated as data.
After the draft is saved, the handoff parameters are removed from the current URL,
so reload restores the same session and draft. At the session limit, delete a
conversation and reload the handoff URL to try again.

## Approvals and workspace files

The Sessions sidebar highlights conversations waiting for approval, including the
number of pending requests and the first tool's title. Waiting sessions sort to
the top. The conversation status also says **Waiting for approval**. Background
session status refreshes every five seconds.

**All sessions** sets the default to **Ask for approval** or **Allow all**.
**This session** can inherit that setting, always ask, or allow all. A session
override takes priority over the global setting. The permission card also has an
**Allow all in this session** shortcut. Changing to Allow all immediately resolves
pending requests as well as future ones, including when a session is detached.
These policies run on the server and survive navigation, but reset when the
server restarts. Global defaults apply to new sessions too. With GitHub login,
“All sessions” applies only to chats belonging to the current login session.

Automatic approval selects an allow option actually offered by the agent,
preferring `allow_once` so switching back to Ask takes effect on future requests.
If only `allow_always` is offered, the agent may remember that grant; switching
back cannot revoke permissions already remembered by the agent. Requests with no
allow option remain pending. Stop cancels outstanding requests. Each decision is
recorded in the transcript, with automatic approvals labeled by policy scope.
Allow all affects ACP tool approvals, including shell/file/network requests if
the agent offers them; the DuckDB tools' read-only restrictions still apply.

Each session already has its own temporary working directory, passed to the ACP
agent as `cwd`. **Workspace files** shows its actual path and files, including
subfolders. It refreshes after tool activity and completed turns, with a manual
Refresh button. Select a file to preview plain text or download it. Text previews
are capped at 200 KB, downloads at 10 MB, and directory listings at 500 entries.
Binary files can be downloaded; HTML is displayed as text. The viewer is
read-only, rejects traversal, and does not follow symlinks or open special files.
This confines the viewer, not the agent's own filesystem tools.

Workspace files share the session lifetime: download anything to keep before
deleting the session, restarting the server, or leaving it disconnected for
30 minutes. The viewer is not a durable artifact store.

The local HTTP API adds `PUT /api/chat/permissions` (`mode: ask|allow`) and
`PUT /api/chat/sessions/{key}/permissions` (`mode: inherit|ask|allow`), both with
matching-Origin checks. Read-only file routes are
`GET /api/chat/sessions/{key}/files?path=.` and
`GET /api/chat/sessions/{key}/file?path=relative/path&download=false`.

## Existing UI evaluation

Reviewed for this implementation on 2026-10-10:

| Candidate | What can be reused | Fit here |
| --- | --- | --- |
| [acp-components](https://github.com/zvzuola/acp-components) | React workbench/chat, tool calls, permissions, and a framework-independent ACP core | Closest component library; adopting its UI would introduce React and a frontend build into the existing plain-JavaScript app. Worth revisiting for a larger workbench. |
| [ACP UI](https://github.com/formulahendry/acp-ui) | Full desktop/mobile/web client, with remote ACP over WebSocket | Useful standalone client; not a drop-in widget for the existing page. Its browser deployment would need a same-origin transport arrangement here. |
| [assistant-ui ExternalStoreRuntime](https://www.assistant-ui.com/docs/runtimes/custom/external-store) | React chat components with custom runtime state | A possible rendering layer, but ACP lifecycle and permission handling still need an adapter. |
| [claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp) | Claude Agent SDK exposed through ACP, including client MCP servers | Selected as the documented agent adapter, rather than building a provider-specific agent loop. |

### Agentic UI patterns

The follow-up review uses the [ACP tool-call and permission contract](https://agentclientprotocol.com/protocol/v1/tool-calls),
[Zed's tool permission controls](https://zed.dev/docs/ai/tool-permissions), and the
[acp-components workbench architecture](https://github.com/zvzuola/acp-components/blob/main/docs/ARCHITECTURE.md)
as references. Their patterns informed the following scope:

| Pattern | AmiGO implementation |
| --- | --- |
| Session attention/status | Waiting badges, pending counts and tool titles, waiting-first ordering |
| Permission policy and decision history | Global default, session overrides, manual choices, visible automatic decisions |
| Agent progress and interruption | Existing streamed answers, plans, tool details and Stop control |
| Workspace inspection | Real session directory, folder navigation, text previews and downloads |
| Conversation continuity | Existing session list, reconnect/replay, independent drafts and transcript export |

The integration keeps the existing frontend stack. `/api/chat/acp` carries
ACP JSON-RPC objects without a bespoke chat-event translation, leaving a path to
replace the thin UI with ACP components later. Third-party clients must respect
the bridge's supported methods. Plain ACP connections without a `connection`
query parameter retain the ephemeral connection lifecycle. The integrated page
uses `amigo/restore`, `amigo/user_prompt`, `amigo/permission_resolved`, and `amigo/close` bridge extensions for
reconnection; native ACP session loading/listing, terminal access, filesystem
capabilities, and agent mode/model controls
are not implemented. This is not a claim of full interoperability with every UI.

Protocol references: [initialization](https://agentclientprotocol.com/protocol/initialization),
[session setup and MCP servers](https://agentclientprotocol.com/protocol/session-setup),
[tool calls and permission outcomes](https://agentclientprotocol.com/protocol/tool-calls).

## Current boundaries

This is a local agent integration. Optional GitHub login separates chat state by
login session; it does not isolate agent processes from the server account. The CLI requires a loopback host when
chat is enabled. The bridge checks loopback peers, local Host headers, and a
matching browser Origin. Do not place it behind a public proxy; authenticated
multi-user hosting and process isolation would require additional design.

Sessions and their event histories are held in server memory, with a 30-minute
grace period after disconnect. Back navigation, reload and temporary connection
loss reattach to the same agent; unsent drafts are kept in the tab as well.
Starting a new session or switching to another detaches the previous conversation
without interrupting its turn. Drafts are saved separately for each session.
The sidebar shows automatic titles from the first user question, activity status,
message counts, and last update times. Sessions open in another tab are marked
and cannot be attached twice. Delete explicitly stops the agent and removes its
transcript. Server restarts and grace period expiry clear the session; there is no disk-backed session history. Up to
eight retained sessions are supported, with a 16MB transcript limit per session.
`GET /api/chat/sessions` lists metadata and `DELETE /api/chat/sessions/{key}` removes
a session. Both are restricted to the local host; deletion also requires a
matching Origin. Without login this is a single-user list. When GitHub login is configured, it
contains only the current login session’s chats (not durable account history).
The transcript download records user messages and
received ACP events (including tool results), so it can contain research data.
Agent-rendered HTML and remote images are disabled in Markdown. This first UI
accepts text prompts; attachments, thought rendering, and interactive terminals
are outside its current scope.

## Verification

```bash
uv run --extra chat pytest
uv run ruff check .
uv run ruff format --check .
uv run --extra chat python scripts/check_chat_browser.py
```

The chat tests cover real MCP stdio calls against a fixture DuckDB, SQL boundaries
and timeout, ACP subprocess streaming and permissions, follow-up turns,
cancellation, reconnection to the same process with pending permissions, grace
period expiry, shutdown cleanup, cross-origin rejection, and Markdown safety. The browser smoke test starts its own fixture server and deterministic
ACP agent; it does not require provider credentials or make model calls. It
checks answer-link navigation followed by Back, reload, unsent draft restoration,
returning to a turn that is waiting for permission, switching between sessions
with independent drafts, and deleting both active and detached sessions. Approval
tests cover pending/background grants, session overrides, policy revocation,
new-session inheritance and decision replay. File tests cover traversal,
symlinks, special files, binary/large files, listing bounds, text preview safety,
folder navigation and downloads. Browser checks exercise both policy scopes and
the session shortcut without making model calls.

The implementation was also checked with `claude-agent-acp` 0.89.1: ACP v1
initialization and session creation with the AmiGO MCP configuration succeeded.
No live model prompt was sent in that check. Process-group cleanup currently
targets macOS/Linux.
