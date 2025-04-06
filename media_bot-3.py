

import discord
from discord.ext import commands
import re
import traceback
import logging
import os
import sqlite3
from datetime import datetime
import aiohttp # For making asynchronous HTTP requests (Lychee API calls)
import asyncio # For managing concurrent tasks (Lychee uploads)

# ==============================================================================
# === CONFIGURATION SECTION ===
# Load settings from environment variables or use defaults.
# Using environment variables is STRONGLY recommended for sensitive data like tokens/keys.
# ==============================================================================

# --- Discord Bot ---
# REQUIRED: Your Discord Bot Token. Get this from the Discord Developer Portal.
# Environment Variable: DISCORD_BOT_TOKEN
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
# Prefix for bot commands (e.g., >>upload).
# Environment Variable: COMMAND_PREFIX
COMMAND_PREFIX = os.getenv('COMMAND_PREFIX', '>>')

# --- Discord IDs ---
# REQUIRED: The ID of the Discord Server (Guild) where the bot operates.
# Enable Developer Mode in Discord settings to easily copy IDs.
# Environment Variable: GUILD_ID
GUILD_ID = int(os.getenv('GUILD_ID', '123456789012345678')) # Replace placeholder
# REQUIRED: The ID of the Role allowed to INITIATE uploads (using commands).
# Environment Variable: ALLOWED_ROLE_ID
ALLOWED_ROLE_ID = int(os.getenv('ALLOWED_ROLE_ID', '123456789012345679')) # Replace placeholder
# REQUIRED: The ID of the Text Channel where new submissions are sent for review.
# Environment Variable: PENDING_CHANNEL_ID
PENDING_CHANNEL_ID = int(os.getenv('PENDING_CHANNEL_ID', '123456789012345680')) # Replace placeholder
# REQUIRED: The ID of the Role allowed to APPROVE/DENY submissions using buttons.
# Can be the same as ALLOWED_ROLE_ID if desired.
# Environment Variable: APPROVAL_ROLE_ID
APPROVAL_ROLE_ID = int(os.getenv('APPROVAL_ROLE_ID', '123456789012345681')) # Replace placeholder

# --- Functionality ---
# Maximum number of files to process from a single submitted message (Discord limit is 10).
# Environment Variable: MAX_FILES_PER_MESSAGE
MAX_FILES_PER_MESSAGE = int(os.getenv('MAX_FILES_PER_MESSAGE', '10'))
# Name of the SQLite database file to store approval info and stats.
# Environment Variable: DATABASE_FILE
DATABASE_FILE = os.getenv('DATABASE_FILE', 'approvals.db')

# --- Lychee Gallery Integration (Optional) ---
# Set to 'true' to enable uploading approved media to Lychee. Set to 'false' to disable.
# Environment Variable: LYCHEE_ENABLED
LYCHEE_ENABLED = os.getenv('LYCHEE_ENABLED', 'false').lower() == 'true'
# REQUIRED if LYCHEE_ENABLED: The base API URL of your Lychee instance (e.g., "https://gallery.example.com/api").
# Environment Variable: LYCHEE_API_URL
LYCHEE_API_URL = os.getenv('LYCHEE_API_URL', 'https://gallery.example.com/api') # Replace placeholder if enabled
# REQUIRED if LYCHEE_ENABLED: Your Lychee API Key (generate this in Lychee settings). Treat this like a password!
# Environment Variable: LYCHEE_API_KEY
LYCHEE_API_KEY = os.getenv('LYCHEE_API_KEY', 'YOUR_LYCHEE_API_KEY_HERE') # Replace placeholder if enabled
# REQUIRED if LYCHEE_ENABLED: The ID of the Lychee Album to upload approved media into.
# Find this in the Lychee interface (often visible in the URL when viewing the album).
# Environment Variable: LYCHEE_ALBUM_ID
LYCHEE_ALBUM_ID = os.getenv('LYCHEE_ALBUM_ID', 'YOUR_ALBUM_ID_HERE') # Replace placeholder if enabled

# --- Logging ---
# Level of detail for logging (e.g., INFO, DEBUG, WARNING, ERROR, CRITICAL).
LOG_LEVEL = logging.INFO

# ==============================================================================
# === END OF CONFIGURATION SECTION ===
# ==============================================================================

# --- Basic Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('discord') # Discord.py's logger
approval_bot_logger = logging.getLogger('ApprovalBot') # Our bot's specific logger

# --- Input Validation ---
# Exit early if critical configuration is missing to prevent unexpected behavior.
if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE': approval_bot_logger.critical("CRITICAL: Bot token missing."); exit(1)
if not GUILD_ID or GUILD_ID == 123456789012345678 or not ALLOWED_ROLE_ID or ALLOWED_ROLE_ID == 123456789012345679 or not PENDING_CHANNEL_ID or PENDING_CHANNEL_ID == 123456789012345680 or not APPROVAL_ROLE_ID or APPROVAL_ROLE_ID == 123456789012345681: approval_bot_logger.critical("CRITICAL: Essential Discord IDs missing or using default placeholders."); exit(1)
if LYCHEE_ENABLED and (not LYCHEE_API_URL or LYCHEE_API_URL == 'https://gallery.example.com/api' or not LYCHEE_API_KEY or LYCHEE_API_KEY == 'YOUR_LYCHEE_API_KEY_HERE' or not LYCHEE_ALBUM_ID or LYCHEE_ALBUM_ID == 'YOUR_ALBUM_ID_HERE'): approval_bot_logger.warning("[Config Check] Lychee ENABLED but its config appears incomplete/default.")

