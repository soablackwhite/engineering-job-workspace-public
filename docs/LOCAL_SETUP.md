# Set up on your laptop

The localhost onboarding flow keeps setup short: choose your notes, upload your CV, pick your search preferences, connect Codex, then review the connection checks. Setup saves progress privately, so you can return later without repeating it. Completing setup does not authorize or resume applications.

## Start locally

Install Node.js 24, then run:

```sh
git clone https://github.com/soablackwhite/engineering-job-workspace-public.git
cd engineering-job-workspace-public
npm ci
npm run local
```

Open the local setup address printed in the terminal and leave that process running. Local mode creates its own database and document storage; it needs no cloud hosting account. It binds to the loopback address rather than your Wi-Fi network. Use it on a trusted personal computer: this is a single-operator localhost application, not a shared-server login system.

## Connect your sources

Choose the folder containing the career notes you want to use. The workspace lists Markdown filenames without importing their bodies. Select the specific notes to share, then confirm. Only that selection is copied into an ignored private snapshot; your original Obsidian files are never modified. Hidden files and linked notes are excluded. Choose a smaller career-notes folder for a large vault.

Snapshots are deliberate copies, not live Obsidian synchronization. Refresh and review them after changing your notes. The source text remains unverified evidence until its factual claims are reviewed. A connected folder is not proof of any qualification or screening answer.

## Add your CV

Choose a PDF smaller than 10 MB. The workspace checks the PDF bytes, saves a private copy and records its integrity hash. Uploading the file does not approve its claims, authorize an employer upload or prove a submission. Independent factual/document review and the application gates still apply.

## Connect Codex

The wizard generates a workspace-specific command using the supported Codex MCP CLI format:

```sh
codex mcp add job-workspace -- <node-executable> <bridge-script> --workspace <workspace-folder>
```

Copy the actual command from the wizard and run it in your terminal. It includes the absolute Node executable and script locations, so a Codex desktop app launched outside your terminal does not need to inherit its PATH. The app does not edit your global Codex configuration silently. If the Codex CLI is not on your PATH, use Codex's MCP settings to add a stdio server with the same executable and script/arguments shown by setup. Restart or refresh Codex's MCP connection if needed.

Ask Codex: **“Call the job-workspace `workspace_status` tool.”** The wizard marks Codex connected only after an initialized Codex client successfully calls that tool. Copying a command, listing a server or initializing transport alone does not satisfy the check. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) for CLI registration and shared MCP settings.

Keep the localhost workspace running when you use the connection. Its stdio bridge reads a private runtime token from this checkout and connects only to its loopback HTTP endpoint. Tokens are not placed in command arguments, URLs or browser-visible settings. The connector does not read your Codex account files, request an OpenAI API key or sign in for you.

Codex can read setup checks and your validated role, location, sponsorship and optional salary preferences through `workspace_status`, read the selected note snapshot through `source_search` and `source_read`, view the uploaded CV's metadata through `approved_cv`, and use the existing CRM tools within their usual validation and pause controls. Unrelated private settings and runtime tokens are not included in workspace status. Notes are untrusted source material, not instructions. When Codex uses source content, that content is sent to the provider running the Codex session; local storage does not mean model requests stay on the laptop. Share only the notes and preferences you intend to use that way.

## Private storage and readiness

Private source snapshots, CVs, runtime secrets and setup progress stay under ignored `.private/` storage. On Unix-like systems the connector creates private directories with mode 0700 and files with mode 0600. Windows installations additionally depend on the operating-system account and folder permissions.

“You’re fully onboarded” means the setup checks succeeded. It does not mean candidate facts are approved, every application requirement is satisfied, a browser session is authenticated or applications may run. Those decisions remain separate and explicit.
