# Discord Media Approval Bot

A Discord bot designed to streamline the process of approving media (images/videos) before potential further use, such as uploading to a shared gallery like Lychee.

## Overview

This bot allows users with a specific "submitter" role to select messages containing media using either a context menu command ("Submit Media") or a prefix command (`>>upload`). The bot then repackages the media and relevant context (original author, source message link, ticket number derived from the channel name) into a new message in a designated "pending approval" channel. Users with an "approver" role can then use buttons on this pending message to approve, approve without gallery upload, or deny the submission. Denials require a reason entered via a modal popup. Approved media URLs are logged in a database to prevent duplicates, and optionally uploaded to a configured Lychee instance.

## Features

*   **Submission Methods:**
    *   Right-click message -> Apps -> "Submit Media" (Context Menu)
    *   Reply to a message with `>>upload` (Prefix Command - configurable prefix)
*   **Approval Workflow:**
    *   Submissions sent to a dedicated pending channel.
    *   Embed displays submitter, original author, source link, ticket number, etc.
    *   Buttons for "Approve", "Approve (Skip Gallery)", and "Deny".
    *   Denial requires a mandatory reason via a modal popup.
    *   Processed messages are updated (title, color, status field) and buttons disabled.
*   **Lychee Integration (Optional):**
    *   Uploads approved media to a configured Lychee instance and album using the Lychee API.
    *   Gracefully handles API errors (attempts URL import, falls back to download/upload).
    *   "Skip Gallery" button allows approval without uploading.
    *   Button disabled if Lychee integration is globally disabled in config.
*   **Database & Tracking:**
    *   Uses SQLite to store URLs of already approved media, preventing duplicates.
    *   Tracks basic statistics for the number of approved uploads per original author.
*   **UI/UX:**
    *   Embeds used for clear presentation of submission details.
    *   Persistent buttons (`discord.ui.View`) work even after bot restarts.
    *   Avoids duplicate file attachments when a single image is successfully embedded.
    *   Ephemeral feedback messages for commands and button clicks.

## Prerequisites

*   **Python:** 3.8 or higher recommended.
*   **pip:** For installing Python packages.
*   **git:** For cloning the repository.
*   **Discord Bot Account:** You need to create a bot application in the [Discord Developer Portal](https://discord.com/developers/applications).
*   **Lychee Instance (Optional):** If using Lychee integration, you need a running Lychee instance with API access enabled.

## Setup & Installation

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/4T88/ticket-media-manager
    cd https://github.com/4T88/ticket-media-manager
    ```

2.  **Create a Virtual Environment (Recommended):**
    *   **Linux/macOS:**
        ```bash
        python3 -m venv .venv
        source .venv/bin/activate
        ```
    *   **Windows (cmd/powershell):**
        ```bash
        python -m venv .venv
        .\.venv\Scripts\activate
        ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

Configuration is handled via **Environment Variables** (recommended for security) or by directly editing the default values in the `CONFIGURATION SECTION` at the top of the Python script (`media_bot.py` or similar).

**CRITICAL SECURITY WARNING:**
**NEVER commit your actual Bot Token or API Keys directly into your code or to public repositories like GitHub.** Use Environment Variables or ensure any local secrets file (like `.env`) is listed in your `.gitignore` file.

### Environment Variables (Recommended Method)

Set these variables in your operating system or hosting environment before running the bot.

*   **`DISCORD_BOT_TOKEN` (REQUIRED):** Your Discord bot token from the Developer Portal.
*   **`COMMAND_PREFIX` (Optional):** The prefix for commands (Default: `>>`).
*   **`GUILD_ID` (REQUIRED):** The ID of the Discord server (guild) where the bot will operate.
*   **`ALLOWED_ROLE_ID` (REQUIRED):** The ID of the role that can *submit* media.
*   **`PENDING_CHANNEL_ID` (REQUIRED):** The ID of the channel where submissions await review.
*   **`APPROVAL_ROLE_ID` (REQUIRED):** The ID of the role that can *approve/deny* submissions.
*   **`MAX_FILES_PER_MESSAGE` (Optional):** Max files per submission (Default: `10`).
*   **`DATABASE_FILE` (Optional):** Name for the SQLite DB file (Default: `approvals.db`).
*   **`LYCHEE_ENABLED` (Optional):** Set to `true` to enable Lychee uploads (Default: `false`).
*   **`LYCHEE_API_URL` (Required if `LYCHEE_ENABLED=true`):** Base API URL of your Lychee instance (e.g., `https://gallery.example.com/api`).
*   **`LYCHEE_API_KEY` (Required if `LYCHEE_ENABLED=true`):** Your Lychee API key.
*   **`LYCHEE_ALBUM_ID` (Required if `LYCHEE_ENABLED=true`):** The ID of the target Lychee album.

**Example (Linux/macOS):**
```bash
export DISCORD_BOT_TOKEN="YOUR_SECRET_TOKEN"
export GUILD_ID="123456789012345678"
export ALLOWED_ROLE_ID="123456789012345679"
# ... set other variables ...
python media_bot.py