# --- Database Setup ---
def init_db():
    """
    Initializes the SQLite database. Creates tables if they don't exist.
    Tables:
        - approved_media: Stores URLs of approved media to prevent duplicates.
        - user_approval_stats: Tracks the number of approved uploads per original author.
    Raises:
        sqlite3.Error: If there's an issue creating the database/tables.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            # Stores URLs *after* they have been approved
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS approved_media (
                    url TEXT PRIMARY KEY,                 -- Discord CDN URL of the media
                    original_message_id INTEGER NOT NULL, -- ID of the message where media was originally posted
                    pending_message_id INTEGER NOT NULL,  -- ID of the message sent to the pending channel
                    approver_id INTEGER,                  -- Discord User ID of the approver
                    approved_at TEXT NOT NULL             -- ISO 8601 timestamp of approval (UTC)
                )
            ''')
            # Tracks stats for the *original author* of the submitted media
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_approval_stats (
                    user_id INTEGER PRIMARY KEY,          -- Discord User ID of the original author
                    approved_upload_count INTEGER DEFAULT 0 -- Count of their media items approved via this bot
                )
            ''')
            # Index for faster checking if a URL is already approved
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_approved_url ON approved_media (url)')
            conn.commit()
            approval_bot_logger.info(f"Database '{DATABASE_FILE}' initialized successfully.")
    except sqlite3.Error as e:
        approval_bot_logger.error(f"Database initialization error: {e}")
        raise # Propagate error to potentially stop bot startup

def is_url_approved(url: str) -> bool:
    """
    Checks if a given media URL already exists in the approved_media table.

    Args:
        url (str): The Discord CDN URL to check.

    Returns:
        bool: True if the URL is found in the database, False otherwise (or if DB error).
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM approved_media WHERE url = ?", (url,))
            return cursor.fetchone() is not None
    except sqlite3.Error as e:
        approval_bot_logger.error(f"DB query error (is_url_approved for {url}): {e}")
        return False # Treat DB errors as "not approved"

def add_approved_url(url: str, original_message_id: int, pending_message_id: int, approver_id: int):
    """
    Adds a media URL and associated metadata to the approved_media table upon approval.
    Uses INSERT OR IGNORE to prevent errors if the URL somehow already exists.

    Args:
        url (str): The Discord CDN URL of the approved media.
        original_message_id (int): ID of the message where the media originated.
        pending_message_id (int): ID of the message in the pending channel.
        approver_id (int): Discord User ID of the person who approved.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            timestamp = datetime.utcnow().isoformat() # Store timestamp in standard ISO format (UTC)
            cursor.execute(
                "INSERT OR IGNORE INTO approved_media (url, original_message_id, pending_message_id, approver_id, approved_at) VALUES (?, ?, ?, ?, ?)",
                (url, original_message_id, pending_message_id, approver_id, timestamp)
            )
            conn.commit()
            approval_bot_logger.debug(f"Added approved URL to DB: {url}")
    except sqlite3.Error as e:
        approval_bot_logger.error(f"DB insert error (add_approved_url for {url}): {e}")

def increment_user_stat(user_id: int, count: int):
    """
    Increments the approved upload count for the original author of the media.
    Uses INSERT...ON CONFLICT...DO UPDATE to handle new and existing users seamlessly.

    Args:
        user_id (int): The Discord User ID of the original author.
        count (int): The number of items approved for this user in this batch (usually 1 or more).
    """
    if count <= 0: return # Avoid unnecessary DB operations
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO user_approval_stats (user_id, approved_upload_count) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET approved_upload_count = approved_upload_count + excluded.approved_upload_count""",
                (user_id, count)
            )
            conn.commit()
            approval_bot_logger.info(f"Incremented approved count for user {user_id} by {count}")
    except sqlite3.Error as e:
        approval_bot_logger.error(f"DB update error (increment_user_stat for user {user_id}): {e}")

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True # Needed for prefix commands and reading content if necessary
intents.guilds = True          # Needed for guild/role info
intents.messages = True        # Needed for message events (replies, context menu)
# intents.members = True      # Consider enabling if frequently needing member info outside events/interactions

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None) # Disable default help

# --- Helper Functions ---
def get_ticket_number(channel_name: str) -> str:
    """
    Extracts the first sequence of digits found in a string (typically a channel name).
    Used to add context (e.g., ticket ID) to the approval request.

    Args:
        channel_name (str): The name of the channel.

    Returns:
        str: The first sequence of digits found, or "unknown" if no digits are present or input is invalid.
    """
    if not isinstance(channel_name, str):
        approval_bot_logger.warning(f"get_ticket_number received non-string input: {type(channel_name)}")
        return "unknown"
    match = re.search(r'\d+', channel_name) # Find one or more digits
    result = match.group() if match else "unknown"
    approval_bot_logger.debug(f"get_ticket_number: Input='{channel_name}', Output='{result}'")
    return result

async def check_submission_role(interaction_or_ctx) -> bool:
    """
    Checks if the user initiating an action (via Interaction or Context) has the ALLOWED_ROLE_ID.

    Args:
        interaction_or_ctx: Either a discord.Interaction or discord.ext.commands.Context object.

    Returns:
        bool: True if the user has the required role, False otherwise.
    """
    user = interaction_or_ctx.user if isinstance(interaction_or_ctx, discord.Interaction) else interaction_or_ctx.author
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        approval_bot_logger.warning(f"Guild {GUILD_ID} not found during submission role check.")
        return False
    try:
        # Get member object (from cache or fetch) to check roles
        member = guild.get_member(user.id) or await guild.fetch_member(user.id)
        return any(role.id == ALLOWED_ROLE_ID for role in member.roles) if member else False
    except (discord.NotFound, discord.Forbidden) as e:
         approval_bot_logger.warning(f"Cannot check submission role for {user.id}: {e}")
         return False
    except Exception as e:
        approval_bot_logger.error(f"Unexpected error during submission role check for {user.id}: {e}")
        return False

async def check_approval_role(interaction: discord.Interaction) -> bool:
    """
    Checks if the user interacting with a View component (button) has the APPROVAL_ROLE_ID.

    Args:
        interaction (discord.Interaction): The interaction object from the button click.

    Returns:
        bool: True if the user has the required role, False otherwise.
    """
    user = interaction.user # User who clicked the button
    # In guild interactions, user should be a Member object. If not, try fetching.
    if not isinstance(user, discord.Member):
        guild = bot.get_guild(GUILD_ID)
        if not guild: approval_bot_logger.warning(f"Guild {GUILD_ID} not found during approval check."); return False
        try: user = await guild.fetch_member(user.id)
        except (discord.NotFound, discord.Forbidden, Exception) as e:
             approval_bot_logger.warning(f"Could not fetch member {getattr(user, 'id', 'unknown')} for approval check: {e}"); return False
    # Check roles if we have a valid member object
    return any(role.id == APPROVAL_ROLE_ID for role in user.roles) if user and hasattr(user, 'roles') else False

# --- Lychee API Interaction ---
async def upload_to_lychee(attachment_url: str, filename: str, content_type: str, album_id: str, ticket_number: str, author_name: str) -> tuple[bool, str]:
    """
    Uploads a single attachment to the configured Lychee gallery.
    Attempts direct URL import first, falls back to download-then-upload.

    Args:
        attachment_url (str): The Discord CDN URL of the file to upload.
        filename (str): The original filename.
        content_type (str): The MIME type of the file (e.g., 'image/png').
        album_id (str): The Lychee album ID to upload into.
        ticket_number (str): The associated ticket number (for description).
        author_name (str): The original Discord author's name (for description).

    Returns:
        tuple[bool, str]: (Success status, Message detailing outcome or error).
    """
    if not LYCHEE_ENABLED: return False, "Lychee upload skipped (disabled)"
    if not LYCHEE_API_URL or not LYCHEE_API_KEY or not album_id: approval_bot_logger.warning(f"[Lychee] Skipped {filename}: Config missing."); return False, "Lychee config missing"
    content_type = content_type if content_type and '/' in content_type else 'application/octet-stream'; session_timeout = aiohttp.ClientTimeout(total=180) # 3 min timeout total

    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        headers = {"Authorization": f"{LYCHEE_API_KEY}", "Accept": "application/json"}

        # --- Attempt 1: Session::import (Import via URL) ---
        import_url = f"{LYCHEE_API_URL.rstrip('/')}/Session::import"; payload = {"url[0]": attachment_url, "albumID": album_id}
        approval_bot_logger.info(f"[Lychee] Import attempt: '{filename}' Album {album_id}")
        try:
            # Use a shorter timeout for the import check itself
            async with session.post(import_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                response_text = await response.text() # Read response for logging
                if response.status in [200, 201, 204]: # Common success codes
                    approval_bot_logger.info(f"[Lychee] Imported '{filename}' via URL (Status {response.status})."); return True, "Imported via URL"
                else:
                    approval_bot_logger.warning(f"[Lychee] Import failed {filename} (Status {response.status}). Response: {response_text[:200]}. Fallback.")
        except asyncio.TimeoutError:
             approval_bot_logger.warning(f"[Lychee] Import timed out for {filename}. Fallback.")
        except Exception as e: # Catch other errors during import attempt
             approval_bot_logger.warning(f"[Lychee] Import failed {filename}: {e}. Fallback.")

        # --- Attempt 2: Fallback - Download then Photo::add ---
        approval_bot_logger.info(f"[Lychee] Fallback download: '{filename}'.")
        try:
            async with session.get(attachment_url, timeout=aiohttp.ClientTimeout(total=60)) as dl_response: # Download timeout
                if dl_response.status == 200:
                    file_bytes = await dl_response.read()
                    if not file_bytes: approval_bot_logger.error(f"[Lychee] Downloaded 0 bytes '{filename}'."); return False, "Download Error (Empty)"
                    approval_bot_logger.info(f"[Lychee] Downloaded {len(file_bytes)} bytes '{filename}'. Uploading manually.")
                    # Prepare form data for manual upload
                    data = aiohttp.FormData(); data.add_field('file', file_bytes, filename=filename, content_type=content_type); data.add_field('albumID', album_id)
                    description_text = f"Discord Ticket: {ticket_number}\nAuthor: {author_name}\nFilename: {filename}"; data.add_field('description', description_text[:1000]) # Add description
                    add_photo_url = f"{LYCHEE_API_URL.rstrip('/')}/Photo::add"; upload_timeout = aiohttp.ClientTimeout(total=120) # Longer upload timeout
                    async with session.post(add_photo_url, data=data, headers=headers, timeout=upload_timeout) as upload_response:
                        upload_response_text = await upload_response.text()
                        if upload_response.status in [200, 201, 204]:
                            photo_id='N/A';
                            try: photo_id = (await upload_response.json()).get('id', 'N/A') # Try to get ID from response
                            except Exception: pass # Ignore if response is not JSON or ID missing
                            approval_bot_logger.info(f"[Lychee] Uploaded '{filename}' manually (ID: {photo_id})."); return True, f"Uploaded manually (ID: {photo_id})"
                        else: approval_bot_logger.error(f"[Lychee] Upload API fail {filename} ({upload_response.status}) Response: {upload_response_text[:500]}"); return False, f"Upload API Error ({upload_response.status})"
                else: approval_bot_logger.error(f"[Lychee] Download fail {filename} ({dl_response.status}) Response: {await dl_response.text()}"); return False, f"Download Error ({dl_response.status})"
        except aiohttp.ClientConnectorError as e: approval_bot_logger.error(f"[Lychee] Connection error during fallback {filename}: {e}"); return False, "Lychee Connection Error"
        except asyncio.TimeoutError: approval_bot_logger.error(f"[Lychee] Timeout during fallback {filename}"); return False, "Lychee Timeout"
        except Exception as e: approval_bot_logger.error(f"[Lychee] Fallback error {filename}: {e}\n{traceback.format_exc()}"); return False, "Lychee Upload Fallback Error"

    # Fallback if something unexpected happens
    return False, "Lychee processing failed unexpectedly"

# --- Core Logic to Prepare Pending Data ---
async def process_upload_request(origin_message: discord.Message) -> tuple[str | None, list[discord.Attachment]]:
    """
    Validates attachments in a message, checks against the approved DB,
    and returns only the *new* image/video attachments needing approval.

    Args:
        origin_message (discord.Message): The message containing potential media.

    Returns:
        tuple[str | None, list[discord.Attachment]]:
            (Error message string if validation fails or no new media, None otherwise),
            (List of discord.Attachment objects needing approval).
    """
    if not origin_message.attachments: return "Message has no attachments.", []
    new_attachments = []; valid_media_found = False; skipped_count = 0
    for a in origin_message.attachments:
        content_type = a.content_type or ""; is_media = content_type.startswith(('image/', 'video/'))
        if is_media:
            valid_media_found = True
            # Check DB only for valid media types
            if not is_url_approved(a.url):
                new_attachments.append(a)
            else:
                skipped_count += 1
                approval_bot_logger.info(f"Skipping already approved: {a.filename} ({a.url})")
    if not valid_media_found: return "No image/video attachments found.", []
    if not new_attachments: return f"All {skipped_count} image/video attachment(s) already approved.", []
    approval_bot_logger.info(f"Found {len(new_attachments)} new media attachments msg {origin_message.id}.")
    return None, new_attachments

# --- UI Components ---

class DenialReasonModal(discord.ui.Modal, title='Denial Reason'):
    """A Modal popup UI for entering the reason for denial."""
    reason_input = discord.ui.TextInput(
        label='Reason for Denial',
        style=discord.TextStyle.paragraph,
        placeholder='Provide a clear reason...',
        required=True, min_length=5, max_length=1000
    )

    def __init__(self, original_view: 'ApprovalView'):
         """Requires the original view to disable its buttons upon submission."""
         super().__init__(timeout=300); self.original_view = original_view

    async def on_submit(self, interaction: discord.Interaction):
        """Handles processing after the user submits the modal."""
        await interaction.response.defer(ephemeral=True)
        reason = self.reason_input.value; message = interaction.message; original_embed = message.embeds[0] if message.embeds else None
        if not original_embed: await interaction.followup.send("Error: No embed.", ephemeral=True); return

        approval_bot_logger.info(f"Processing denial modal msg {message.id} by {interaction.user}. Reason: {reason[:50]}...")

        # Update Embed with denial status and reason
        new_embed = original_embed.copy(); new_embed.title = "❌ Media Denied"; new_embed.color = discord.Color.red()
        status_idx = next((i for i, f in enumerate(new_embed.fields) if f.name=="Status"), -1)
        status_txt = f"❌ Denied by {interaction.user.mention} on <t:{int(datetime.utcnow().timestamp())}:f>\n**Reason:** {reason}"
        if status_idx != -1: new_embed.set_field_at(status_idx, name="Status", value=status_txt, inline=False)
        else: new_embed.add_field(name="Status", value=status_txt, inline=False)
        # Remove Lychee status field if present
        lychee_idx = next((i for i, f in enumerate(new_embed.fields) if f.name=="Lychee Status"), -1)
        if lychee_idx != -1: new_embed.remove_field(lychee_idx)

        # Disable buttons on the original message
        for item in self.original_view.children:
            if isinstance(item, discord.ui.Button): item.disabled = True

        try:
            # Edit the message in the pending channel
            await message.edit(embed=new_embed, view=self.original_view)
            await interaction.followup.send(f"Submission denied.", ephemeral=True) # Confirm action to moderator
        except discord.HTTPException as e:
            approval_bot_logger.error(f"Failed edit msg {message.id} modal denial: {e}")
            await interaction.followup.send("Denied, but failed to edit message.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Handles errors occurring during modal processing."""
        approval_bot_logger.error(f"DenialReasonModal error: {error}", exc_info=True)
        await interaction.followup.send("Error processing denial.", ephemeral=True)

class ApprovalView(discord.ui.View):
    """
    Persistent UI View attached to messages in the pending channel.
    Contains buttons for approving, approving without gallery upload, and denying submissions.
    """
    def __init__(self):
        """Initializes the view and disables the 'Skip Gallery' button if Lychee is off."""
        super().__init__(timeout=None) # Makes view persistent
        # Find the "Skip Gallery" button by its custom_id
        skip_gallery_button = discord.utils.get(self.children, custom_id="approval_bot:approve_skip_gallery")
        # Disable it if Lychee integration is globally turned off in config
        if skip_gallery_button and not LYCHEE_ENABLED:
            skip_gallery_button.disabled = True
            approval_bot_logger.debug("Disabled 'Skip Gallery' button (Lychee globally off).")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the user interacting has the necessary approval role."""
        if await check_approval_role(interaction):
            return True # Allow interaction
        else:
            # Inform user they lack permission and block interaction
            await interaction.response.send_message("You lack the role to approve/deny.", ephemeral=True)
            return False

    async def _handle_approval(self, interaction: discord.Interaction, *, upload_to_gallery: bool):
        """
        Core logic shared by both 'Approve' and 'Approve (Skip Gallery)' buttons.

        Args:
            interaction (discord.Interaction): The button click interaction.
            upload_to_gallery (bool): If True, attempt Lychee upload (if enabled). If False, skip Lychee.
        """
        await interaction.response.defer(ephemeral=True, thinking=True) # Acknowledge interaction quickly
        message = interaction.message; original_embed = message.embeds[0] if message.embeds else None
        if not original_embed: await interaction.followup.send("Error: No embed data.", ephemeral=True); return

        # Prevent double-processing if already approved/denied
        processed = False
        if original_embed.title and "Pending" not in original_embed.title: processed = True
        status_field = next((f.value for f in original_embed.fields if f.name == "Status"), None)
        if not processed and status_field and "Pending" not in status_field: processed = True
        if processed: await interaction.followup.send("Already processed.", ephemeral=True); return

        # Parse attachment details stored in the embed footer
        attachments_to_process = []
        if original_embed.footer and original_embed.footer.text: # Check footer exists and has text
            for line in original_embed.footer.text.split('\n'):
                if line.endswith("..."): continue # Skip trim indicator
                parts = line.split('|', 2);
                if len(parts) == 3: attachments_to_process.append({"url": parts[0], "filename": parts[1], "content_type": parts[2]})
                else: approval_bot_logger.warning(f"Parse fail footer line msg {message.id}: '{line}'")
        else: approval_bot_logger.error(f"Approval fail msg {message.id}: Footer missing/empty."); await interaction.followup.send("Error: No attachment details in footer.", ephemeral=True); return
        if not attachments_to_process: approval_bot_logger.error(f"Approval fail msg {message.id}: No attachments parsed."); await interaction.followup.send("Error: Could not parse attachment details.", ephemeral=True); return

        # Extract necessary IDs and names from embed fields/description
        original_msg_id_str=None; original_author_id_str=None; ticket_num_str="unknown"; original_author_name="unknown"
        ids_field = next((f.value for f in original_embed.fields if f.name=="IDs"), None)
        if ids_field:
            for line in ids_field.split('\n'):
                if line.startswith("OriginalMsg:"): original_msg_id_str = line.split(":",1)[1].strip()
                if line.startswith("Author:"): original_author_id_str = line.split(":",1)[1].strip()
        if original_embed.description:
             for line in original_embed.description.split('\n'):
                  if line.startswith("**Original Author Name:**"): original_author_name = line.split("`")[1] if '`' in line else "unknown"
                  if line.startswith("**Ticket Number:**"): ticket_num_str = line.split("`")[1] if '`' in line else "unknown"
                  if not original_author_id_str and line.startswith("**Original Author:**"): m=re.search(r'<@!?(\d+)>',line); original_author_id_str=m.group(1) if m else None
        try: original_msg_id = int(original_msg_id_str) if original_msg_id_str else 0; original_author_id = int(original_author_id_str) if original_author_id_str else 0
        except (ValueError, TypeError) as e: approval_bot_logger.error(f"Approval fail msg {message.id}: Cannot parse IDs - {e}"); await interaction.followup.send("Error: Cannot parse IDs.", ephemeral=True); return
        if not original_author_id: approval_bot_logger.warning(f"No original_author_id msg {message.id}. Stats skip.")

        # Process approvals: Add to DB, optionally upload to Lychee concurrently
        approval_bot_logger.info(f"Processing approval {len(attachments_to_process)} items (Gallery: {upload_to_gallery}) msg {message.id} by {interaction.user}")
        approved_count=0; lychee_success_count=0; lychee_failures=[]; upload_tasks=[]
        for att_data in attachments_to_process:
            # Add to internal DB first
            try: add_approved_url(att_data["url"], original_msg_id, message.id, interaction.user.id); approved_count += 1
            except Exception as db_err: approval_bot_logger.error(f"DB Error add URL {att_data['url']} msg {message.id}: {db_err}", exc_info=True); lychee_failures.append(f"{att_data['filename']}: DB Error"); continue # Skip this item
            # If uploading to gallery and it's enabled, create upload task
            if upload_to_gallery and LYCHEE_ENABLED:
                 try: task = asyncio.create_task(upload_to_lychee(att_data["url"], att_data["filename"], att_data["content_type"], LYCHEE_ALBUM_ID, ticket_num_str, original_author_name)); upload_tasks.append((task, att_data["filename"]))
                 except Exception as task_create_err: approval_bot_logger.error(f"Error creating Lychee task {att_data['filename']}: {task_create_err}", exc_info=True); lychee_failures.append(f"{att_data['filename']}: Task Create Error")

        # Wait for all Lychee uploads to complete (if any started)
        if upload_tasks:
            approval_bot_logger.info(f"Waiting for {len(upload_tasks)} Lychee tasks...")
            results = await asyncio.gather(*(t for t,fn in upload_tasks), return_exceptions=True)
            approval_bot_logger.info("Lychee tasks finished.")
            # Process results
            for i, res in enumerate(results):
                _, filename = upload_tasks[i]
                if isinstance(res, Exception): lychee_failures.append(f"{filename}: Upload Err ({type(res).__name__})"); approval_bot_logger.error(f"Lychee task exception {filename}: {res}", exc_info=True)
                else: success, msg = res;
                if success: lychee_success_count +=1
                else: lychee_failures.append(f"{filename}: {msg}"); approval_bot_logger.warning(f"Lychee upload fail {filename}: {msg}")

        # Increment user stats in DB
        if approved_count > 0 and original_author_id != 0:
            try: increment_user_stat(original_author_id, approved_count)
            except Exception as stat_err: approval_bot_logger.error(f"DB Error increment stats user {original_author_id} msg {message.id}: {stat_err}", exc_info=True)

        # Update the original embed in pending channel
        new_embed = original_embed.copy(); new_embed.title = "✅ Media Approved"; new_embed.color = discord.Color.green()
        # Update Status field
        status_idx = next((i for i,f in enumerate(new_embed.fields) if f.name=="Status"), -1)
        status_txt = f"✅ Approved by {interaction.user.mention} on <t:{int(datetime.utcnow().timestamp())}:f>";
        if not upload_to_gallery: status_txt += " (Gallery Skipped)"
        if status_idx != -1: new_embed.set_field_at(status_idx, name="Status", value=status_txt, inline=False)
        else: new_embed.add_field(name="Status", value=status_txt, inline=False)
        # Add/Update Lychee Status field if Lychee is enabled
        if LYCHEE_ENABLED:
            lychee_val = "Skipped"
            if upload_to_gallery: lychee_val = f"{lychee_success_count}/{len(attachments_to_process)} uploaded.";
            if lychee_failures: lychee_val += f" ({len(lychee_failures)} fail)"
            lychee_idx = next((i for i, f in enumerate(new_embed.fields) if f.name == "Lychee Status"), -1)
            if lychee_idx != -1: new_embed.set_field_at(lychee_idx, name="Lychee Status", value=lychee_val, inline=True)
            else: new_embed.add_field(name="Lychee Status", value=lychee_val, inline=True)
        else: # Remove Lychee status field if Lychee disabled (in case it existed from previous version)
            lychee_idx = next((i for i, f in enumerate(new_embed.fields) if f.name == "Lychee Status"), -1)
            if lychee_idx != -1: new_embed.remove_field(lychee_idx)

        # Disable all buttons on the view
        for item in self.children:
             if isinstance(item, discord.ui.Button): item.disabled=True

        # Try to edit the message
        edit_success = False
        try: await message.edit(embed=new_embed, view=self); edit_success = True
        except discord.NotFound: approval_bot_logger.warning(f"Failed edit msg {message.id} after approval: Not found.")
        except discord.Forbidden: approval_bot_logger.error(f"Failed edit msg {message.id} after approval: Missing Perms."); await interaction.followup.send("Approval processed, but I lack perms to edit message.", ephemeral=True); return
        except discord.HTTPException as e: approval_bot_logger.error(f"Failed edit msg {message.id} after approve (HTTP {e.status}): {e.text}", exc_info=True)
        except Exception as e: approval_bot_logger.error(f"Unexpected error editing msg {message.id} after approve: {e}", exc_info=True)

        # Send confirmation message to the moderator
        confirm_msg = f"Approved {approved_count} item(s)."
        if LYCHEE_ENABLED: confirm_msg += "\nLychee: Skipped." if not upload_to_gallery else f"\nLychee: {lychee_success_count}/{len(attachments_to_process)} uploaded ({len(lychee_failures)} fails logged)."
        if not edit_success: confirm_msg += "\n(Warning: Failed to update original message.)" # Add warning if edit failed
        await interaction.followup.send(confirm_msg, ephemeral=True)

    # --- Button Definitions ---
    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="approval_bot:approve")
    async def approve_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the main 'Approve' button."""
        await self._handle_approval(interaction, upload_to_gallery=True)

    @discord.ui.button(label="Approve (Skip Gallery)", style=discord.ButtonStyle.secondary, custom_id="approval_bot:approve_skip_gallery")
    async def approve_skip_gallery_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the 'Approve (Skip Gallery)' button."""
        # Button is disabled by __init__ if Lychee is globally off, but check again for safety.
        if not LYCHEE_ENABLED and button.disabled:
            await interaction.response.send_message("Lychee uploading is disabled globally.", ephemeral=True)
            return
        await self._handle_approval(interaction, upload_to_gallery=False)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="approval_bot:deny")
    async def deny_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the 'Deny' button. Opens the denial reason modal."""
        # Pre-check if already processed before showing modal
        message = interaction.message; original_embed = message.embeds[0] if message.embeds else None; processed = False
        if original_embed and original_embed.title and "Pending" not in original_embed.title: processed = True
        status_field = next((f.value for f in original_embed.fields if f.name == "Status"), None) if original_embed else None
        if not processed and status_field and "Pending" not in status_field: processed = True
        if processed: await interaction.response.send_message("Already processed.", ephemeral=True); return
        # Create and send the modal, passing 'self' (the view) to the modal
        modal = DenialReasonModal(original_view=self); await interaction.response.send_modal(modal)

# --- Event Handlers ---
@bot.event
async def on_ready():
    """Called when the bot successfully connects to Discord."""
    approval_bot_logger.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    approval_bot_logger.info(f'Guild: {GUILD_ID}, Pending: {PENDING_CHANNEL_ID}, Submit Role: {ALLOWED_ROLE_ID}, Approve Role: {APPROVAL_ROLE_ID}')
    approval_bot_logger.info(f'Lychee Enabled: {LYCHEE_ENABLED}')
    try:
        init_db() # Initialize database tables
        bot.add_view(ApprovalView()) # Register the persistent view *before* syncing commands
        approval_bot_logger.info("Registered persistent ApprovalView.")
        # Sync application commands (like context menu) to the specified guild
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj) # Copy global commands to guild scope
        synced = await bot.tree.sync(guild=guild_obj) # Sync specifically to this guild (faster updates)
        approval_bot_logger.info(f"Synced {len(synced)} application commands to Guild {GUILD_ID}.")
    except Exception as e:
        approval_bot_logger.error(f"Error during on_ready setup: {e}", exc_info=True)

@bot.event
async def on_command_error(ctx: commands.Context, error):
    """Global error handler for prefix commands."""
    if isinstance(error, commands.CommandNotFound): return # Ignore commands that don't exist
    # Default error message and deletion delay
    delete_after = 15; msg = f"Error: `{type(error).__name__}`"
    # User-friendly messages for common errors
    if isinstance(error, commands.CommandOnCooldown): msg = f"⏳ Cooldown! {error.retry_after:.1f}s."; delete_after = max(5, int(error.retry_after) + 1)
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)): msg = f"⚠️ Invalid/Missing arguments."
    elif isinstance(error, commands.CheckFailure): msg = "🚫 No permission/context for this command."
    elif isinstance(error, commands.CommandInvokeError):
        # Log the *original* exception for better debugging
        approval_bot_logger.error(f"Invoke Err '{ctx.command.name}': {error.original}", exc_info=error.original)
        msg = f"Err running cmd: `{type(error.original).__name__}`"
    else:
        # Log other unexpected errors
        approval_bot_logger.error(f"Unhandled Cmd Err '{ctx.command.name}': {error}", exc_info=error)
    # Try to reply and clean up the command message
    try: await ctx.reply(msg, mention_author=False, delete_after=delete_after); await asyncio.sleep(0.5); await ctx.message.delete()
    except Exception: pass # Ignore errors during error reporting/cleanup

# --- Application Commands (Context Menu) ---
@bot.tree.context_menu(name="Submit Media", guild=discord.Object(id=GUILD_ID))
async def submit_context(interaction: discord.Interaction, message: discord.Message):
    """Context menu command to submit media from a selected message."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    # Permission and basic checks
    if not await check_submission_role(interaction): await interaction.followup.send("No permission to submit.", ephemeral=True); return
    if not isinstance(interaction.channel, discord.TextChannel): await interaction.followup.send("Use in text channel.", ephemeral=True); return
    # Process the target message
    error_msg, new_attachments = await process_upload_request(message)
    if error_msg: await interaction.followup.send(error_msg, ephemeral=True); return
    if not new_attachments: await interaction.followup.send("No new media found.", ephemeral=True); return # Should be caught above, safety check
    # Get the destination channel
    pending_channel = bot.get_channel(PENDING_CHANNEL_ID)
    if not isinstance(pending_channel, discord.TextChannel): await interaction.followup.send("Error: Pending channel invalid.", ephemeral=True); return
    # Prepare data for the pending message
    files_to_send = new_attachments[:MAX_FILES_PER_MESSAGE]
    channel_name_for_ticket = interaction.channel.name # Log the source channel name
    approval_bot_logger.info(f"Attempting to get ticket number from channel name: '{channel_name_for_ticket}'")
    ticket_number = get_ticket_number(channel_name_for_ticket) # Extract ticket number
    submitter = interaction.user; original_author = message.author
    # Prepare footer text with attachment details (URL|Filename|Type)
    attachment_details = []; footer_limit = 2048; current_footer_len = 0
    for att in files_to_send: # Corrected footer loop
        url=att.url[:1000]; fname=att.filename.replace('|','_').replace('\n','_')[:200]; ctype=(att.content_type or 'unknown')[:100]; d_str=f"{url}|{fname}|{ctype}"
        if current_footer_len + len(d_str) + 1 > footer_limit: attachment_details.append("...(details trimmed)"); break
        attachment_details.append(d_str); current_footer_len += len(d_str) + 1
    footer_text = "\n".join(attachment_details)
    # Create the embed
    embed = discord.Embed(title="📥 Media Approval Request (Pending)", color=discord.Color.orange())
    embed.description=(f"**Submitted by:** {submitter.mention} (`{submitter.id}`)\n"+f"**Original Author:** {original_author.mention} (`{original_author.id}`)\n"+f"Original Author Name: `{original_author.name}`\n"+f"**Source Channel:** {interaction.channel.mention} (`{interaction.channel.name}`)\n"+f"**Ticket Number:** `{ticket_number}`\n"+f"**Original Message:** {message.jump_url}\n"+f"**Files Submitted:** {len(files_to_send)}")
    embed.add_field(name="Status", value="⏳ Pending Approval", inline=False); embed.add_field(name="IDs", value=f"OriginalMsg: {message.id}\nSubmitter: {submitter.id}\nAuthor: {original_author.id}", inline=True)
    embed.set_footer(text=footer_text); embed.timestamp = discord.utils.utcnow()
    # Try to set the first image in the embed for preview
    embed_image_set = False
    if files_to_send and files_to_send[0].content_type and files_to_send[0].content_type.startswith('image/'): img_url = files_to_send[0].url;
    if len(img_url) < 2000: embed.set_image(url=img_url); embed_image_set = True
    # Send the message to the pending channel
    try:
        send_kwargs = {"content": f"Review request from Ticket `{ticket_number}`.", "embed": embed, "view": ApprovalView()}
        # Conditionally attach files: only if >1 file OR if the single image couldn't be embedded
        attach_files_needed = (len(files_to_send) > 1) or not embed_image_set
        if attach_files_needed:
            discord_files_to_attach = []
            for i, attachment_obj in enumerate(files_to_send): # Corrected loop indent
                 try:
                     df = await attachment_obj.to_file(spoiler=False)
                     discord_files_to_attach.append(df) # Append inside Try
                 except Exception as file_err:
                     approval_bot_logger.error(f"Failed convert attachment {attachment_obj.filename}: {file_err}", exc_info=True)
                     continue # Skip this file
            if not discord_files_to_attach and len(files_to_send) > 0: await interaction.followup.send("Error preparing attachments.", ephemeral=True); return
            if discord_files_to_attach: send_kwargs["files"] = discord_files_to_attach; approval_bot_logger.debug(f"Sending context menu with {len(discord_files_to_attach)} attachments.")
        else: approval_bot_logger.debug("Sending context menu with image in embed, no separate file.")
        # Send the message
        pending_msg = await pending_channel.send(**send_kwargs)
        # Confirm success to the submitting user
        await interaction.followup.send(f"✅ Submitted {len(files_to_send)} item(s) from [message]({message.jump_url}) to {pending_channel.mention}.", ephemeral=True)
    except discord.Forbidden: await interaction.followup.send(f"Error: Cannot send to {pending_channel.mention}.", ephemeral=True)
    except discord.HTTPException as e: await interaction.followup.send(f"Error: Discord API issue ({e.status}).", ephemeral=True)
    except Exception as e: approval_bot_logger.error(f"Context menu send error: {e}\n{traceback.format_exc()}"); await interaction.followup.send("Unexpected send error.", ephemeral=True)

# --- Prefix Commands ---
@bot.command(name="upload", aliases=["submit"]) # Keep "submit" as alias?
@commands.guild_only()
@commands.cooldown(1, 10, commands.BucketType.user)
async def upload_prefix(ctx: commands.Context):
    """Prefix command to submit media by replying `>>upload` to a message."""
    # Helper to reply and delete trigger message
    async def reply_and_cleanup(content, delay=15):
        try: await ctx.reply(content, mention_author=False, delete_after=delay); await asyncio.sleep(0.5); await ctx.message.delete()
        except Exception: pass # Ignore cleanup errors
    try:
        # Permission and reply checks
        if not await check_submission_role(ctx): await reply_and_cleanup("No permission.", 10); return
        if not ctx.message.reference or not ctx.message.reference.message_id: await reply_and_cleanup("Reply to media msg.", 10); return
        # Fetch replied message
        try: replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception as e: await reply_and_cleanup(f"Cannot fetch replied message ({type(e).__name__}).", 10); return
        # Process attachments
        error_msg, new_attachments = await process_upload_request(replied_message)
        if error_msg: await reply_and_cleanup(error_msg, 15); return
        if not new_attachments: await reply_and_cleanup("No new media found in replied message.", 10); return
        # Get destination channel
        pending_channel = bot.get_channel(PENDING_CHANNEL_ID)
        if not isinstance(pending_channel, discord.TextChannel): await reply_and_cleanup("Error: Pending channel invalid.", 10); return
        # Prepare data
        files_to_send = new_attachments[:MAX_FILES_PER_MESSAGE]
        channel_name_for_ticket = ctx.channel.name # Log channel name
        approval_bot_logger.info(f"Attempting to get ticket number from channel name: '{channel_name_for_ticket}' (via >>upload)")
        ticket_number = get_ticket_number(channel_name_for_ticket)
        submitter = ctx.author; original_author = replied_message.author
        # Prepare footer
        attachment_details = []; footer_limit = 2048; current_footer_len = 0
        for att in files_to_send: # Corrected footer loop
            url=att.url[:1000]; fname=att.filename.replace('|','_').replace('\n','_')[:200]; ctype=(att.content_type or 'unknown')[:100]; d_str=f"{url}|{fname}|{ctype}"
            if current_footer_len + len(d_str) + 1 > footer_limit: attachment_details.append("...(details trimmed)"); break
            attachment_details.append(d_str); current_footer_len += len(d_str) + 1
        footer_text = "\n".join(attachment_details)
        # Create embed
        embed = discord.Embed(title="📥 Media Approval Request (Pending)", color=discord.Color.orange())
        embed.description=(f"**Submitted by:** {submitter.mention} (`{submitter.id}`)\n"+f"**Original Author:** {original_author.mention} (`{original_author.id}`)\n"+f"Original Author Name: `{original_author.name}`\n"+f"**Source Channel:** {ctx.channel.mention} (`{ctx.channel.name}`)\n"+f"**Ticket Number:** `{ticket_number}`\n"+f"**Original Message:** {replied_message.jump_url}\n"+f"**Files Submitted:** {len(files_to_send)}")
        embed.add_field(name="Status", value="⏳ Pending Approval", inline=False); embed.add_field(name="IDs", value=f"OriginalMsg: {replied_message.id}\nSubmitter: {submitter.id}\nAuthor: {original_author.id}", inline=True)
        embed.set_footer(text=footer_text); embed.timestamp = discord.utils.utcnow()
        # Set embed image if possible
        embed_image_set = False
        if files_to_send and files_to_send[0].content_type and files_to_send[0].content_type.startswith('image/'): img_url = files_to_send[0].url;
        if len(img_url) < 2000: embed.set_image(url=img_url); embed_image_set = True
        # Send message
        try:
            send_kwargs = {"content": f"Review request from Ticket `{ticket_number}`.", "embed": embed, "view": ApprovalView()}
            attach_files_needed = (len(files_to_send) > 1) or not embed_image_set # Refined condition
            if attach_files_needed:
                discord_files_to_attach = []
                for i, attachment_obj in enumerate(files_to_send): # Corrected loop indent
                    try:
                        df = await attachment_obj.to_file(spoiler=False)
                        discord_files_to_attach.append(df) # Append inside Try
                    except Exception as file_err:
                        approval_bot_logger.error(f"Failed convert attachment {attachment_obj.filename}: {file_err}", exc_info=True)
                        continue # Skip this file
                if not discord_files_to_attach and len(files_to_send) > 0: await reply_and_cleanup("Error preparing attachments.", 10); return
                if discord_files_to_attach: send_kwargs["files"] = discord_files_to_attach; approval_bot_logger.debug(f"Sending >>upload with {len(discord_files_to_attach)} attachments.")
            else: approval_bot_logger.debug("Sending >>upload with image in embed, no separate file.")
            # Send the message
            pending_msg = await pending_channel.send(**send_kwargs)
            # Confirm success and clean up trigger
            await reply_and_cleanup(f"✅ Submitted {len(files_to_send)} item(s) to {pending_channel.mention}.", 15)
        except discord.Forbidden: await reply_and_cleanup(f"Error: Cannot send to {pending_channel.mention}.", 10)
        except discord.HTTPException as e: await reply_and_cleanup(f"Error: Discord API issue ({e.status}).", 10)
        except Exception as e: approval_bot_logger.error(f"Upload send error: {e}\n{traceback.format_exc()}"); await reply_and_cleanup("Unexpected send error.", 10)
    except Exception as outer_e: approval_bot_logger.error(f"Outer error in >>upload: {outer_e}\n{traceback.format_exc()}"); await reply_and_cleanup("Unexpected outer error.", 10)

# --- Other Commands (Help, Why) ---
@bot.command(name="help")
@commands.cooldown(1, 10, commands.BucketType.user)
async def help_command(ctx: commands.Context):
    """Displays help information about the bot commands and workflow."""
    pchan = bot.get_channel(PENDING_CHANNEL_ID); pending_channel_mention = pchan.mention if pchan else f"`ID {PENDING_CHANNEL_ID}`"
    sub_role = ctx.guild.get_role(ALLOWED_ROLE_ID) if ctx.guild else None; app_role = ctx.guild.get_role(APPROVAL_ROLE_ID) if ctx.guild else None
    sub_role_mention = sub_role.mention if sub_role else f"`ID {ALLOWED_ROLE_ID}`"; app_role_mention = app_role.mention if app_role else f"`ID {APPROVAL_ROLE_ID}`"
    embed = discord.Embed(title="Media Approval Bot Help", description=f"Prefix: `{COMMAND_PREFIX}`", color=discord.Color.blue())
    embed.add_field(name=f"{COMMAND_PREFIX}upload (Reply)", value=f"Submit media from replied message.\nRequires: {sub_role_mention}", inline=False)
    embed.add_field(name="Submit Media (Context Menu)", value=f"Right-click msg -> Apps -> Submit Media.\nRequires: {sub_role_mention}", inline=False)
    approval_desc = (f"Submissions in {pending_channel_mention}. Users with {app_role_mention} use buttons:\n- `Approve`: Approves + Lychee (if enabled).\n- `Approve (Skip Gallery)`: Approves, no Lychee.")
    if not LYCHEE_ENABLED: approval_desc += " (This button is disabled as Lychee is off globally)."
    approval_desc += "\n- `Deny`: Requires reason via popup."
    embed.add_field(name="Approval Process", value=approval_desc, inline=False)
    if LYCHEE_ENABLED: embed.add_field(name="Lychee Gallery", value="Default 'Approve' uploads to Lychee.", inline=False)
    embed.add_field(name=f"{COMMAND_PREFIX}help", value="Shows this help message.", inline=False)
    embed.add_field(name=f"{COMMAND_PREFIX}why", value="General server disruption info (unrelated to bot).", inline=False)
    embed.set_footer(text=f"Bot running in Guild: {ctx.guild.name}" if ctx.guild else "Bot Help"); await ctx.send(embed=embed)

@bot.command(name="why")
@commands.cooldown(1, 60, commands.BucketType.channel)
async def why_command(ctx: commands.Context):
     """Displays generic information about potential server disruption causes."""
     embed=discord.Embed(title="Potential Causes of Server Disruption", description="Generic info.", color=discord.Color.dark_red())
     embed.add_field(name="Cracked/Offline-Mode", value="`online-mode: false` bypasses Mojang auth, risks impersonation.", inline=False)
     embed.add_field(name="Policy Violations", value="Violating Discord TOS/Guidelines (hate speech, etc.) can lead to action.", inline=False)
     embed.add_field(name="Griefing/Raiding", value="Organized disruption, spam, exploits.", inline=False)
     embed.add_field(name="Technical Issues", value="Hosting problems, DDoS, misconfigurations.", inline=False)
     await ctx.send(embed=embed)

# --- Bot Execution ---
if __name__ == "__main__":
    """Entry point for running the bot."""
    approval_bot_logger.info("Attempting to start Approval Bot...")
    try:
        # bot.run handles the login and event loop.
        # init_db() is called in on_ready after login attempt succeeds.
        bot.run(BOT_TOKEN, log_handler=None) # Use logging configured via basicConfig
    except discord.LoginFailure:
        # Specific error for bad token
        approval_bot_logger.critical("CRITICAL ERROR: Login Failed. Check BOT_TOKEN.")
    except discord.PrivilegedIntentsRequired:
        # Specific error if required intents aren't enabled in the Developer Portal
        approval_bot_logger.critical("CRITICAL ERROR: Privileged Intents Required (e.g., Server Members or Message Content). Enable in Dev Portal.")
    except Exception as e:
        # Catch any other exceptions during startup
        approval_bot_logger.critical(f"CRITICAL STARTUP ERROR: {e}", exc_info=True)
