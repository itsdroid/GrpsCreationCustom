import logging
import json
import os
import asyncio
import threading
import queue
import time
import zipfile
import tempfile
import shutil
from typing import Optional
from collections import defaultdict
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters, CallbackQueryHandler
)
from telegram.constants import ParseMode

from BigBotFinal import run_group_creation_process, API_ID, API_HASH, get_account_summary, send_account_stats_and_cleanup
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError
from datetime import datetime, timedelta

# --- Configuration ---
CONFIG_FILE = 'bot_config.json'
SESSIONS_DIR = 'sessions'

# Loading sticker ID
LOADING_STICKER_ID = "CAACAgUAAxkBAAEPUtFovPZ08EglcUMRAg0mpuQjV8eXRAACtRkAAiEb2VXfF6Me-ipGBjYE"

# Channel verification settings
REQUIRED_CHANNEL = "@NexoUnion"  # Replace with your channel username
CHANNEL_LINK = "https://t.me/NexoUnion"  # Replace with your channel link

# --- FIXED SETTINGS ---
FIXED_GROUPS_PER_ACCOUNT = 50
FIXED_DELAY = int((24 * 3600) / FIXED_GROUPS_PER_ACCOUNT)  # Informational average delay per group (~24h total cycle)
FIXED_MESSAGES_PER_GROUP = 10
FIXED_MESSAGES = [
"💻 صُنع الكود بعناية", "🖥️ الابتكار يبدأ من هنا",
"⚡ بُني للسرعة", "🔧 أدوات الاحتراف",
"🛠️ هندسة بدقة عالية", "📡 اتصال عالمي",
"🤖 جاهز للمستقبل", "💾 بياناتك في أمان",
"🌐 جسر بين التقنية والأفكار", "🚀 انطلاقة نحو التقدم"
]

# Broadcast reminder configuration
BROADCAST_USERS_FILE = 'broadcast_users.json'
BROADCAST_DELAY_HOURS = 25
REGISTERED_USERS = set()
BROADCAST_REMINDER_TASK = None

# States for conversation
(LOGIN_METHOD_CHOICE, GET_PHONE, GET_LOGIN_CODE, GET_2FA_PASS, UPLOAD_ZIP, GET_LOG_CHANNEL) = range(6)
ACTIVE_PROCESSES = {}
CANCELLATION_REQUESTED = {}  # Track when user requests cancellation
CANCELLATION_REQUESTED = {}  # Track when user requests cancellation

# Channel verification tracking (no longer used - bot is admin-only)
VERIFIED_USERS = set()  # Track users who have verified channel membership

# Account usage tracking for 24-hour limits
ACCOUNT_USAGE_FILE = 'account_usage.json'
MAX_GROUPS_PER_24H = 50  # Maximum groups per account per 24 hours

# --- Helper Functions ---
async def show_loading(update: Update, text: str = "⏳ Processing...") -> Optional[Message]:
    """Show loading sticker and message"""
    try:
        if update.message:
            return await update.message.reply_sticker(LOADING_STICKER_ID)
        elif update.callback_query:
            return await update.callback_query.message.reply_sticker(LOADING_STICKER_ID)
    except Exception as e:
        logging.error(f"Failed to send loading sticker: {e}")
        # Fallback to text message
        if update.message:
            return await update.message.reply_text(text)
        elif update.callback_query:
            return await update.callback_query.message.reply_text(text)
    return None

async def hide_loading(loading_msg: Optional[Message]):
    """Delete loading message/sticker"""
    try:
        if loading_msg:
            await loading_msg.delete()
    except Exception as e:
        logging.error(f"Failed to delete loading message: {e}")

def get_effective_message(update: Update) -> Optional[Message]:
    """Return the message associated with an update or callback."""
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None

async def reply_to_update(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs) -> Optional[Message]:
    """Reply to whichever message triggered the update, or DM the user."""
    target_message = get_effective_message(update)
    if target_message:
        return await target_message.reply_text(text, **kwargs)
    chat_id = update.effective_user.id if update.effective_user else None
    if chat_id:
        return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    return None

def load_config():
    if not os.path.exists(SESSIONS_DIR): 
        os.makedirs(SESSIONS_DIR)
        print(f"Created sessions directory: {SESSIONS_DIR}")
    
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump({"BOT_TOKEN": "YOUR_BOT_TOKEN_HERE", "OWNER_IDS": [], "ADMIN_IDS": [], "API_ID": "your api id", "API_HASH": "your api hash"}, f, indent=4)
        print("CONFIG CREATED: Please edit 'bot_config.json' with your bot token and owner IDs.")
        exit()
    
    with open(CONFIG_FILE, 'r') as f: 
        return json.load(f)

def backup_session(session_path: str, user_id: int):
    """Create a backup of the session file"""
    try:
        session_file = f"{session_path}.session"
        if os.path.exists(session_file):
            backup_dir = os.path.join(SESSIONS_DIR, str(user_id), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_name = f"{os.path.basename(session_path)}_{timestamp}.session"
            backup_path = os.path.join(backup_dir, backup_name)
            
            shutil.copy2(session_file, backup_path)
            print(f"Session backed up: {backup_path}")
            return backup_path
    except Exception as e:
        print(f"Failed to backup session: {e}")
    return None

def save_config(config_data):
    with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)

# --- Account Usage Tracking Functions ---
def load_account_usage():
    """Load account usage data from file"""
    if os.path.exists(ACCOUNT_USAGE_FILE):
        try:
            with open(ACCOUNT_USAGE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading account usage: {e}")
    return {}

def save_account_usage(usage_data):
    """Save account usage data to file"""
    try:
        with open(ACCOUNT_USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(usage_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving account usage: {e}")

def get_account_key(user_id, phone_number):
    """Generate unique key for account tracking"""
    clean_phone = phone_number.replace('+', '')
    return f"{user_id}_{clean_phone}"

def cleanup_old_usage_records():
    """Remove usage records older than 24 hours"""
    usage_data = load_account_usage()
    current_time = datetime.now()
    updated = False
    
    for account_key in list(usage_data.keys()):
        account_usage = usage_data[account_key]
        # Clean up old creation records (older than 24 hours)
        if 'creation_times' in account_usage:
            old_times = account_usage['creation_times']
            new_times = []
            for time_str in old_times:
                try:
                    creation_time = datetime.fromisoformat(time_str)
                    if current_time - creation_time < timedelta(hours=24):
                        new_times.append(time_str)
                    else:
                        updated = True
                except:
                    updated = True  # Remove invalid timestamps
            account_usage['creation_times'] = new_times
            
        # Remove accounts with no recent activity
        if not account_usage.get('creation_times'):
            del usage_data[account_key]
            updated = True
    
    if updated:
        save_account_usage(usage_data)
    
    return usage_data

def get_account_usage_info(user_id, phone_number):
    """Get current usage info for an account"""
    cleanup_old_usage_records()  # Clean up old records first
    usage_data = load_account_usage()
    account_key = get_account_key(user_id, phone_number)
    
    if account_key not in usage_data:
        return {
            'groups_created_24h': 0,
            'remaining_groups': MAX_GROUPS_PER_24H,
            'next_reset_time': None,
            'creation_times': []
        }
    
    account_usage = usage_data[account_key]
    creation_times = account_usage.get('creation_times', [])
    
    # Calculate next reset time (24 hours from oldest creation)
    next_reset_time = None
    if creation_times:
        try:
            oldest_time = datetime.fromisoformat(min(creation_times))
            next_reset_time = oldest_time + timedelta(hours=24)
        except:
            pass
    
    groups_created_24h = len(creation_times)
    remaining_groups = max(0, MAX_GROUPS_PER_24H - groups_created_24h)
    
    return {
        'groups_created_24h': groups_created_24h,
        'remaining_groups': remaining_groups,
        'next_reset_time': next_reset_time,
        'creation_times': creation_times
    }

def can_create_groups(user_id, phone_number, requested_count):
    """Check if account can create the requested number of groups"""
    usage_info = get_account_usage_info(user_id, phone_number)
    return usage_info['remaining_groups'] >= requested_count

def record_group_creation(user_id, phone_number, count=1):
    """Record group creation for an account"""
    usage_data = load_account_usage()
    account_key = get_account_key(user_id, phone_number)
    
    if account_key not in usage_data:
        usage_data[account_key] = {
            'creation_times': [],
            'phone': phone_number,
            'user_id': user_id
        }
    
    current_time = datetime.now().isoformat()
    
    # Add creation times for each group
    for _ in range(count):
        usage_data[account_key]['creation_times'].append(current_time)
    
    save_account_usage(usage_data)

def format_time_remaining(next_reset_time):
    """Format time remaining until reset in a readable format"""
    if not next_reset_time:
        return "No restrictions"
    
    current_time = datetime.now()
    if next_reset_time <= current_time:
        return "Available now"
    
    time_diff = next_reset_time - current_time
    hours = int(time_diff.total_seconds() // 3600)
    minutes = int((time_diff.total_seconds() % 3600) // 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def load_registered_users():
    """Load saved broadcast recipients."""
    if os.path.exists(BROADCAST_USERS_FILE):
        try:
            with open(BROADCAST_USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception as e:
            logging.error(f"Failed to load broadcast users: {e}")
    return set()

def save_registered_users():
    """Persist broadcast users to disk."""
    try:
        with open(BROADCAST_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(REGISTERED_USERS), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save broadcast users: {e}")

def register_broadcast_user(user_id: Optional[int]):
    """Register a user for future broadcast reminders."""
    if not user_id:
        return
    if user_id not in REGISTERED_USERS:
        REGISTERED_USERS.add(user_id)
        save_registered_users()

async def send_broadcast_reminder(bot):
    """Send reminder message to all registered users."""
    if not REGISTERED_USERS:
        return
    reminder_text = (
        "⏰ **25-Hour Reminder**\n\n"
        "لقد مر يوم كامل على آخر دورة إنشاء مجموعات.\n"
        "ابدأ الآن دفعة جديدة من الـ50 مجموعة للحفاظ على نشاط حساباتك 📈"
    )
    for uid in list(REGISTERED_USERS):
        try:
            await bot.send_message(chat_id=uid, text=reminder_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.error(f"Failed to send broadcast reminder to {uid}: {e}")

async def schedule_broadcast_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Schedule a reminder broadcast after the configured delay."""
    global BROADCAST_REMINDER_TASK
    if BROADCAST_REMINDER_TASK and not BROADCAST_REMINDER_TASK.done():
        return
    bot = context.bot
    async def _reminder_task():
        global BROADCAST_REMINDER_TASK
        try:
            await asyncio.sleep(BROADCAST_DELAY_HOURS * 3600)
            await send_broadcast_reminder(bot)
        except Exception as e:
            logging.error(f"Broadcast reminder task failed: {e}")
        finally:
            BROADCAST_REMINDER_TASK = None
    BROADCAST_REMINDER_TASK = asyncio.create_task(_reminder_task(), name="broadcast_reminder_task")

config = load_config()
# Initialize broadcast recipients from disk
REGISTERED_USERS = load_registered_users()
# Initialize account usage tracking
cleanup_old_usage_records()
# Handle backward compatibility - convert old OWNER_ID to OWNER_IDS array
if 'OWNER_ID' in config and 'OWNER_IDS' not in config:
    config['OWNER_IDS'] = [config['OWNER_ID']] if config['OWNER_ID'] != 0 else []
    del config['OWNER_ID']
    save_config(config)
    print("Converted OWNER_ID to OWNER_IDS array for backward compatibility")

OWNER_IDS, ADMIN_IDS = config['OWNER_IDS'], config['ADMIN_IDS']

async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is a member of the required channel"""
    try:
        # Remove @ symbol if present for the API call
        channel_username = REQUIRED_CHANNEL.replace('@', '')
        
        # Get chat member status
        chat_member = await context.bot.get_chat_member(chat_id=f"@{channel_username}", user_id=user_id)
        
        # Check if user is a member (member, administrator, or creator)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"Error checking channel membership for user {user_id}: {e}")
        return False

async def send_channel_verification_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_type="reply"):
    """Send channel verification message with join link and verify button - styled like Nexo Union"""
    verification_text = (
        f"🔔 **Join our channel to access the bot!**\n\n"
        f"📢 **Channel:** {REQUIRED_CHANNEL}\n"
        f"🔗 **Link:** {CHANNEL_LINK}\n\n"
        f"💡 **You must join the channel to use bot features**"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ Verify", callback_data="verify_channel")]
    ])
    
    if message_type == "reply" and update.message:
        await update.message.reply_text(verification_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    elif message_type == "edit" and hasattr(update, 'callback_query'):
        await update.callback_query.edit_message_text(verification_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        # Fallback for other cases
        if update.message:
            await update.message.reply_text(verification_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

def authorized(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        # Only owner and admins can access the bot
        if user_id in OWNER_IDS or user_id in ADMIN_IDS:
            register_broadcast_user(user_id)
            return await func(update, context, *args, **kwargs)
        
        # Regular users are not allowed - show access denied message
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    return wrapper

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id in ADMIN_IDS:
            return await func(update, context, *args, **kwargs)
        else: 
            await update.message.reply_text("⛔ **Admin Access Required!**\n\nOnly admins can access account management.", parse_mode=ParseMode.MARKDOWN)
    return wrapper

def get_main_keyboard():
    """Create main menu keyboard"""
    keyboard = [
        [InlineKeyboardButton("🔐 Login Your Accounts", callback_data="start_creation")],
        [InlineKeyboardButton("🚀 Start Groups Creation", callback_data="view_accounts")],
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
        [InlineKeyboardButton("👨‍💻 Developer Info", callback_data="developer_info")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    """Create admin management keyboard for owner"""
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin_prompt")],
        [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin_prompt")],
        [InlineKeyboardButton("📋 List Admins", callback_data="list_admins")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def validate_session(session_path, session_name, user_id=None):
    """Validate if a session file is still working with timeout protection"""
    client = None
    try:
        # Check if session file exists and has content
        session_file = f"{session_path}.session"
        if not os.path.exists(session_file):
            print(f"Session file not found: {session_file} for user {user_id}")
            return {'valid': False, 'reason': 'File not found'}
        
        file_size = os.path.getsize(session_file)
        if file_size == 0:
            print(f"Session file is empty: {session_file} for user {user_id}")
            return {'valid': False, 'reason': 'File empty'}
        
        print(f"Validating session: {session_path} (size: {file_size} bytes) for user {user_id}")
        
        # Use the API credentials from BigBotFinal with timeout protection
        client = TelegramClient(session_path, API_ID, API_HASH)
        
        # Set connection timeout to 15 seconds
        try:
            await asyncio.wait_for(client.connect(), timeout=15.0)
        except asyncio.TimeoutError:
            print(f"Connection timeout for session: {session_path} for user {user_id}")
            return {'valid': False, 'reason': 'Connection timeout'}
        
        # Check authorization with timeout
        try:
            is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10.0)
        except asyncio.TimeoutError:
            print(f"Authorization check timeout for session: {session_path} for user {user_id}")
            await client.disconnect()
            return {'valid': False, 'reason': 'Authorization timeout'}
        
        if is_authorized:
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=10.0)
                if me:
                    print(f"Session valid for: {me.first_name} (@{me.username}) - User ID: {user_id}")
                    result = {
                        'valid': True,
                        'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or 'Unknown',
                        'username': me.username or 'N/A',
                        'id': me.id,
                        'phone': session_name,
                        'user_id': user_id
                    }
                    await client.disconnect()
                    return result
                else:
                    print(f"Failed to get user details for session: {session_path}")
                    await client.disconnect()
                    return {'valid': False, 'reason': 'No user details'}
            except asyncio.TimeoutError:
                print(f"Get user details timeout for session: {session_path} for user {user_id}")
                await client.disconnect()
                return {'valid': False, 'reason': 'User details timeout'}
        else:
            print(f"Session not authorized: {session_path} for user {user_id}")
            await client.disconnect()
            return {'valid': False, 'reason': 'Not authorized'}
            
    except Exception as e:
        print(f"Error validating session {session_path} for user {user_id}: {e}")
        if client:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except:
                pass
        return {'valid': False, 'reason': str(e)}

def get_account_keyboard(sessions):
    """Create keyboard for account selection"""
    keyboard = []
    for i, session in enumerate(sessions[:10]):  # Limit to 10 accounts per page
        keyboard.append([InlineKeyboardButton(f"📱 {session}", callback_data=f"account_{session}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

def ensure_user_session_path(user_id: int, session_name: str) -> str:
    """Ensure proper session path construction for user-specific sessions"""
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    # Try migrating any legacy sessions into the user folder
    migrate_legacy_sessions_if_any(user_id)
    os.makedirs(user_session_dir, exist_ok=True)
    session_path = os.path.join(user_session_dir, session_name)
    print(f"Session path for user {user_id}: {session_path}")
    return session_path

def get_session_file_path(user_id: int, session_name: str) -> str:
    """Get the full path to the .session file"""
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    session_file_path = os.path.join(user_session_dir, f"{session_name}.session")
    print(f"Session file path for user {user_id}: {session_file_path}")
    return session_file_path

def escape_markdown(text: str) -> str:
    """Escape special characters that can break Markdown formatting"""
    if not text:
        return text
    
    # Characters that need escaping in Markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    escaped_text = str(text)
    for char in special_chars:
        escaped_text = escaped_text.replace(char, f'\\{char}')
    
    return escaped_text

# --- Per-user settings (log channel, summary msg IDs) ---
def _user_settings_path(user_id: int) -> str:
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    os.makedirs(user_session_dir, exist_ok=True)
    return os.path.join(user_session_dir, 'settings.json')

def load_user_settings(user_id: int) -> dict:
    path = _user_settings_path(user_id)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'account_summaries' not in data:
                    data['account_summaries'] = {}
                return data
        except Exception as e:
            print(f"Failed to load user settings for {user_id}: {e}")
    return {"log_channel_id": None, "account_summaries": {}}

def save_user_settings(user_id: int, settings: dict):
    try:
        path = _user_settings_path(user_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Failed to save user settings for {user_id}: {e}")

def get_log_channel_id(user_id: int) -> Optional[int]:
    return load_user_settings(user_id).get('log_channel_id')

def set_log_channel_id(user_id: int, channel_id: int):
    settings = load_user_settings(user_id)
    settings['log_channel_id'] = channel_id
    save_user_settings(user_id, settings)

def get_account_summary_message_id(user_id: int, phone: str) -> Optional[int]:
    settings = load_user_settings(user_id)
    return settings.get('account_summaries', {}).get(phone)

def set_account_summary_message_id(user_id: int, phone: str, message_id: int):
    settings = load_user_settings(user_id)
    summaries = settings.get('account_summaries', {})
    summaries[phone] = message_id
    settings['account_summaries'] = summaries
    save_user_settings(user_id, settings)

def clear_account_summary_message_id(user_id: int, phone: str):
    settings = load_user_settings(user_id)
    summaries = settings.get('account_summaries', {})
    if phone in summaries:
        del summaries[phone]
        settings['account_summaries'] = summaries
        save_user_settings(user_id, settings)

def get_links_file_message_id(user_id: int, phone: str) -> Optional[int]:
    """Get the message id of the last links TXT file sent for this account in the log channel."""
    settings = load_user_settings(user_id)
    links_map = settings.get('links_files', {})
    return links_map.get(phone)

def set_links_file_message_id(user_id: int, phone: str, message_id: int):
    """Store the message id of the latest links TXT file for this account."""
    settings = load_user_settings(user_id)
    links_map = settings.get('links_files', {})
    links_map[phone] = message_id
    settings['links_files'] = links_map
    save_user_settings(user_id, settings)

def get_summary_txt_message_id(user_id: int) -> Optional[int]:
    settings = load_user_settings(user_id)
    return settings.get('summary_txt_message_id')

def set_summary_txt_message_id(user_id: int, message_id: int):
    settings = load_user_settings(user_id)
    settings['summary_txt_message_id'] = message_id
    save_user_settings(user_id, settings)

def get_aggregate_summary_message_id(user_id: int) -> Optional[int]:
    """Retrieve the single aggregated summary message id for the channel."""
    settings = load_user_settings(user_id)
    return settings.get('aggregate_summary_message_id')

def set_aggregate_summary_message_id(user_id: int, message_id: int):
    """Persist the aggregated summary message id for the channel."""
    settings = load_user_settings(user_id)
    settings['aggregate_summary_message_id'] = message_id
    save_user_settings(user_id, settings)

def add_uploaded_session_record(user_id: int, record: dict):
    """Persist an uploaded session record: {file_id, filename, phone}"""
    settings = load_user_settings(user_id)
    uploaded = settings.get('uploaded_sessions', [])
    # Avoid duplicates by filename
    if not any(r.get('filename') == record.get('filename') for r in uploaded):
        uploaded.append(record)
        settings['uploaded_sessions'] = uploaded
        save_user_settings(user_id, settings)

def get_uploaded_sessions(user_id: int) -> list:
    settings = load_user_settings(user_id)
    return settings.get('uploaded_sessions', [])

# --- Utilities ---
def guess_country_from_phone(phone_digits: str) -> str:
    """Very small mapping to guess country from international prefix.
    Expects phone_digits without '+'."""
    prefixes = {
        '1': 'United States/Canada',
        '7': 'Russia/Kazakhstan',
        '20': 'Egypt',
        '27': 'South Africa',
        '30': 'Greece',
        '31': 'Netherlands',
        '32': 'Belgium',
        '33': 'France',
        '34': 'Spain',
        '39': 'Italy',
        '44': 'United Kingdom',
        '49': 'Germany',
        '52': 'Mexico',
        '55': 'Brazil',
        '60': 'Malaysia',
        '61': 'Australia',
        '62': 'Indonesia',
        '63': 'Philippines',
        '65': 'Singapore',
        '81': 'Japan',
        '82': 'South Korea',
        '84': 'Vietnam',
        '86': 'China',
        '90': 'Turkey',
        '91': 'India',
        '92': 'Pakistan',
        '93': 'Afghanistan',
        '94': 'Sri Lanka',
        '95': 'Myanmar',
        '98': 'Iran',
        '212': 'Morocco',
        '213': 'Algeria',
        '216': 'Tunisia',
        '218': 'Libya',
        '234': 'Nigeria',
        '254': 'Kenya',
        '255': 'Tanzania',
        '256': 'Uganda',
        '380': 'Ukraine',
        '420': 'Czech Republic',
        '421': 'Slovakia',
        '852': 'Hong Kong',
        '853': 'Macau',
        '855': 'Cambodia',
        '856': 'Laos',
        '880': 'Bangladesh',
        '886': 'Taiwan',
    }
    # Match the longest prefix first
    for length in range(4, 0, -1):
        pref = phone_digits[:length]
        if pref in prefixes:
            return prefixes[pref]
    return 'Unknown'

def debug_session_storage(user_id: int):
    """Debug function to show session storage structure for a user"""
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    print(f"=== Session Storage Debug for User {user_id} ===")
    print(f"User session directory: {user_session_dir}")
    
    if os.path.exists(user_session_dir):
        session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
        print(f"Found {len(session_files)} session files:")
        for session_file in session_files:
            session_path = os.path.join(user_session_dir, session_file)
            file_size = os.path.getsize(session_path) if os.path.exists(session_path) else 0
            print(f"  - {session_file} (size: {file_size} bytes)")
            
        # Check backup directory
        backup_dir = os.path.join(user_session_dir, "backups")
        if os.path.exists(backup_dir):
            backup_files = [f for f in os.listdir(backup_dir) if f.endswith('.session')]
            print(f"Found {len(backup_files)} backup files in backups/")
    else:
        print("User session directory does not exist")
    print("=" * 50)

async def get_any_valid_client(user_id: int) -> Optional[TelegramClient]:
    """Try to open any valid Telethon client from user's sessions. Returns connected client or None."""
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    if not os.path.exists(user_session_dir):
        return None
    for fname in os.listdir(user_session_dir):
        if fname.endswith('.session'):
            session_name = fname[:-8]
            session_path = os.path.join(user_session_dir, session_name)
            try:
                client = TelegramClient(session_path, API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    return client
                await client.disconnect()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
    return None

async def get_bot_client() -> Optional[TelegramClient]:
    """Create a Telethon client authenticated as the bot using BOT_TOKEN for channel history access."""
    try:
        # Use a separate session for bot client
        bot_session_dir = os.path.join(SESSIONS_DIR, 'bot')
        os.makedirs(bot_session_dir, exist_ok=True)
        bot_session_path = os.path.join(bot_session_dir, 'bot_session')
        client = TelegramClient(bot_session_path, API_ID, API_HASH)
        await client.start(bot_token=config['BOT_TOKEN'])
        return client
    except Exception as e:
        print(f"Failed to create bot Telethon client: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return None

async def restore_sessions_via_telethon_history(user_id: int, channel_id: int) -> dict:
    """Scan the log channel history via Telethon and download .session files into user's folder."""
    result = {"restored": 0, "skipped": 0, "failed": 0}
    client = await get_any_valid_client(user_id)
    if not client:
        # Try a bot-authenticated Telethon client as fallback
        client = await get_bot_client()
        if not client:
            return result
    try:
        # Ensure entity
        entity = await client.get_entity(channel_id)
        # Iterate recent messages
        async for msg in client.iter_messages(entity, limit=1000):
            try:
                if not msg or not msg.document:
                    continue
                # Try to get filename
                filename = None
                if msg.file and getattr(msg.file, 'name', None):
                    filename = msg.file.name
                # Fallback to attributes
                if not filename and msg.document and getattr(msg.document, 'attributes', None):
                    for attr in msg.document.attributes:
                        if getattr(attr, 'file_name', None):
                            filename = attr.file_name
                            # After sending individual files, also upload/update a single summary TXT in channel
                try:
                    channel_id = get_log_channel_id(user_id)
                    if channel_id:
                        # Build summary content from results
                        lines = []
                        for res in results:
                            pn = res.get('phone_number', 'Unknown')
                            total_all_time = res.get('total_groups_created', 0)
                            lines.append(f"{pn} - {total_all_time} total")
                        summary_text = "\n".join(lines) if lines else "No data"
                        # Prepare a temp summary file
                        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                        os.makedirs(user_session_dir, exist_ok=True)
                        summary_path = os.path.join(user_session_dir, 'account_totals_summary.txt')
                        with open(summary_path, 'w', encoding='utf-8') as sf:
                            sf.write(summary_text)

                        # Delete previous summary txt message if exists
                        old_sum_id = get_summary_txt_message_id(user_id)
                        if old_sum_id:
                            try:
                                await context.bot.delete_message(chat_id=channel_id, message_id=old_sum_id)
                            except Exception:
                                pass

                        # Send new summary txt
                        with open(summary_path, 'rb') as sf:
                            sent = await context.bot.send_document(
                                chat_id=channel_id,
                                document=InputFile(sf, filename='account_totals_summary.txt'),
                                caption='📄 Accounts Totals Summary',
                                parse_mode=ParseMode.MARKDOWN
                            )
                        if sent:
                            set_summary_txt_message_id(user_id, sent.message_id)
                except Exception as e:
                    print(f"Failed to send/update summary txt: {e}")

                break
                if not filename or not filename.endswith('.session'):
                    continue
                user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                os.makedirs(user_session_dir, exist_ok=True)
                target_path = os.path.join(user_session_dir, filename)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    result["skipped"] += 1
                    continue
                # Download
                await client.download_media(msg, file=target_path)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    result["restored"] += 1
                else:
                    result["failed"] += 1
            except Exception:
                result["failed"] += 1
                continue
    except Exception:
        pass
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return result

def migrate_legacy_sessions_if_any(user_id: int) -> int:
    """Move legacy .session files from the root sessions/ folder into the user's folder.
    Returns number of files moved."""
    moved = 0
    try:
        root_dir = SESSIONS_DIR
        user_dir = os.path.join(SESSIONS_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        if os.path.exists(root_dir):
            for f in os.listdir(root_dir):
                if f.endswith('.session'):
                    src = os.path.join(root_dir, f)
                    dst = os.path.join(user_dir, f)
                    try:
                        shutil.move(src, dst)
                        moved += 1
                    except Exception as e:
                        print(f"Failed to migrate legacy session {f}: {e}")
    except Exception as e:
        print(f"Error in migrate_legacy_sessions_if_any: {e}")
    return moved

async def process_zip_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, zip_file_path: str):
    """Process ZIP file containing session and JSON files"""
    user_id = update.effective_user.id
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    os.makedirs(user_session_dir, exist_ok=True)
    
    temp_dir = tempfile.mkdtemp()
    accounts_info = []
    
    try:
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Find JSON files and corresponding session files (limit to 20)
        json_files = [f for f in os.listdir(temp_dir) if f.endswith('.json')][:20]
        
        if len([f for f in os.listdir(temp_dir) if f.endswith('.json')]) > 20:
            await update.message.reply_text(
                "⚠️ **Account Limit Reached!**\n\n"
                f"📊 **Found:** {len([f for f in os.listdir(temp_dir) if f.endswith('.json')])} accounts in ZIP\n"
                f"🔒 **Limit:** 20 accounts maximum\n\n"
                f"✅ **Processing first 20 accounts only**",
                parse_mode=ParseMode.MARKDOWN
            )
        
        total_accounts = len(json_files)
        processed_count = 0
        
        # Show initial progress
        await update.message.reply_text(
            f"📁 **Processing ZIP Accounts**\n\n"
            f"📊 **Progress:** 0/{total_accounts} accounts processed\n"
            f"{'▱' * 10} 0%",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for json_file in json_files:
            processed_count += 1
            
            # Update progress bar
            progress_percentage = int((processed_count / total_accounts) * 100)
            filled_bars = int((processed_count / total_accounts) * 10)
            empty_bars = 10 - filled_bars
            progress_bar = '▰' * filled_bars + '▱' * empty_bars
            
            await update.message.reply_text(
                f"📁 **Processing ZIP Accounts**\n\n"
                f"📊 **Progress:** {processed_count}/{total_accounts} accounts processed\n"
                f"{progress_bar} {progress_percentage}%\n\n"
                f"🔄 **Currently processing:** {json_file.replace('.json', '')}",
                parse_mode=ParseMode.MARKDOWN
            )
            phone_number = json_file.replace('.json', '')
            session_file = f"{phone_number}.session"
            
            if session_file in os.listdir(temp_dir):
                # Load account data from JSON
                with open(os.path.join(temp_dir, json_file), 'r') as f:
                    account_data = json.load(f)
                
                # Copy session file to user's directory
                source_session = os.path.join(temp_dir, session_file)
                dest_session = os.path.join(user_session_dir, session_file)
                shutil.copy2(source_session, dest_session)
                
                # Test the account and get details
                try:
                    session_path = os.path.join(user_session_dir, phone_number)
                    api_id = account_data.get('app_id', API_ID)
                    api_hash = account_data.get('app_hash', API_HASH)
                    twofa = account_data.get('twoFA', '')
                    
                    client = TelegramClient(session_path, api_id, api_hash)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        if twofa:
                            try:
                                await client.sign_in(password=twofa)
                            except Exception as auth_error:
                                print(f"2FA failed for {phone_number}: {auth_error}")
                                await client.disconnect()
                                continue
                        else:
                            print(f"Account {phone_number} not authorized and no 2FA provided")
                            await client.disconnect()
                            continue
                    
                    me = await client.get_me()
                    await client.disconnect()
                    
                    account_info = {
                        'session_path': session_path,
                        'phone': account_data.get('phone', phone_number),
                        'api_id': api_id,
                        'api_hash': api_hash,
                        'user_details': {
                            'name': f"{me.first_name} {me.last_name or ''}".strip(),
                            'username': me.username or 'N/A',
                            'id': me.id
                        }
                    }
                    accounts_info.append(account_info)
                    # Upload valid session to log channel with details
                    try:
                        channel_id = get_log_channel_id(user_id)
                        if channel_id:
                            session_file_path = os.path.join(user_session_dir, session_file)
                            if os.path.exists(session_file_path) and os.path.getsize(session_file_path) > 0:
                                country = guess_country_from_phone(str(account_info['phone']).replace('+',''))
                                safe_country = escape_markdown(country)
                                safe_name = escape_markdown(account_info['user_details']['name'])
                                safe_phone = escape_markdown(str(account_info['phone']))
                                with open(session_file_path, 'rb') as f:
                                    sent_doc = await context.bot.send_document(
                                        chat_id=channel_id,
                                        document=f,
                                        filename=os.path.basename(session_file_path),
                                        caption=(
                                            "📁 **Session File**\n\n"
                                            f"👤 **User:** {safe_name}\n"
                                            f"📞 **Phone:** `{safe_phone}`\n"
                                            f"🆔 **User ID:** `{account_info['user_details']['id']}`\n"
                                            f"🌍 **Country:** {safe_country}"
                                        ),
                                        parse_mode=ParseMode.MARKDOWN
                                    )
                                # Store file_id for restoration
                                try:
                                    if sent_doc and sent_doc.document:
                                        add_uploaded_session_record(user_id, {
                                            'file_id': sent_doc.document.file_id,
                                            'filename': os.path.basename(session_file_path),
                                            'phone': str(account_info['phone']).replace('+','')
                                        })
                                except Exception as e:
                                    print(f"Failed to record ZIP uploaded session: {e}")

                                # Also upload the corresponding JSON (use original from ZIP)
                                try:
                                    json_source = os.path.join(temp_dir, f"{phone_number}.json")
                                    if os.path.exists(json_source):
                                        with open(json_source, 'rb') as jf:
                                            await context.bot.send_document(
                                                chat_id=channel_id,
                                                document=jf,
                                                filename=os.path.basename(json_source),
                                                caption="📄 Account JSON",
                                                parse_mode=ParseMode.MARKDOWN
                                            )
                                    else:
                                        # Generate JSON if not in ZIP
                                        json_payload = {
                                            "app_id": api_id,
                                            "app_hash": api_hash,
                                            "twoFA": account_data.get('twoFA', ''),
                                            "phone": str(account_info['phone']),
                                            "user_id": account_info['user_details']['id']
                                        }
                                        tmp_json = os.path.join(user_session_dir, f"{phone_number}.json")
                                        with open(tmp_json, 'w', encoding='utf-8') as jf:
                                            json.dump(json_payload, jf, indent=2)
                                        with open(tmp_json, 'rb') as jf:
                                            await context.bot.send_document(
                                                chat_id=channel_id,
                                                document=jf,
                                                filename=os.path.basename(tmp_json),
                                                caption="📄 Account JSON",
                                                parse_mode=ParseMode.MARKDOWN
                                            )
                                except Exception as e:
                                    print(f"Failed to upload ZIP JSON to channel: {e}")
                    except Exception as e:
                        print(f"Failed to upload ZIP session to channel: {e}")
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ **Failed to process {phone_number}:** {str(e)}", parse_mode=ParseMode.MARKDOWN)
        
        # Show final completion message
        await update.message.reply_text(
            f"✅ **ZIP Processing Complete!**\n\n"
            f"📊 **Results:** {len(accounts_info)}/{total_accounts} accounts successfully processed\n"
            f"{'▰' * 10} 100%",
            parse_mode=ParseMode.MARKDOWN
        )
        
        if accounts_info:
            # Send success report
            report_text = f"🎉 **Account Loading Summary**\n\n📊 **Successfully Loaded:** {len(accounts_info)} accounts\n\n"
            
            for i, acc in enumerate(accounts_info[:10], 1):  # Show first 10
                details = acc['user_details']
                # Escape special characters for safe display
                safe_name = escape_markdown(details['name'])
                safe_username = escape_markdown(details['username'])
                safe_phone = escape_markdown(acc['phone'])
                
                report_text += f"📱 `{i}.` {safe_name} (@{safe_username}) - {safe_phone}\n"
            
            if len(accounts_info) > 10:
                report_text += f"\n... and {len(accounts_info) - 10} more accounts"
            
            await update.message.reply_text(report_text, parse_mode=ParseMode.MARKDOWN)
            
            # Store accounts info for later use if needed and present next actions
            context.user_data['zip_accounts'] = accounts_info
            next_actions_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Login More Accounts", callback_data="start_creation")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
            await update.message.reply_text(
                "✅ **ZIP Accounts Loaded Successfully!**\n\nChoose what to do next:",
                reply_markup=next_actions_keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            # Clear conversation state; no group count prompt after ZIP upload
            context.user_data['conversation_state'] = None
        else:
            await update.message.reply_text("❌ **No Valid Accounts Found**\n\nPlease check your ZIP file format and try again.", parse_mode=ParseMode.MARKDOWN)
            context.user_data['conversation_state'] = None
            
    except Exception as e:
        await update.message.reply_text(f"❌ **ZIP Processing Error:** {str(e)}", parse_mode=ParseMode.MARKDOWN)
        context.user_data['conversation_state'] = None
    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(zip_file_path):
            os.remove(zip_file_path)

async def send_login_success_details(update: Update, context: ContextTypes.DEFAULT_TYPE, session_path: str, phone: str):
    """Connects to a session, sends details, and then disconnects."""
    try:
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            await update.message.reply_text("❌ **Session Invalid**\n\nPlease try logging in again.", parse_mode=ParseMode.MARKDOWN)
            context.user_data['conversation_state'] = None
            return
        
        me = await client.get_me()
        if not me:
            await client.disconnect()
            await update.message.reply_text("❌ **Failed to get user details**\n\nPlease try again.", parse_mode=ParseMode.MARKDOWN)
            context.user_data['conversation_state'] = None
            return
            
        # Escape special characters for safe Markdown display
        safe_first_name = escape_markdown(me.first_name or '')
        safe_last_name = escape_markdown(me.last_name or '')
        safe_username = escape_markdown(me.username or 'N/A')
        safe_phone = escape_markdown(phone)
        
        details_text = (
            f"✅ **Account Successfully Logged In!**\n\n"
            f"👤 **Name:** {safe_first_name} {safe_last_name}\n"
            f"🔖 **Username:** @{safe_username}\n"
            f"🆔 **ID:** `{me.id}`\n"
            f"📱 **Phone:** `{safe_phone}`\n\n"
            f"🔐 **Session Status:** Active & Saved\n\n"
            f"⚠️ **Important:** Wait 2-3 minutes before starting group creation to avoid account freezing!"
        )
        await update.message.reply_text(details_text, parse_mode=ParseMode.MARKDOWN)
        
        # Send session file
        session_file = f"{session_path}.session"
        if os.path.exists(session_file):
            try:
                with open(session_file, 'rb') as file:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=file,
                        caption="📁 **Session File**\n\nKeep this file safe! It contains your login session.",
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                print(f"Failed to send session file: {e}")
        
        # Properly disconnect and save session
        await client.disconnect()
        
        # Verify session file exists and is valid
        if os.path.exists(session_file):
            file_size = os.path.getsize(session_file)
            if file_size > 0:
                print(f"Session file saved successfully: {session_file} ({file_size} bytes)")
                
                # Create backup of the session
                user_id = update.effective_user.id
                backup_path = backup_session(session_path, user_id)
                if backup_path:
                    print(f"Session backup created: {backup_path}")
                
            else:
                print(f"Warning: Session file is empty: {session_file}")
        
        context.user_data['account_info'] = {'session_path': session_path, 'phone': phone}
        
        # Log to user's configured channel if available
        try:
            user_id = update.effective_user.id
            channel_id = get_log_channel_id(user_id)
            if channel_id:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=(
                        f"🟢 **Account Logged In**\n\n"
                        f"👤 **Name:** {safe_first_name} {safe_last_name}\n"
                        f"🔖 **Username:** @{safe_username}\n"
                        f"🆔 **ID:** `{me.id}`\n"
                        f"📱 **Phone:** `{safe_phone}`\n"
                        f"🗃️ **Session:** Saved"
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
                # Also upload the valid session file to the log channel
                try:
                    session_file = f"{session_path}.session"
                    if os.path.exists(session_file) and os.path.getsize(session_file) > 0:
                        # Country guess
                        country = guess_country_from_phone(phone.replace('+',''))
                        safe_country = escape_markdown(country)
                        with open(session_file, 'rb') as f:
                            sent_doc = await context.bot.send_document(
                                chat_id=channel_id,
                                document=f,
                                filename=os.path.basename(session_file),
                                caption=(
                                    "📁 **Session File**\n\n"
                                    f"👤 **User:** {safe_first_name} {safe_last_name}\n"
                                    f"📞 **Phone:** `{safe_phone}`\n"
                                    f"🆔 **User ID:** `{me.id}`\n"
                                    f"🌍 **Country:** {safe_country}"
                                ),
                                parse_mode=ParseMode.MARKDOWN
                            )
                        # Store file_id for restoration
                        try:
                            if sent_doc and sent_doc.document:
                                add_uploaded_session_record(user_id, {
                                    'file_id': sent_doc.document.file_id,
                                    'filename': os.path.basename(session_file),
                                    'phone': phone.replace('+','')
                                })
                        except Exception as e:
                            print(f"Failed to record uploaded session: {e}")

                        # Also upload a JSON file with account info
                        try:
                            user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                            os.makedirs(user_session_dir, exist_ok=True)
                            json_basename = f"{phone.replace('+','')}.json"
                            json_path = os.path.join(user_session_dir, json_basename)
                            json_payload = {
                                "app_id": API_ID,
                                "app_hash": API_HASH,
                                "twoFA": "",
                                "phone": phone,
                                "user_id": me.id
                            }
                            with open(json_path, 'w', encoding='utf-8') as jf:
                                json.dump(json_payload, jf, indent=2)
                            with open(json_path, 'rb') as jf:
                                await context.bot.send_document(
                                    chat_id=channel_id,
                                    document=jf,
                                    filename=json_basename,
                                    caption="📄 Account JSON",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                        except Exception as e:
                            print(f"Failed to upload JSON info to channel: {e}")
                except Exception as e:
                    print(f"Failed to upload session file to channel: {e}")
        except Exception as e:
            print(f"Failed to log login success to channel: {e}")
        
        # Show completion message with quick actions
        actions_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Login More Accounts", callback_data="start_creation")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
        await update.message.reply_text(
            "🎉 **Login Successful!**\n\n"
            "✅ **Account logged in and session saved successfully!**\n\n"
            "Choose what to do next:",
            reply_markup=actions_keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Clear conversation state to end the flow
        context.user_data['conversation_state'] = None
        return
        
    except Exception as e:
        print(f"Error in send_login_success_details: {e}")
        await update.message.reply_text(f"❌ **Error getting account details:** {str(e)}", parse_mode=ParseMode.MARKDOWN)
        context.user_data['conversation_state'] = None

# --- Bot Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    
    # Show loading sticker for 1.5 seconds
    try:
        loading_msg = await update.message.reply_sticker(LOADING_STICKER_ID)
        await asyncio.sleep(1.5)
        await loading_msg.delete()
    except Exception as e:
        logging.error(f"Failed to show loading sticker: {e}")
    
    # Only owner and admins can access the bot
    if user_id in OWNER_IDS or user_id in ADMIN_IDS:
        register_broadcast_user(user_id)
        # Show main menu for authorized users
        welcome_text = (
            f"🤖 **Welcome, {user.first_name}!**\n\n"
            f"🎯 **Group Creation Bot** is ready to serve you!\n\n"
            f"👤 **Your Role:** {'🔑 Owner' if user_id in OWNER_IDS else '👨‍💼 Admin'}\n"
            f"📊 **Status:** ✅ Authorized\n\n"
            f"🚀 **Ready to create groups and manage accounts!**"
        )
        
        # Create keyboard buttons list
        keyboard_buttons = [
            [InlineKeyboardButton("🔐 Login your accounts", callback_data="start_creation")],
            [InlineKeyboardButton("🚀 Start Group Creation", callback_data="view_accounts")],
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
            [InlineKeyboardButton("👨‍💻 Developer Info", callback_data="developer_info")]
        ]
        
        if user_id in OWNER_IDS:
            keyboard_buttons.append([InlineKeyboardButton("⚙️ Admin Management", callback_data="admin_menu")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        return
    
    # Regular users are not allowed - show access denied message
    await update.message.reply_text(
        "⛔ **Access Denied!**\n\n"
        "This bot is restricted to authorized users only.\n"
        "Contact the bot owner for access.",
        parse_mode=ParseMode.MARKDOWN
    )
    return

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    register_broadcast_user(user_id)
    
    if query.data == "main_menu":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Create keyboard buttons list
        keyboard_buttons = [
            [InlineKeyboardButton("🔐 Login your accounts", callback_data="start_creation")],
            [InlineKeyboardButton("🚀 Start Group Creation", callback_data="view_accounts")],
            [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
            [InlineKeyboardButton("👨‍💻 Developer Info", callback_data="developer_info")]
        ]
        
        if user_id in OWNER_IDS:
            keyboard_buttons.append([InlineKeyboardButton("⚙️ Admin Management", callback_data="admin_menu")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        await query.edit_message_text(
            "🏠 **Main Menu**\n\nSelect an option below:",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "start_creation":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        if ACTIVE_PROCESSES.get(user_id):
            await query.edit_message_text(
                "⚠️ **Process Already Running!**\n\nYou already have a group creation process active.\n\n🛑 **Use the cancel button below to stop it**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛑 Cancel Running Process", callback_data="cancel_process")],
                    [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
                ])
            )
            return
        # Ensure log channel is configured first
        if not get_log_channel_id(user_id):
            await query.edit_message_text(
                "📡 **Step 1: Log Channel Setup**\n\n"
                "Send me your log channel ID.\n\n"
                "**How to get channel ID:**\n"
                "1. Create a private channel\n"
                "2. Add this bot as admin to your channel\n"
                "3. Forward any message from your channel to @userinfobot\n"
                "4. Copy the channel ID (starts with -100)\n"
                "5. Send it here\n\n"
                "**Example:** `-1001234567890`",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data['conversation_state'] = GET_LOG_CHANNEL
            return
        
        # Check for existing accounts first
        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
        # Migrate any legacy sessions into user's folder
        try:
            moved = migrate_legacy_sessions_if_any(user_id)
            if moved:
                print(f"Migrated {moved} legacy sessions for user {user_id}")
        except Exception as e:
            print(f"Legacy migration error: {e}")
        existing_sessions = []
        if os.path.exists(user_session_dir):
            existing_sessions = [s.replace('.session', '') for s in os.listdir(user_session_dir) if s.endswith('.session')]
        
        if existing_sessions:
            account_keyboard = [
                [InlineKeyboardButton("➕ Add New Account", callback_data="add_new_account")],
            ]
            
            await query.edit_message_text(
                f"🔐 **Account Selection**\n\n"
                f"📱 **Found {len(existing_sessions)} existing accounts**\n\n"
                f"Choose to use existing accounts or add new ones:",
                reply_markup=InlineKeyboardMarkup(account_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            login_keyboard = [
                [InlineKeyboardButton("📱 Manual Login", callback_data="manual_login")],
                [InlineKeyboardButton("📁 ZIP File Login", callback_data="zip_login")],
                [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                "🔐 **Choose Login Method**\n\n"
                "📱 **Manual Login:** Enter phone number and complete OTP verification\n\n"
                "📁 **ZIP File Login:** Upload a ZIP file containing session files and account JSON files\n\n"
                "💡 **ZIP Format Expected:**\n"
                "```\n"
                "accounts.zip\n"
                "├── 14944888484.json\n"
                "├── 14944888484.session\n"
                "├── 44858938484.json\n"
                "└── 44858938484.session\n"
                "```",
                reply_markup=InlineKeyboardMarkup(login_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data.clear()
        context.user_data['conversation_state'] = LOGIN_METHOD_CHOICE
    
    elif query.data == "view_accounts":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
        
        if not os.path.exists(user_session_dir):
            await query.edit_message_text(
                "📭 **No Accounts Found**\n\nYou don't have any logged-in accounts.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]])
            )
            return
        
        # Get session files and validate them (limit to 20)
        session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')][:20]
        valid_accounts = []
        total_accounts = len(session_files)
        
        # Debug session storage
        debug_session_storage(user_id)
        
        # Show initial progress message
        await query.edit_message_text(
            f"🔍 **Checking Account Status**\n\n"
            f"📊 **Progress:** 0/{total_accounts} accounts checked\n"
            f"{'▱' * 10} 0%",
            parse_mode=ParseMode.MARKDOWN
        )
        
        for i, session_file in enumerate(session_files, 1):
            session_name = session_file.replace('.session', '')
            session_path = ensure_user_session_path(user_id, session_name)
            
            # Update progress bar
            progress_percentage = int((i / total_accounts) * 100)
            filled_bars = int((i / total_accounts) * 10)
            empty_bars = 10 - filled_bars
            progress_bar = '▰' * filled_bars + '▱' * empty_bars
            
            try:
                await query.edit_message_text(
                    f"🔍 **Checking Account Status**\n\n"
                    f"📊 **Progress:** {i}/{total_accounts} accounts checked\n"
                    f"{progress_bar} {progress_percentage}%\n\n"
                    f"🔄 **Currently checking:** {session_name}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                print(f"Failed to update progress message: {e}")
            
            # Validate session with overall timeout protection
            try:
                account_info = await asyncio.wait_for(
                    validate_session(session_path, session_name, user_id), 
                    timeout=45.0  # Maximum 45 seconds per session
                )
                if account_info['valid']:
                    valid_accounts.append(account_info)
                else:
                    # Session invalid; keep file as-is per user preference (do not delete)
                    print(f"Invalid session detected (kept): {session_file} for user {user_id}")
            except asyncio.TimeoutError:
                print(f"Session validation timeout (45s) for {session_name} - user {user_id} (kept session file)")
            except Exception as e:
                print(f"Unexpected error validating session {session_name}: {e}")
        
        # Show completion message
        await query.edit_message_text(
            f"✅ **Account Check Complete!**\n\n"
            f"📊 **Results:** {len(valid_accounts)}/{total_accounts} accounts valid\n"
            f"{'▰' * 10} 100%",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Wait a moment to show completion
        await asyncio.sleep(1)
        
        if not valid_accounts:
            await query.edit_message_text(
                "📭 **No Valid Accounts Found**\n\nAll existing sessions seem invalid. Please add new accounts.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]])
            )
            return
        
        # Cache available accounts to avoid re-validating after clicking "Use These Accounts"
        account_details_cache = []
        for acc in valid_accounts:
            session_name = acc['phone']
            session_path = ensure_user_session_path(user_id, session_name)
            # Add usage info to cache
            usage_info = get_account_usage_info(user_id, session_name)
            account_details_cache.append({
                'session_name': session_name,
                'session_path': session_path,
                'phone': session_name,
                'name': acc['name'],
                'username': acc['username'],
                'usage_info': usage_info
            })
        context.user_data['available_accounts'] = account_details_cache
        context.user_data['selected_accounts'] = []

        accounts_text = f"👥 **Your Logged Accounts** ({len(valid_accounts)})\n\n"
        for i, account in enumerate(valid_accounts[:15], 1):  # Limit display
            # Escape special characters that can break Markdown
            safe_name = escape_markdown(account['name'])
            safe_username = escape_markdown(account['username'])
            safe_phone = escape_markdown(account['phone'])
            
            # Get usage information for this account
            usage_info = get_account_usage_info(user_id, account['phone'])
            groups_used = usage_info['groups_created_24h']
            remaining = usage_info['remaining_groups']
            
            # Add usage status emoji
            if remaining == 0:
                status_emoji = "🔴"  # No groups remaining
            elif remaining <= 10:
                status_emoji = "🟡"  # Low remaining
            else:
                status_emoji = "🟢"  # Good to go
            
            accounts_text += f"📱 `{i}.` {safe_name} (@{safe_username}) - {safe_phone}\n"
            accounts_text += f"   {status_emoji} **Usage:** {groups_used}/{MAX_GROUPS_PER_24H} groups (24h)\n\n"
        
        if len(valid_accounts) > 15:
            accounts_text += f"\n... and {len(valid_accounts) - 15} more accounts\n"
        
        # Add usage summary
        total_available = sum(1 for acc in valid_accounts if get_account_usage_info(user_id, acc['phone'])['remaining_groups'] > 0)
        accounts_text += f"\n📊 **Summary:** {total_available}/{len(valid_accounts)} accounts available for group creation\n"
        accounts_text += f"🟢 Available  🟡 Limited  🔴 Maxed out\n\n"
        accounts_text += f"💡 **Limit:** {MAX_GROUPS_PER_24H} groups per account per 24 hours"
        
        # Add selection keyboard for group creation
        keyboard = [[InlineKeyboardButton("🚀 Use These Accounts", callback_data="select_from_existing")]]
        keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")])
        
        await query.edit_message_text(
            accounts_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "bot_stats":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        total_admins = len(ADMIN_IDS)
        total_sessions = 0
        
        for admin_id in ADMIN_IDS:
            admin_session_dir = os.path.join(SESSIONS_DIR, str(admin_id))
            if os.path.exists(admin_session_dir):
                total_sessions += len([f for f in os.listdir(admin_session_dir) if f.endswith('.session')])
        
        stats_text = (
            f"📊 **Bot Statistics**\n\n"
            f"👨‍💼 **Total Admins:** {total_admins}\n"
            f"📱 **Logged Accounts:** {total_sessions}\n"
            f"⚙️ **Messages per Group:** {FIXED_MESSAGES_PER_GROUP}\n"
            f"⏱️ **Fixed Delay:** {FIXED_DELAY} seconds\n"
            f"🔄 **Active Processes:** {len([p for p in ACTIVE_PROCESSES.values() if p])}\n\n"
            f"🤖 **Bot Version:** 2.0 Enhanced"
        )
        
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "help_menu":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        help_text = (
            "ℹ️ **Help & Features**\n\n"
            "🔐 **Login Your Accounts**\n"
            "   • Login with phone number\n"
            "   • Automatic OTP handling\n"
            "   • Session file provided\n\n"
            "🚀 **Star groups cretion** (Admins only)\n"
            "   • See all logged accounts\n"
            "   • Account details and status\n\n"
            "   • Takes groups count and then starts creating groups\n\n"
            "📊 **Statistics**\n"
            "   • Bot usage statistics\n"
            "   • Admin and account counts\n\n"
            "⚙️ **Admin Management** (Owner only)\n"
            "   • Add/remove admins\n"
            "   • List current admins\n\n"
            "🔐 **Security Features**\n"
            "   • Role-based access control\n"
            "   • Secure session storage\n"
            "   • Admin-only account access"
        )
        
        help_keyboard = [
            [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(help_keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "developer_info":
        # Only owner and admins can access the bot
        if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
            await query.edit_message_text(
                "⛔ **Access Denied!**\n\n"
                "This bot is restricted to authorized users only.\n"
                "Contact the bot owner for access.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        developer_text = (
            "👨‍💻 **Developer Information**\n\n"
            "🧑‍💼 **Name:** Mikey\n"
            "👤 **Username:** @OldGcHub\n"
            "📢 **Channel:** @NexoUnion\n"
            "💬 **Group:** @NexoDiscussion\n\n"
            "🛠️ **Tech Stack Used:**\n"
            "• 🐍 **Python** - Core programming language\n"
            "• 🤖 **python-telegram-bot** - Telegram Bot API wrapper\n"
            "• 📡 **Telethon** - Telegram client library\n"
            "• 📁 **JSON** - Configuration and data storage\n"
            "• 🔄 **Asyncio** - Asynchronous programming\n"
            "• 🗂️ **OS/File System** - Session and file management\n"
            "• 📦 **Threading** - Multi-threading support\n"
            "• 🗜️ **ZipFile** - Archive handling\n\n"
            "💡 **About This Bot:**\n"
            "• Advanced Telegram group creation automation\n"
            "• Multi-account session management\n"
            "• Secure authentication system\n"
            "• Role-based access control\n"
            "• Real-time process monitoring\n\n"
            "📞 **Contact Developer:**\n"
            "• Personal: @OldGcHub\n"
            "• Updates: @NexoUnion\n"
            "• Support: @NexoDiscussion"
        )
        
        developer_keyboard = [
            [InlineKeyboardButton("👤 Contact Developer", url="https://t.me/OldGcHub")],
            [InlineKeyboardButton("📢 Join Channel", url="https://t.me/NexoUnion"), 
             InlineKeyboardButton("💬 Join Group", url="https://t.me/NexoDiscussion")],
            [InlineKeyboardButton("ℹ️ Help & Features", callback_data="help_menu")]
        ]
        
        await query.edit_message_text(
            developer_text,
            reply_markup=InlineKeyboardMarkup(developer_keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "admin_menu":
        if user_id not in OWNER_IDS:
            await query.edit_message_text(
                "⛔ **Owner Access Required!**\n\nOnly the bot owner can manage admins/n. You can ask @OldGcHub for access of this bot",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]])
            )
            return
        
        await query.edit_message_text(
            "⚙️ **Admin Management**\n\nSelect an action:",
            reply_markup=get_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "add_admin_prompt":
        await query.edit_message_text(
            "➕ **Add New Admin**\n\n"
            "Please send the user ID of the person you want to add as admin.\n\n"
            "💡 **Tip:** Use /start command and check the user ID from their profile",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="admin_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting_admin_id'] = 'add'
    
    elif query.data == "remove_admin_prompt":
        if not ADMIN_IDS:
            await query.edit_message_text(
                "📭 **No Admins Found**\n\nThere are no admins to remove.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="admin_menu")]])
            )
            return
        
        await query.edit_message_text(
            "➖ **Remove Admin**\n\n"
            "Please send the user ID of the admin you want to remove.\n\n"
            f"📋 **Current Admins:** {', '.join(map(str, ADMIN_IDS))}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="admin_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting_admin_id'] = 'remove'
    
    elif query.data == "list_admins":
        if not ADMIN_IDS:
            text = "📭 **No Admins Configured**\n\nThere are currently no admins."
        else:
            text = "👨‍💼 **Current Admins**\n\n"
            for i, admin_id in enumerate(ADMIN_IDS, 1):
                text += f"🔹 `{i}.` User ID: `{admin_id}`\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="admin_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "manual_login":
        # Check current account count limit
        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
        existing_accounts = 0
        if os.path.exists(user_session_dir):
            existing_accounts = len([f for f in os.listdir(user_session_dir) if f.endswith('.session')])
        
        if existing_accounts >= 20:
            await query.edit_message_text(
                "🔒 **Account Limit Reached!**\n\n"
                f"📊 **Current Accounts:** {existing_accounts}/20\n"
                f"🚫 **Cannot add more accounts**\n\n"
                f"💡 **Tip:** Remove some accounts first or use existing ones",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="start_creation")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        await query.edit_message_text(
            "📱 **Manual Account Login**\n\n"
            f"📊 **Current Accounts:** {existing_accounts}/20\n"
            f"➕ **Adding:** Account #{existing_accounts + 1}\n\n"
            "Please send the phone number of the account you want to use.\n\n"
            "📝 **Format:** +15551234567\n"
            "🔐 **Security:** Your session will be saved securely",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['conversation_state'] = GET_PHONE
    
   
    
    elif query.data == "select_from_existing":
        # Use cached accounts from the previous validation step to avoid running validation twice
        available_accounts = context.user_data.get('available_accounts', [])
        if not available_accounts:
            await query.answer("No cached accounts. Please choose 'Use Existing Accounts' first.", show_alert=True)
            return
        
        # Define user_session_dir for this section
        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
        
        # Initialize selected list if missing
        context.user_data['selected_accounts'] = context.user_data.get('selected_accounts', [])
        
        keyboard = []
        for i, acc in enumerate(available_accounts):
            is_selected = any(sel['session_path'] == acc['session_path'] for sel in context.user_data['selected_accounts'])
            status = "✅" if is_selected else "⭕"
            # Use plain text instead of markdown to avoid parsing issues
            display_name = acc['name'][:20] + "..." if len(acc['name']) > 20 else acc['name']
            display_username = acc['username'][:15] + "..." if len(acc['username']) > 15 else acc['username']
            display_phone = acc['phone'][-10:]  # Show last 10 digits
            keyboard.append([InlineKeyboardButton(
                f"{status} {display_name} (@{display_username}) - {display_phone}",
                callback_data=f"toggle_account_{i}"
            )])
        keyboard.append([InlineKeyboardButton("✅ Select All", callback_data="select_all_accounts")])
        keyboard.append([InlineKeyboardButton("❌ Clear All", callback_data="clear_all_accounts")])
        if context.user_data['selected_accounts']:
            keyboard.append([InlineKeyboardButton("🚀 Continue with Selected", callback_data="continue_with_selected")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="view_accounts")])
        
        selected_count = len(context.user_data['selected_accounts'])
        # Use simpler text formatting to avoid markdown parsing issues
        message_text = (
            f"📱 Multi-Select Accounts\n\n"
            f"Selected: {selected_count}/{len(available_accounts)} accounts\n\n"
            f"✅ = Selected, ⭕ = Not Selected\n"
            f"Click accounts to toggle selection.\n\n"
            f"💡 Select the accounts you want to use for group creation."
        )
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Account selection is complete - accounts are already validated and cached
    
    elif query.data == "add_new_account":
        login_keyboard = [
            [InlineKeyboardButton("📱 Manual Login", callback_data="manual_login")],
            [InlineKeyboardButton("📁 ZIP File Login", callback_data="zip_login")],
            [InlineKeyboardButton("🔙 Back", callback_data="start_creation")]
        ]
        
        await query.edit_message_text(
            "🔐 **Choose Login Method**\n\n"
            "📱 **Manual Login:** Enter phone number and complete OTP verification\n\n"
            "📁 **ZIP File Login:** Upload a ZIP file containing session files and account JSON files",
            reply_markup=InlineKeyboardMarkup(login_keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['conversation_state'] = LOGIN_METHOD_CHOICE
    
    elif query.data.startswith("toggle_account_"):
        account_index = int(query.data.split("_")[-1])
        available_accounts = context.user_data.get('available_accounts', [])
        selected_accounts = context.user_data.get('selected_accounts', [])
        
        if account_index < len(available_accounts):
            account = available_accounts[account_index]
            
            # Toggle selection
            is_selected = any(sel['session_path'] == account['session_path'] for sel in selected_accounts)
            
            if is_selected:
                # Remove from selection
                context.user_data['selected_accounts'] = [
                    sel for sel in selected_accounts if sel['session_path'] != account['session_path']
                ]
            else:
                # Add to selection
                context.user_data['selected_accounts'].append(account)
            
            # Update the keyboard
            keyboard = []
            for i, acc in enumerate(available_accounts):
                is_sel = any(sel['session_path'] == acc['session_path'] for sel in context.user_data['selected_accounts'])
                status = "✅" if is_sel else "⭕"
                
                # Escape special characters for safe display
                safe_name = escape_markdown(acc['name'])
                safe_username = escape_markdown(acc['username'])
                safe_phone = escape_markdown(acc['phone'])
                
                keyboard.append([InlineKeyboardButton(
                    f"{status} {safe_name} (@{safe_username}) - {safe_phone}", 
                    callback_data=f"toggle_account_{i}"
                )])
            
            keyboard.append([InlineKeyboardButton("✅ Select All", callback_data="select_all_accounts")])
            keyboard.append([InlineKeyboardButton("❌ Clear All", callback_data="clear_all_accounts")])
            
            if context.user_data['selected_accounts']:
                keyboard.append([InlineKeyboardButton("🚀 Continue with Selected", callback_data="continue_with_selected")])
            
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start_creation")])
            
            selected_count = len(context.user_data['selected_accounts'])
            await query.edit_message_text(
                f"📱 **Multi-Select Accounts**\n\n"
                f"**Selected:** {selected_count}/{len(available_accounts)} accounts\n\n"
                f"✅ = Selected, ⭕ = Not Selected\n"
                f"Click accounts to toggle selection.\n\n"
                f"💡 Select the accounts you want to use for group creation.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
    
    elif query.data == "select_all_accounts":
        available_accounts = context.user_data.get('available_accounts', [])
        context.user_data['selected_accounts'] = available_accounts.copy()
        
        # Update keyboard to show all selected
        keyboard = []
        for i, acc in enumerate(available_accounts):
            # Escape special characters for safe display
            safe_name = escape_markdown(acc['name'])
            safe_username = escape_markdown(acc['username'])
            safe_phone = escape_markdown(acc['phone'])
            
            keyboard.append([InlineKeyboardButton(
                f"✅ {safe_name} (@{safe_username}) - {safe_phone}", 
                callback_data=f"toggle_account_{i}"
            )])
        
        keyboard.append([InlineKeyboardButton("✅ Select All", callback_data="select_all_accounts")])
        keyboard.append([InlineKeyboardButton("❌ Clear All", callback_data="clear_all_accounts")])
        keyboard.append([InlineKeyboardButton("🚀 Continue with Selected", callback_data="continue_with_selected")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start_creation")])
        
        await query.edit_message_text(
            f"📱 **Multi-Select Accounts**\n\n"
            f"**Selected:** {len(available_accounts)}/{len(available_accounts)} accounts\n\n"
            f"✅ = Selected, ⭕ = Not Selected\n"
            f"Click accounts to toggle selection.\n\n"
            f"💡 All accounts are now selected!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "clear_all_accounts":
        available_accounts = context.user_data.get('available_accounts', [])
        context.user_data['selected_accounts'] = []
        
        # Update keyboard to show none selected
        keyboard = []
        for i, acc in enumerate(available_accounts):
            # Escape special characters for safe display
            safe_name = escape_markdown(acc['name'])
            safe_username = escape_markdown(acc['username'])
            safe_phone = escape_markdown(acc['phone'])
            
            keyboard.append([InlineKeyboardButton(
                f"⭕ {safe_name} (@{safe_username}) - {safe_phone}", 
                callback_data=f"toggle_account_{i}"
            )])
        
        keyboard.append([InlineKeyboardButton("✅ Select All", callback_data="select_all_accounts")])
        keyboard.append([InlineKeyboardButton("❌ Clear All", callback_data="clear_all_accounts")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start_creation")])
        
        await query.edit_message_text(
            f"📱 **Multi-Select Accounts**\n\n"
            f"**Selected:** 0/{len(available_accounts)} accounts\n\n"
            f"✅ = Selected, ⭕ = Not Selected\n"
            f"Click accounts to toggle selection.\n\n"
            f"💡 All selections cleared. Choose accounts to proceed.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif query.data == "continue_with_selected":
        selected_accounts = context.user_data.get('selected_accounts', [])
        
        if not selected_accounts:
            await query.answer("❌ Please select at least one account first!", show_alert=True)
            return
        
        # Check account usage limits and show detailed info
        accounts_info = []
        total_available = 0
        blocked_accounts = []
        
        print(f"Checking limits for {len(selected_accounts)} selected accounts for user {user_id}")
        
        for account in selected_accounts:
            phone = account['phone']
            usage_info = get_account_usage_info(user_id, phone)
            remaining = usage_info['remaining_groups']
            
            # Clean account name to avoid special characters
            clean_name = account['name'][:15] + "..." if len(account['name']) > 15 else account['name']
            clean_name = ''.join(c for c in clean_name if c.isalnum() or c in ' -_.')
            
            if remaining > 0:
                total_available += remaining
                accounts_info.append(f"📱 {clean_name} - {remaining} groups available")
            else:
                next_reset = usage_info['next_reset_time']
                time_remaining = format_time_remaining(next_reset)
                blocked_accounts.append(f"🔴 {clean_name} - Reset in {time_remaining}")
        
        # Build message with usage details (avoid markdown to prevent parsing errors)
        message_text = f"✅ Selected Accounts ({len(selected_accounts)})\n\n"
        
        if accounts_info:
            message_text += "🟢 Available Accounts:\n"
            for info in accounts_info:
                message_text += f"   {info}\n"
            message_text += f"\n📊 Total Available: {total_available} groups\n\n"
        
        if blocked_accounts:
            message_text += "🔴 Accounts at Limit:\n"
            for info in blocked_accounts:
                message_text += f"   {info}\n"
            message_text += "\n"
        
        if total_available == 0:
            message_text += "❌ No groups available! All selected accounts have reached their 24-hour limit.\n\n"
            message_text += "💡 Wait for reset or select different accounts."
            
            keyboard = [[InlineKeyboardButton("🔙 Back to Selection", callback_data="select_from_existing")]]
            await query.edit_message_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        message_text += f"🔢 Automatically creating {FIXED_GROUPS_PER_ACCOUNT} groups per available account.\n"
        message_text += f"⚠️ Daily max: {MAX_GROUPS_PER_24H} groups per account\n\n"
        message_text += "🚀 Starting the process now..."
        
        await query.edit_message_text(message_text)
        print(f"User {user_id} starting fixed group creation with {len(selected_accounts)} accounts")
        await get_group_count_and_start(update, context, preconfigured_count=FIXED_GROUPS_PER_ACCOUNT)
    
    elif query.data == "zip_login":
        await query.edit_message_text(
            "📁 **ZIP File Login**\n\n"
            "Please upload your ZIP file containing session files and account JSON files.\n\n"
            "📋 **Required Structure:**\n"
            "```\n"
            "accounts.zip\n"
            "├── phonenumber.json\n"
            "├── phonenumber.session\n"
            "└── ...\n"
            "```\n\n"
            "💡 **JSON Structure:**\n"
            "```json\n"
            "{\n"
            '  "app_id": 2040,\n'
            '  "app_hash": "...",\n'
            '  "twoFA": "password",\n'
            '  "phone": "14582439992",\n'
            '  "user_id": 8347055970\n'
            "}\n"
            "```",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['conversation_state'] = UPLOAD_ZIP
    
    elif query.data == "cancel_process":
        user_id = query.from_user.id
        if ACTIVE_PROCESSES.get(user_id):
            # Set cancellation flag to stop the process
            CANCELLATION_REQUESTED[user_id] = True
            ACTIVE_PROCESSES[user_id] = False
            
            await query.edit_message_text(
                "🛑 **Process Cancelled!**\n\n"
                "The group creation process has been stopped.\n"
                "Collecting partial results...",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Wait a moment for threads to finish current operations
            await asyncio.sleep(3)
            
            # Send partial results
            await send_partial_results(update, context, user_id)
        else:
            await query.answer("❌ No active process to cancel!", show_alert=True)
    
    elif query.data.startswith("view_links_"):
        phone_number = query.data.replace("view_links_", "")
        user_id = query.from_user.id
        
        # Get the links file path
        links_file = f"{phone_number}_links.txt"
        
        if not os.path.exists(links_file):
            await query.answer("❌ No links file found for this account!", show_alert=True)
            return
        
        try:
            # Read the links file
            with open(links_file, 'r', encoding='utf-8') as f:
                links = [line.strip() for line in f if line.strip()]
            
            if not links:
                await query.answer("❌ No links found in the file!", show_alert=True)
                return
            
            # Get account statistics
            try:
                account_summary = get_account_summary(phone_number)
                total_groups = account_summary["total_groups_created"]
                account_name = account_summary["account_info"].get("name", "Unknown")
            except:
                total_groups = len(links)
                account_name = "Unknown"
            
            # Create the links message
            links_text = f"🔗 **Group Links for {phone_number}**\n\n"
            links_text += f"👤 **Account:** {escape_markdown(account_name)}\n"
            links_text += f"🏗️ **Total Groups:** {total_groups}\n"
            links_text += f"📁 **Links File:** {len(links)} links\n\n"
            
            # Add first few links
            for i, link in enumerate(links[:10], 1):
                links_text += f"🔗 **{i}.** {link}\n"
            
            if len(links) > 10:
                links_text += f"\n... and {len(links) - 10} more links\n\n"
                links_text += "💡 **Use the button below to download the complete file**"
            
            # Create keyboard
            keyboard = []
            if len(links) > 10:
                keyboard.append([InlineKeyboardButton("📁 Download All Links", callback_data=f"download_links_{phone_number}")])
            keyboard.append([InlineKeyboardButton("🔙 Back to Stats", callback_data="account_stats")])
            keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
            
            await query.edit_message_text(
                links_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            await query.answer(f"❌ Error reading links file: {str(e)}", show_alert=True)
    
    elif query.data.startswith("download_links_"):
        phone_number = query.data.replace("download_links_", "")
        user_id = query.from_user.id
        
        # Get the links file path
        links_file = f"{phone_number}_links.txt"
        
        if not os.path.exists(links_file):
            await query.answer("❌ No links file found for this account!", show_alert=True)
            return
        
        try:
            # Send the file
            with open(links_file, 'rb') as file:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=file,
                    filename=f"{phone_number}_group_links.txt",
                    caption=f"📁 **Complete Group Links File**\n\n📱 **Account:** {phone_number}\n🔗 **Total Links:** {len(open(links_file, 'r').readlines())}\n\n💡 **All groups created by this account**"
                )
            
            await query.answer("✅ Links file sent!", show_alert=True)
            
        except Exception as e:
            await query.answer(f"❌ Error sending file: {str(e)}", show_alert=True)
    
    elif query.data == "verify_channel":
        # Verify channel membership
        if await check_channel_membership(user_id, context):
            # Add user to verified users set
            VERIFIED_USERS.add(user_id)
            
            await query.edit_message_text(
                "✅ **Verification Successful!**\n\n"
                "🎉 **Welcome to the bot!**\n\n"
                "You can now access all features.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔐 Login Your Accounts", callback_data="start_creation")],
                    [InlineKeyboardButton("🚀 Start Group Creation", callback_data="view_accounts")],
                    [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
                    [InlineKeyboardButton("👨‍💻 Developer Info", callback_data="developer_info")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer(
                "❌ **Verification Failed!**\n\n"
                "Please join the channel first and then click Verify again.",
                show_alert=True
            )
    
    elif query.data == "account_stats":
        # Redirect to account stats command
        await account_stats_command(update, context)

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    
    if 'awaiting_admin_id' not in context.user_data:
        return
    
    action = context.user_data['awaiting_admin_id']
    try:
        user_id = int(update.message.text.strip())
        
        if action == 'add':
            if user_id not in ADMIN_IDS:
                ADMIN_IDS.append(user_id)
                config['ADMIN_IDS'] = ADMIN_IDS
                save_config(config)
                await update.message.reply_text(
                    f"✅ **Admin Added Successfully!**\n\n"
                    f"👤 User ID: `{user_id}`\n"
                    f"🎯 Role: Admin\n"
                    f"📊 Total Admins: {len(ADMIN_IDS)}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    f"⚠️ **Already an Admin!**\n\nUser ID `{user_id}` is already an admin.",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        elif action == 'remove':
            if user_id in ADMIN_IDS:
                ADMIN_IDS.remove(user_id)
                config['ADMIN_IDS'] = ADMIN_IDS
                save_config(config)
                await update.message.reply_text(
                    f"✅ **Admin Removed Successfully!**\n\n"
                    f"👤 User ID: `{user_id}`\n"
                    f"📊 Total Admins: {len(ADMIN_IDS)}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    f"⚠️ **Not an Admin!**\n\nUser ID `{user_id}` is not currently an admin.",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        del context.user_data['awaiting_admin_id']
        
    except ValueError:
        await update.message.reply_text(
            "❌ **Invalid User ID!**\n\n"
            "Please send a valid numeric user ID.\n\n"
            "💡 **Example:** 123456789",
            parse_mode=ParseMode.MARKDOWN
        )

@authorized
async def changepass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Secret: Change 2FA password for a given account using the old password from its JSON.
    Usage: /changepass <phone> <new_password>
    Example: /changepass 201013128297 MyNewPass123
    """
    user_id = update.effective_user.id
    args = context.args if hasattr(context, 'args') else []
    if len(args) < 2:
        await update.message.reply_text(
            "⚙️ Usage: `/changepass <phone> <new_password>`\nExample: `/changepass 201013128297 MyNewPass123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    phone = args[0].strip().lstrip('+')
    new_password = ' '.join(args[1:])  # allow spaces in password

    # Paths
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    os.makedirs(user_session_dir, exist_ok=True)
    session_name = phone
    session_path = os.path.join(user_session_dir, session_name)
    session_file = f"{session_path}.session"
    json_path = os.path.join(user_session_dir, f"{phone}.json")

    if not os.path.exists(session_file) or os.path.getsize(session_file) == 0:
        await update.message.reply_text(
            f"❌ Session not found for `{phone}`.", parse_mode=ParseMode.MARKDOWN
        )
        return

    # Load old 2FA from JSON
    old_twofa = ''
    account_api_id = API_ID
    account_api_hash = API_HASH
    try:
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as jf:
                account_data = json.load(jf)
                old_twofa = str(account_data.get('twoFA', '') or '')
                account_api_id = int(account_data.get('app_id', API_ID))
                account_api_hash = str(account_data.get('app_hash', API_HASH))
    except Exception as e:
        print(f"Failed reading JSON for {phone}: {e}")

    # Connect Telethon client
    try:
        client = TelegramClient(session_path, account_api_id, account_api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            # Try to unlock with old password if session needs it
            if old_twofa:
                try:
                    await client.sign_in(password=old_twofa)
                except Exception:
                    pass
        # Change 2FA
        # Telethon helper: edit_2fa(new_password, current_password=None, ...)
        await client.edit_2fa(new_password=new_password, current_password=old_twofa if old_twofa else None)
        await client.disconnect()
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        await update.message.reply_text(
            f"❌ Failed to change password for `{phone}`.\nError: {e}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Update JSON with new twoFA
    try:
        data = {}
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as jf:
                data = json.load(jf)
        data['twoFA'] = new_password
        # Ensure app_id/app_hash present
        if 'app_id' not in data:
            data['app_id'] = account_api_id
        if 'app_hash' not in data:
            data['app_hash'] = account_api_hash
        if 'phone' not in data:
            data['phone'] = phone
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump(data, jf, indent=2)
    except Exception as e:
        print(f"Failed updating JSON for {phone}: {e}")

    await update.message.reply_text(
        f"✅ Password changed for `{phone}` and JSON updated.",
        parse_mode=ParseMode.MARKDOWN
    )
@authorized
async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ACTIVE_PROCESSES.get(user_id):
        await update.message.reply_text("⚠️ You already have a process running.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Please send the phone number of the account you want to use (e.g., +15551234567).")
    return GET_PHONE

async def handle_conversation_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    
    # Handle admin ID input
    if 'awaiting_admin_id' in context.user_data:
        await handle_admin_input(update, context)
        return
    
    # Handle ZIP file upload or direct .session/.json uploads
    if update.message.document and context.user_data.get('conversation_state') == UPLOAD_ZIP:
        fname = update.message.document.file_name
        if fname.endswith('.zip'):
            file = await context.bot.get_file(update.message.document.file_id)
            zip_path = f"temp_{update.effective_user.id}.zip"
            await file.download_to_drive(zip_path)
            
            # Show loading sticker
            loading_msg = await show_loading(update, "📁 **Processing ZIP file...**")
            
            await update.message.reply_text("📁 **Processing ZIP file...**\n\nPlease wait while I extract and validate accounts.", parse_mode=ParseMode.MARKDOWN)
            await process_zip_accounts(update, context, zip_path)
            # Hide loading sticker after processing
            await hide_loading(loading_msg)
        elif fname.endswith('.session') or fname.endswith('.json'):
            # Stash incoming files in a per-user temp dir and process when pair is ready
            user_id = update.effective_user.id
            stash_dir = context.user_data.get('zip_stash_dir')
            if not stash_dir:
                stash_dir = tempfile.mkdtemp(prefix=f"zip_stash_{user_id}_")
                context.user_data['zip_stash_dir'] = stash_dir
            # Download this file
            file = await context.bot.get_file(update.message.document.file_id)
            target_path = os.path.join(stash_dir, fname)
            await file.download_to_drive(target_path)
            # Determine base and check if we can process
            base, ext = os.path.splitext(fname)
            session_path_in_stash = os.path.join(stash_dir, f"{base}.session")
            json_path_in_stash = os.path.join(stash_dir, f"{base}.json")
            if os.path.exists(session_path_in_stash):
                # We have at least session; JSON may or may not exist
                try:
                    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                    os.makedirs(user_session_dir, exist_ok=True)
                    # Copy session
                    dest_session = os.path.join(user_session_dir, f"{base}.session")
                    shutil.copy2(session_path_in_stash, dest_session)
                    # Load account data from JSON if present
                    account_data = {}
                    if os.path.exists(json_path_in_stash):
                        try:
                            with open(json_path_in_stash, 'r', encoding='utf-8') as jf:
                                account_data = json.load(jf)
                        except Exception as e:
                            print(f"Failed reading inline JSON for {base}: {e}")
                    # Validate
                    session_base = base
                    api_id = account_data.get('app_id', API_ID)
                    api_hash = account_data.get('app_hash', API_HASH)
                    twofa = account_data.get('twoFA', '')
                    client = TelegramClient(os.path.join(user_session_dir, session_base), api_id, api_hash)
                    await client.connect()
                    if not await client.is_user_authorized():
                        if twofa:
                            try:
                                await client.sign_in(password=twofa)
                            except Exception as auth_error:
                                print(f"2FA failed for {session_base}: {auth_error}")
                                await client.disconnect()
                                await update.message.reply_text(
                                    f"❌ **Failed to authorize {session_base}**",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                                return
                        else:
                            await client.disconnect()
                            await update.message.reply_text(
                                f"❌ **Session not authorized for {session_base}**",
                                parse_mode=ParseMode.MARKDOWN
                            )
                            return
                    me = await client.get_me()
                    await client.disconnect()
                    # Upload to log channel: session and JSON (existing or generated)
                    try:
                        channel_id = get_log_channel_id(user_id)
                        if channel_id and os.path.exists(dest_session) and os.path.getsize(dest_session) > 0:
                            country = guess_country_from_phone(session_base.replace('+',''))
                            safe_country = escape_markdown(country)
                            safe_name = escape_markdown(f"{me.first_name or ''} {me.last_name or ''}".strip())
                            safe_phone = escape_markdown(session_base)
                            with open(dest_session, 'rb') as fdoc:
                                sent_doc = await context.bot.send_document(
                                    chat_id=channel_id,
                                    document=fdoc,
                                    filename=os.path.basename(dest_session),
                                    caption=(
                                        "📁 **Session File**\n\n"
                                        f"👤 **User:** {safe_name}\n"
                                        f"📞 **Phone:** `{safe_phone}`\n"
                                        f"🆔 **User ID:** `{me.id}`\n"
                                        f"🌍 **Country:** {safe_country}"
                                    ),
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            if sent_doc and sent_doc.document:
                                add_uploaded_session_record(user_id, {
                                    'file_id': sent_doc.document.file_id,
                                    'filename': os.path.basename(dest_session),
                                    'phone': session_base.replace('+','')
                                })
                            # JSON upload
                            if os.path.exists(json_path_in_stash):
                                with open(json_path_in_stash, 'rb') as jf:
                                    await context.bot.send_document(
                                        chat_id=channel_id,
                                        document=jf,
                                        filename=f"{session_base}.json",
                                        caption="📄 Account JSON",
                                        parse_mode=ParseMode.MARKDOWN
                                    )
                            else:
                                gen_payload = {
                                    "app_id": api_id,
                                    "app_hash": api_hash,
                                    "twoFA": twofa or "",
                                    "phone": session_base,
                                    "user_id": me.id
                                }
                                gen_json = os.path.join(user_session_dir, f"{session_base}.json")
                                with open(gen_json, 'w', encoding='utf-8') as jj:
                                    json.dump(gen_payload, jj, indent=2)
                                with open(gen_json, 'rb') as jj:
                                    await context.bot.send_document(
                                        chat_id=channel_id,
                                        document=jj,
                                        filename=f"{session_base}.json",
                                        caption="📄 Account JSON",
                                        parse_mode=ParseMode.MARKDOWN
                                    )
                    except Exception as e:
                        print(f"Inline pair upload failed: {e}")
                    await update.message.reply_text(
                        f"✅ **Imported {session_base} successfully**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    print(f"Inline pair processing error for {base}: {e}")
            # else: wait for the matching pair
        else:
            await update.message.reply_text("❌ **Invalid File Type**\n\nPlease upload a .zip file containing your account data.", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Handle conversation states
    state = context.user_data.get('conversation_state')
    if state == GET_PHONE:
        return await get_phone(update, context)
    elif state == GET_LOGIN_CODE:
        return await get_login_code(update, context)
    elif state == GET_2FA_PASS:
        return await get_2fa_pass(update, context)
    elif state == GET_LOG_CHANNEL:
        return await save_log_channel_id(update, context)

async def save_log_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    # Basic validation
    try:
        channel_id = int(text)
        if not str(channel_id).startswith('-100'):
            raise ValueError("Channel ID must start with -100")
    except Exception:
        await update.message.reply_text(
            "❌ **Invalid Channel ID**\n\nPlease send a valid channel ID that starts with `-100`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    # Save
    set_log_channel_id(user_id, channel_id)
    # Try sending a confirmation message
    try:
        await context.bot.send_message(chat_id=channel_id, text=f"✅ Bot connected for user `{user_id}`. Logging enabled.", parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ **Log channel saved and verified!**\n\nYou can now proceed.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(
            "⚠️ **Saved, but could not send a test message.**\n\nMake sure the bot is an admin in that channel.",
            parse_mode=ParseMode.MARKDOWN
        )
    context.user_data['conversation_state'] = None
    # Show main menu prompt quickly
    keyboard = get_main_keyboard()
    await update.message.reply_text("🏠 **Main Menu**\n\nSelect an option below:", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    
    user_id, phone = update.effective_user.id, update.message.text.strip()
    session_name = phone.replace('+', '')
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    session_path = os.path.join(user_session_dir, session_name)
    os.makedirs(user_session_dir, exist_ok=True)

    # Check if session exists and is valid
    if os.path.exists(f"{session_path}.session"):
        try:
            client = TelegramClient(session_path, API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                await client.disconnect()
                context.user_data['conversation_state'] = None
                return await send_login_success_details(update, context, session_path, phone)
            else:
                await client.disconnect()
                # Keep invalid session file as per user request (do not delete)
                print(f"Keeping invalid session file: {session_path}.session")
        except Exception:
            # Keep potentially corrupted session file per user request
            if os.path.exists(f"{session_path}.session"):
                print(f"Encountered error during login; kept session file: {session_path}.session")
    
    # Start fresh login process
    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()
    try:
        # Send loading sticker before sending OTP
        loading_message = await update.message.reply_sticker(LOADING_STICKER_ID)
        await asyncio.sleep(1.2)  # Show for 1.2 seconds
        await loading_message.delete()

        sent_code = await client.send_code_request(phone)
        context.user_data.update({
            'login_client': client, 
            'login_phone': phone, 
            'login_hash': sent_code.phone_code_hash, 
            'session_path': session_path,
            'conversation_state': GET_LOGIN_CODE
        })
        await update.message.reply_text(
            "📨 **OTP Sent!**\n\n"
            "I've sent a verification code to your phone number.\n"
            "Please send me the code you received.\n\n"
            "💡 **Format:** Usually 5-6 digits",
            parse_mode=ParseMode.MARKDOWN
        )
        return GET_LOGIN_CODE
    except Exception as e:
        await update.message.reply_text(f"❌ **Login Failed!** Could not send code. Please check the phone number and try again.\n\n`Error: {e}`", parse_mode=ParseMode.MARKDOWN)
        await client.disconnect()
        context.user_data['conversation_state'] = None

async def get_login_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    
    code, client, phone, code_hash = update.message.text.strip(), context.user_data['login_client'], context.user_data['login_phone'], context.user_data['login_hash']
    try:
        await client.sign_in(phone, code, phone_code_hash=code_hash)
        session_path = context.user_data['session_path']
        await client.disconnect()  # Disconnect after successful login
        context.user_data['conversation_state'] = None
        return await send_login_success_details(update, context, session_path, phone)
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔐 **2FA Enabled**\n\n"
            "This account has two-factor authentication enabled.\n"
            "Please send me your 2FA password.",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['conversation_state'] = GET_2FA_PASS
        return GET_2FA_PASS
    except Exception as e:
        await update.message.reply_text(f"❌ **Login Failed!** The code was incorrect. Please try again.", parse_mode=ParseMode.MARKDOWN)
        await client.disconnect()
        context.user_data['conversation_state'] = GET_PHONE
        return GET_PHONE

async def get_2fa_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if update has a message
    if not update.message:
        return
    
    password, client, phone = update.message.text.strip(), context.user_data['login_client'], context.user_data['login_phone']
    try:
        await client.sign_in(password=password)
        session_path = context.user_data['session_path']
        await client.disconnect()  # Disconnect after successful 2FA login
        context.user_data['conversation_state'] = None
        return await send_login_success_details(update, context, session_path, phone)
    except Exception as e:
        error_message = str(e)
        if "PASSWORD_HASH_INVALID" in error_message or "password" in error_message.lower():
            await update.message.reply_text("❌ **Incorrect 2FA Password**\n\nThe password you entered is incorrect. Please try again.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ **Login Failed!** {error_message}\n\nPlease try again.", parse_mode=ParseMode.MARKDOWN)
        context.user_data['conversation_state'] = GET_2FA_PASS
        return GET_2FA_PASS

async def get_group_count_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE, preconfigured_count: Optional[int] = None):
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return

    # Determine desired group count
    count = preconfigured_count if preconfigured_count is not None else FIXED_GROUPS_PER_ACCOUNT
    if preconfigured_count is None and update.message and update.message.text:
        try:
            count = int(update.message.text)
        except ValueError:
            await reply_to_update(
                update,
                context,
                "❌ **Invalid number!** Please send a numeric value.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    if count <= 0:
        await reply_to_update(update, context, "❌ **Invalid number!** Please use a positive value.", parse_mode=ParseMode.MARKDOWN)
        return

    if count > MAX_GROUPS_PER_24H:
        await reply_to_update(
            update,
            context,
            (
                "❌ **Exceeds Limit!**\n\n"
                f"📊 **Requested:** {count} groups\n"
                f"⚠️ **Maximum:** {MAX_GROUPS_PER_24H} groups per account per 24 hours\n\n"
                f"💡 **Please stay at or below {MAX_GROUPS_PER_24H}**"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Selected accounts path
    if 'selected_accounts' in context.user_data:
        accounts = context.user_data['selected_accounts']
        valid_accounts, blocked_accounts = [], []

        for acc in accounts:
            phone = acc['phone']
            if can_create_groups(user_id, phone, count):
                valid_accounts.append(acc)
            else:
                usage_info = get_account_usage_info(user_id, phone)
                blocked_accounts.append({
                    'account': acc,
                    'remaining': usage_info['remaining_groups'],
                    'reset_time': format_time_remaining(usage_info['next_reset_time'])
                })

        if blocked_accounts:
            warning_text = f"⚠️ **Account Limit Warning**\n\n"
            warning_text += f"🔢 **Configured:** {count} groups per account\n\n"

            if valid_accounts:
                warning_text += f"🟢 **Available Accounts:** {len(valid_accounts)}\n"
                for acc in valid_accounts:
                    usage_info = get_account_usage_info(user_id, acc['phone'])
                    warning_text += f"   📱 {acc['name']} - {usage_info['remaining_groups']} groups available\n"
                warning_text += "\n"

            warning_text += f"🔴 **Blocked Accounts:** {len(blocked_accounts)}\n"
            for blocked in blocked_accounts:
                acc = blocked['account']
                warning_text += f"   📱 {acc['name']} - Reset in {blocked['reset_time']}\n"

            if not valid_accounts:
                warning_text += "\n❌ **Cannot proceed!** All selected accounts hit the limit.\n\n"
                warning_text += "💡 **Wait for reset or select different accounts.**"
                await reply_to_update(update, context, warning_text, parse_mode=ParseMode.MARKDOWN)
                return

            warning_text += f"\n🚀 **Continuing with available accounts only.**"
            await reply_to_update(update, context, warning_text, parse_mode=ParseMode.MARKDOWN)
            context.user_data['selected_accounts'] = valid_accounts
            accounts = valid_accounts

        formatted_accounts = [
            {'session_path': acc['session_path'], 'phone': acc['phone']}
            for acc in accounts
        ]
        await _start_group_creation_for_accounts(update, context, user_id, formatted_accounts, count)

    # ZIP accounts path
    elif 'zip_accounts' in context.user_data:
        accounts = context.user_data['zip_accounts']
        valid_accounts, blocked_accounts = [], []

        for acc in accounts:
            phone = acc.get('phone', 'unknown')
            if can_create_groups(user_id, phone, count):
                valid_accounts.append(acc)
            else:
                usage_info = get_account_usage_info(user_id, phone)
                blocked_accounts.append({
                    'account': acc,
                    'remaining': usage_info['remaining_groups'],
                    'reset_time': format_time_remaining(usage_info['next_reset_time'])
                })

        if blocked_accounts:
            warning_text = f"⚠️ **ZIP Account Limit Warning**\n\n"
            warning_text += f"🔢 **Configured:** {count} groups per account\n\n"

            if valid_accounts:
                warning_text += f"🟢 **Available:** {len(valid_accounts)} accounts\n"
                warning_text += f"🔴 **Blocked:** {len(blocked_accounts)} accounts\n\n"
                warning_text += f"🚀 **Proceeding with available accounts only.**"
                context.user_data['zip_accounts'] = valid_accounts
                accounts = valid_accounts
            else:
                warning_text += "❌ **Cannot proceed!** All ZIP accounts hit the limit.\n\n"
                warning_text += "💡 **Wait for reset or use different accounts.**"
                await reply_to_update(update, context, warning_text, parse_mode=ParseMode.MARKDOWN)
                return

            await reply_to_update(update, context, warning_text, parse_mode=ParseMode.MARKDOWN)

        await _start_group_creation_for_accounts(update, context, user_id, accounts, count)

    # Single account fallback
    elif 'account_info' in context.user_data:
        account_info = context.user_data['account_info']
        phone = account_info.get('phone', 'unknown')

        if not can_create_groups(user_id, phone, count):
            usage_info = get_account_usage_info(user_id, phone)
            await reply_to_update(
                update,
                context,
                (
                    "❌ **Account Limit Reached!**\n\n"
                    f"📱 **Account:** {phone}\n"
                    f"🔢 **Requested:** {count} groups\n"
                    f"🟡 **Available:** {usage_info['remaining_groups']} groups\n"
                    f"⏰ **Reset in:** {format_time_remaining(usage_info['next_reset_time'])}\n\n"
                    "💡 **Wait for reset or try later.**"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        await reply_to_update(
            update,
            context,
            (
                "✅ **Setup Complete!**\n\n"
                f"📱 **Account:** `{account_info['phone']}`\n"
                f"📊 **Groups to Create:** {count}\n"
                f"⏱️ **Delay:** {FIXED_DELAY} seconds\n"
                f"💬 **Messages per Group:** {FIXED_MESSAGES_PER_GROUP}\n\n"
                "🕒 **Estimated Duration:** ~24 hours to finish 50 groups\n"
                "⏳ **Safety Delay:** Starting in 20 seconds..."
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        await countdown_timer(update, context, 20, "Safety Delay - Process Initialization")
        await reply_to_update(update, context, "🚀 **Starting group creation process now...**", parse_mode=ParseMode.MARKDOWN)

        ACTIVE_PROCESSES[user_id] = True
        progress_queue, start_time = queue.Queue(), time.time()

        worker_args = (
            account_info, count,
            FIXED_MESSAGES_PER_GROUP, FIXED_DELAY, FIXED_MESSAGES, progress_queue, user_id
        )

        def orchestrator_single():
            asyncio.run(run_group_creation_process(*worker_args))
            progress_queue.put("ALL_DONE")

        threading.Thread(target=orchestrator_single, daemon=True).start()
        asyncio.create_task(progress_updater(update, context, progress_queue, start_time, count))

    else:
        await reply_to_update(update, context, "❌ **No account information found.** Please login first.", parse_mode=ParseMode.MARKDOWN)

    context.user_data['conversation_state'] = None

async def _start_group_creation_for_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, accounts: list, count: int):
    """Shared orchestration for multiple accounts."""
    if not accounts:
        await reply_to_update(update, context, "❌ **No valid accounts available to start the process.**", parse_mode=ParseMode.MARKDOWN)
        return

    total_groups_all = count * len(accounts)
    await reply_to_update(
        update,
        context,
        (
            "✅ **Setup Complete!**\n\n"
            f"📱 **Accounts:** {len(accounts)}\n"
            f"📊 **Groups per Account:** {count}\n"
            f"🧮 **Total Groups (All Accounts):** {total_groups_all}\n"
            f"⏱️ **Delay:** {FIXED_DELAY} seconds\n"
            f"💬 **Messages per Group:** {FIXED_MESSAGES_PER_GROUP}\n\n"
            "🕒 **Estimated Duration:** ~24 hours to finish 50 groups per account\n"
            "⏳ **Safety Delay:** Starting in 20 seconds..."
        ),
        parse_mode=ParseMode.MARKDOWN
    )

    await countdown_timer(update, context, 20, "Safety Delay - Process Initialization")
    await reply_to_update(update, context, "🚀 **Starting group creation process now...**", parse_mode=ParseMode.MARKDOWN)

    groups_per_account = count
    progress_queue, start_time = queue.Queue(), time.time()
    ACTIVE_PROCESSES[user_id] = True

    def orchestrator():
        for account in accounts:
            if groups_per_account > 0:
                worker_args = (
                    account, groups_per_account,
                    FIXED_MESSAGES_PER_GROUP, FIXED_DELAY, FIXED_MESSAGES, progress_queue, user_id
                )
                asyncio.run(run_group_creation_process(*worker_args))
        progress_queue.put("ALL_DONE")

    threading.Thread(target=orchestrator, daemon=True).start()
    asyncio.create_task(progress_updater(update, context, progress_queue, start_time, total_groups_all))

async def progress_updater(update: Update, context: ContextTypes.DEFAULT_TYPE, progress_queue: queue.Queue, start_time: float, total_groups: int):
    user_id = update.effective_user.id
    
    # Create keyboard with cancel button
    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Cancel Process", callback_data="cancel_process")]
    ])
    
    status_message = await context.bot.send_message(
        chat_id=user_id, 
        text="🚀 **Starting process...**\n\n🛑 **Use the button below to cancel if needed**", 
        reply_markup=cancel_keyboard,
        parse_mode=ParseMode.MARKDOWN
    )
    created_count = 0
    # Collect links per account to write TXT files later
    per_account_links = defaultdict(list)

    # Per-account counters for this run
    per_account_counts = {}
    # Maintain aggregate totals per account across DONE events for channel summary
    aggregate_totals = {}
    while True:
        try:
            item = progress_queue.get_nowait()
            # End signal after all accounts processed
            if isinstance(item, str) and item == "ALL_DONE":
                # Build final aggregate channel summary
                try:
                    channel_id = get_log_channel_id(user_id)
                    if channel_id:
                        # Build aggregate summary from all persisted stats in user's sessions folder
                        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                        totals_map = {}
                        if os.path.exists(user_session_dir):
                            for fname in os.listdir(user_session_dir):
                                if fname.endswith('_stats.json'):
                                    try:
                                        with open(os.path.join(user_session_dir, fname), 'r', encoding='utf-8') as sf:
                                            data = json.load(sf)
                                            phone = fname.replace('_stats.json', '')
                                            totals_map[phone] = int(data.get('total_groups_created', 0))
                                    except Exception as e:
                                        print(f"Failed reading stats {fname}: {e}")
                        # Fallback to runtime aggregates if no files were found
                        if not totals_map:
                            totals_map = aggregate_totals
                        # Prepare text in requested format
                        lines = [f"{acc} - {total} groups total" for acc, total in totals_map.items()]
                        summary_text = "\n".join(lines) if lines else "No data"
                        old_id = get_aggregate_summary_message_id(user_id)
                        if old_id:
                            try:
                                await context.bot.edit_message_text(chat_id=channel_id, message_id=old_id, text=summary_text)
                            except Exception:
                                sent = await context.bot.send_message(chat_id=channel_id, text=summary_text)
                                set_aggregate_summary_message_id(user_id, sent.message_id)
                        else:
                            sent = await context.bot.send_message(chat_id=channel_id, text=summary_text)
                            set_aggregate_summary_message_id(user_id, sent.message_id)
                except Exception as e:
                    print(f"Failed to post aggregate summary: {e}")
                await schedule_broadcast_reminder(context)
                break
            # Handle final results
            if isinstance(item, str) and item.startswith("DONE"):
                results = json.loads(item.split(':', 1)[1])
                time_taken = time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
                final_report = f" **Process Complete!**\n\n **Time Taken:** {time_taken}\n\n"
                
                # Process each account result
                for res in results:
                    phone_number = res.get('phone_number', 'Unknown')
                    total_groups = res.get('total_groups_created', 0)
                    final_report += f" {res['account_details']}\n **Groups Created This Run:** {res['created_count']}\n **Total Groups (All Time):** {total_groups}\n\n"

                    # Send account stats and cleanup data
                    if phone_number != 'Unknown':
                        stats_result = send_account_stats_and_cleanup(user_id, phone_number)
                        # No cleanup to keep totals; just reflect summary
                        # Update summary in log channel with final totals (edit single message)
                        try:
                            channel_id = get_log_channel_id(user_id)
                            if channel_id:
                                summary_text = (
                                    f"📊 **Account Summary**\n\n"
                                    f"📱 **Account:** `{phone_number}`\n"
                                    f"🔢 **Created This Run:** {res['created_count']}\n"
                                    f"🧮 **Total (All Time):** {res.get('total_groups_created', 0)}"
                                )
                            

                            old_msg_id = get_account_summary_message_id(user_id, phone_number)
                            if old_msg_id:
                                try:
                                    await context.bot.edit_message_text(
                                        chat_id=channel_id,
                                        message_id=old_msg_id,
                                        text=summary_text,
                                        parse_mode=ParseMode.MARKDOWN
                                    )
                                except Exception:
                                    sent = await context.bot.send_message(chat_id=channel_id, text=summary_text, parse_mode=ParseMode.MARKDOWN)
                                    set_account_summary_message_id(user_id, phone_number, sent.message_id)
                            else:
                                sent = await context.bot.send_message(chat_id=channel_id, text=summary_text, parse_mode=ParseMode.MARKDOWN)
                                set_account_summary_message_id(user_id, phone_number, sent.message_id)
                            # Update aggregate map and post aggregated summary listing all accounts
                            aggregate_totals[phone_number] = res.get('total_groups_created', 0)
                            try:
                                lines = [f"{acc} - {total} groups total" for acc, total in aggregate_totals.items()]
                                agg_text = "\n".join(lines)
                                agg_id = get_aggregate_summary_message_id(user_id)
                                if agg_id:
                                    try:
                                        await context.bot.edit_message_text(chat_id=channel_id, message_id=agg_id, text=agg_text)
                                    except Exception:
                                        sent2 = await context.bot.send_message(chat_id=channel_id, text=agg_text)
                                        set_aggregate_summary_message_id(user_id, sent2.message_id)
                                else:
                                    sent2 = await context.bot.send_message(chat_id=channel_id, text=agg_text)
                                    set_aggregate_summary_message_id(user_id, sent2.message_id)
                            except Exception as e:
                                print(f"Failed to update aggregate summary after account: {e}")
                        except Exception as e:
                            print(f"Failed to update log channel summary: {e}")

                await context.bot.edit_message_text(
                    chat_id=user_id, 
                    message_id=status_message.message_id, 
                    text=final_report, 
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Send group files for each account
                # First, build per-account TXT files in requested format
                # Filename: <phone>(<groupscount>)(<YYYYMMDD>).txt
                date_part = time.strftime('%Y%m%d')
                user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
                os.makedirs(user_session_dir, exist_ok=True)
                for res in results:
                    phone_number = res.get('phone_number', 'Unknown')
                    # Build our own links file from accumulated links
                    links = per_account_links.get(phone_number, [])
                    # Use the larger of collected link count and created_count from results
                    created_count_for_name = res.get('created_count', 0)
                    count_value = max(len(links), created_count_for_name)
                    desired_name = f"{phone_number}_{date_part}.txt"
                    output_path = os.path.join(user_session_dir, desired_name)
                    try:
                        with open(output_path, 'w', encoding='utf-8') as outf:
                            for lnk in links:
                                outf.write(f"|-{lnk}\n")
                        # Point res['output_file'] to our generated file for downstream sends
                        res['output_file'] = output_path
                    except Exception as e:
                        print(f"Failed writing links file for {phone_number}: {e}")

                    if res.get('output_file') and os.path.exists(res['output_file']):
                        with open(res['output_file'], 'rb') as file:
                            await context.bot.send_document(
                                chat_id=user_id, 
                                document=file,
                                caption=f" **Group Links File**\n\n **Account:** {phone_number}\n **Groups Created This Run:** {res['created_count']}\n **Total Groups (All Time):** {res.get('total_groups_created', 0)}\n\n **All groups created by this account**\n\n **Note:** Stats have been automatically cleaned up from database.",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        # Also upload links file to log channel if configured
                        try:
                            channel_id = get_log_channel_id(user_id)
                            if channel_id:
                                # Use our desired file name
                                renamed = os.path.basename(res['output_file'])
                                # Clean up previous links file message for this account to keep channel tidy
                                old_links_id = get_links_file_message_id(user_id, phone_number)
                                if old_links_id:
                                    try:
                                        await context.bot.delete_message(chat_id=channel_id, message_id=old_links_id)
                                    except Exception:
                                        pass
                                with open(res['output_file'], 'rb') as chf:
                                    sent_doc = await context.bot.send_document(
                                        chat_id=channel_id,
                                        document=InputFile(chf, filename=renamed),
                                        caption=f"📄 Group Links - {phone_number}",
                                        parse_mode=ParseMode.MARKDOWN
                                    )
                                if sent_doc:
                                    set_links_file_message_id(user_id, phone_number, sent_doc.message_id)
                        except Exception as e:
                            print(f"Failed sending links file to channel: {e}")
                break

            # Handle per-group events
            if isinstance(item, dict) and item.get('event') == 'group_created':
                phone = str(item.get('phone'))
                title = item.get('title')
                link = item.get('link')
                # Increment counters
                per_account_counts[phone] = per_account_counts.get(phone, 0) + 1
                created_count += 1
                if link:
                    per_account_links[phone].append(link)
                # Post link and summary to log channel if configured
                try:
                    channel_id = get_log_channel_id(user_id)
                    if channel_id:
                        # Only update rolling summary (single message edited in place). Do not post direct links.
                        summary_text = (
                            f"📊 **Account Summary**\n\n"
                            f"📱 **Account:** `{phone}`\n"
                            f"🔢 **Groups Created This Run:** {per_account_counts[phone]}"
                        )
                        old_msg_id = get_account_summary_message_id(user_id, phone)
                        if old_msg_id:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=channel_id,
                                    message_id=old_msg_id,
                                    text=summary_text,
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            except Exception:
                                # If edit fails, fallback to new message
                                sent = await context.bot.send_message(chat_id=channel_id, text=summary_text, parse_mode=ParseMode.MARKDOWN)
                                set_account_summary_message_id(user_id, phone, sent.message_id)
                        else:
                            sent = await context.bot.send_message(chat_id=channel_id, text=summary_text, parse_mode=ParseMode.MARKDOWN)
                            set_account_summary_message_id(user_id, phone, sent.message_id)
                except Exception as e:
                    print(f"Failed to post to log channel: {e}")
                # Continue loop without updating status yet
                continue

            # Backward-compatible: integer increments
            created_count += item
            percentage = (created_count / total_groups) * 100 if total_groups > 0 else 0
            progress_bar = "" * int(percentage // 10) + "" * (10 - int(percentage // 10))
            
            # Update progress with cancel button
            await context.bot.edit_message_text(
                chat_id=user_id, 
                message_id=status_message.message_id,
                text=f"⚙️ **Creating Groups...**\n\n📊 **Progress:** {progress_bar} {percentage:.1f}%\n🔢 **Created:** {created_count}/{total_groups}\n\n🛑 **Use the button below to cancel if needed**",
                reply_markup=cancel_keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
        except queue.Empty: 
            await asyncio.sleep(2)
    ACTIVE_PROCESSES[user_id] = False

async def send_partial_results(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Send partial results when process is cancelled"""
    try:
        # Get user session directory to find account files
        user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
        if not os.path.exists(user_session_dir):
            await context.bot.send_message(
                chat_id=user_id,
                text="🛑 **Process Cancelled!**\n\nNo account data found.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Find all links files for this user's accounts
        links_files = []
        for file in os.listdir('.'):
            if file.endswith('_links.txt'):
                # Check if this file belongs to this user's accounts
                phone_number = file.replace('_links.txt', '')
                # Check if this phone number has a session file in user's directory
                session_file = f"{phone_number}.session"
                if os.path.exists(os.path.join(user_session_dir, session_file)):
                    links_files.append((phone_number, file))
        
        if not links_files:
            await context.bot.send_message(
                chat_id=user_id,
                text="🛑 **Process Cancelled!**\n\nNo groups were created before cancellation.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Send links files for each account
        for phone_number, links_file in links_files:
            if os.path.exists(links_file) and os.path.getsize(links_file) > 0:
                try:
                    with open(links_file, 'rb') as file:
                        await context.bot.send_document(
                            chat_id=user_id,
                            document=file,
                            filename=f"{phone_number}.txt",
                            caption=f"📋 **Group Links - Account {phone_number}**\n\n🔗 **Groups Created Before Cancellation**\n\n💡 **These are the groups created by this account before the process was cancelled**"
                        )
                except Exception as e:
                    print(f"Error sending cancelled links file for {phone_number}: {e}")
        
        # Send final cancellation message
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ **Cancellation Complete!**\n\n📋 **All available group links have been sent above.**\n\n🔄 **You can start a new process anytime.**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Start New Process", callback_data="view_accounts")],
                [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Clear cancellation flag
        if user_id in CANCELLATION_REQUESTED:
            del CANCELLATION_REQUESTED[user_id]
        
    except Exception as e:
        print(f"Error in send_partial_results: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="🛑 **Process Cancelled!**\n\n❌ **Error retrieving results.**",
            parse_mode=ParseMode.MARKDOWN
        )

async def countdown_timer(update: Update, context: ContextTypes.DEFAULT_TYPE, seconds: int, message: str):
    """Display a countdown timer for process initialization"""
    chat_id = update.effective_chat.id if update.effective_chat else (update.effective_user.id if update.effective_user else None)
    if not chat_id:
        return
    countdown_message = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ **{message}**\n\n⏱️ **Starting in:** {seconds} seconds",
        parse_mode=ParseMode.MARKDOWN
    )

    for i in range(seconds - 1, 0, -1):
        await asyncio.sleep(1)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=countdown_message.message_id,
                text=f"⏳ **{message}**\n\n⏱️ **Starting in:** {i} seconds",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    
    await asyncio.sleep(1)
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=countdown_message.message_id,
            text=f"🚀 **{message}**\n\n✅ **Ready to start!**",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    return countdown_message

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ **Setup Cancelled**\n\nAll processes have been stopped.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@authorized
async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check and manage user sessions"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    
    if not os.path.exists(user_session_dir):
        await update.message.reply_text(
            f"📭 **No Sessions Found**\n\nPath: `{user_session_dir}`\n\nTip: Use `/getmylogins` to restore from your log channel.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
    if not session_files:
        await update.message.reply_text(
            f"📭 **No Sessions Found**\n\nDirectory: `{user_session_dir}`\n\nTip: Use `/getmylogins` to restore from your log channel.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check backup directory
    backup_dir = os.path.join(user_session_dir, "backups")
    backup_count = 0
    if os.path.exists(backup_dir):
        backup_count = len([f for f in os.listdir(backup_dir) if f.endswith('.session')])
    
    sessions_text = f"🔐 **Your Sessions**\n\n📁 **Total Sessions:** {len(session_files)}\n📦 **Backups:** {backup_count}\n\n"
    
    for i, session_file in enumerate(session_files[:10], 1):
        session_name = session_file.replace('.session', '')
        session_path = os.path.join(user_session_dir, session_name)
        
        # Get file info
        try:
            file_size = os.path.getsize(f"{session_path}.session")
            size_mb = file_size / (1024 * 1024)
            sessions_text += f"📱 `{i}.` {session_name} ({size_mb:.2f} MB)\n"
        except:
            sessions_text += f"📱 `{i}.` {session_name} (Unknown size)\n"
    
    if len(session_files) > 10:
        sessions_text += f"\n... and {len(session_files) - 10} more sessions"
    
    await update.message.reply_text(
        sessions_text,
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def reconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Secret command: upload all .session files to the configured log channel"""
    user_id = update.effective_user.id
    channel_id = get_log_channel_id(user_id)
    if not channel_id:
        await update.message.reply_text(
            "📡 **Log Channel Not Set**\n\nPlease set your log channel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        # Prompt setup
        instructions = (
            "📡 **Step 1: Log Channel Setup**\n\n"
            "Send me your log channel ID.\n\n"
            "**How to get channel ID:**\n"
            "1. Create a private channel\n"
            "2. Add this bot as admin to your channel\n"
            "3. Forward any message from your channel to @userinfobot\n"
            "4. Copy the channel ID (starts with -100)\n"
            "5. Send it here\n\n"
            "**Example:** `-1001234567890`"
        )
        await update.message.reply_text(instructions, parse_mode=ParseMode.MARKDOWN)
        context.user_data['conversation_state'] = GET_LOG_CHANNEL
        return
    # Gather session files
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    if not os.path.exists(user_session_dir):
        await update.message.reply_text("📭 **No Sessions Found**", parse_mode=ParseMode.MARKDOWN)
        return
    session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
    if not session_files:
        await update.message.reply_text("📭 **No Sessions Found**", parse_mode=ParseMode.MARKDOWN)
        return
    # Inform user
    await update.message.reply_text(
        f"📤 **Uploading {len(session_files)} session files to your log channel...**",
        parse_mode=ParseMode.MARKDOWN
    )
    # Send each session file to the channel
    sent_count = 0
    for sess in session_files:
        try:
            full_path = os.path.join(user_session_dir, sess)
            phone_digits = sess.replace('.session', '')
            display_phone = f"+{phone_digits}" if not phone_digits.startswith('+') else phone_digits
            country = guess_country_from_phone(phone_digits)
            name = "Unknown"
            username = "N/A"
            user_id_val = "Unknown"

            # Try to fetch account details from session
            session_path = os.path.join(user_session_dir, phone_digits)
            try:
                client = TelegramClient(session_path, API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    if me:
                        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
                        username = me.username or 'N/A'
                        user_id_val = str(me.id)
                await client.disconnect()
            except Exception as e:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            # Escape markdown
            safe_name = escape_markdown(name)
            safe_phone = escape_markdown(display_phone)
            safe_user_id = escape_markdown(user_id_val)
            safe_country = escape_markdown(country)

            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                with open(full_path, 'rb') as f:
                    sent_doc = await context.bot.send_document(
                        chat_id=channel_id,
                        document=f,
                        filename=sess,
                        caption=(
                            "📁 **Session File**\n\n"
                            f"👤 **User:** {safe_name}\n"
                            f"📞 **Phone:** {safe_phone}\n"
                            f"🆔 **User ID:** `{safe_user_id}`\n"
                            f"🌍 **Country:** {safe_country}"
                        ),
                        parse_mode=ParseMode.MARKDOWN
                    )
                    sent_count += 1
                # Store file_id for restoration
                try:
                    if sent_doc and sent_doc.document:
                        add_uploaded_session_record(user_id, {
                            'file_id': sent_doc.document.file_id,
                            'filename': sess,
                            'phone': phone_digits
                        })
                except Exception as e:
                    print(f"Failed to record reconfig uploaded session: {e}")

                # Also upload a JSON with account info for /reconfig
                try:
                    # If we obtained details above (me, etc.), we already have variables set. If not, try reconnecting.
                    if user_id_val == "Unknown":
                        session_path = os.path.join(user_session_dir, phone_digits)
                        try:
                            client = TelegramClient(session_path, API_ID, API_HASH)
                            await client.connect()
                            if await client.is_user_authorized():
                                me2 = await client.get_me()
                                if me2:
                                    user_id_val = str(me2.id)
                            await client.disconnect()
                        except Exception:
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                    json_basename = f"{phone_digits}.json"
                    json_path = os.path.join(user_session_dir, json_basename)
                    json_payload = {
                        "app_id": API_ID,
                        "app_hash": API_HASH,
                        "twoFA": "",
                        "phone": display_phone,
                        "user_id": int(user_id_val) if str(user_id_val).isdigit() else user_id_val
                    }
                    with open(json_path, 'w', encoding='utf-8') as jf:
                        json.dump(json_payload, jf, indent=2)
                    with open(json_path, 'rb') as jf:
                        await context.bot.send_document(
                            chat_id=channel_id,
                            document=jf,
                            filename=json_basename,
                            caption="📄 Account JSON",
                            parse_mode=ParseMode.MARKDOWN
                        )
                except Exception as e:
                    print(f"Failed to upload /reconfig JSON to channel: {e}")
        except Exception as e:
            print(f"Failed to send session {sess} to channel {channel_id}: {e}")
            continue
    await update.message.reply_text(
        f"✅ **Uploaded {sent_count}/{len(session_files)} session files to log channel.**",
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def getmylogins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore missing .session files from stored file_ids in user settings."""
    user_id = update.effective_user.id
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    os.makedirs(user_session_dir, exist_ok=True)

    records = get_uploaded_sessions(user_id)

    restored = 0
    skipped = 0
    failed = 0

    if records:
        for rec in records:
            filename = rec.get('filename') or f"{rec.get('phone','unknown')}.session"
            target_path = os.path.join(user_session_dir, filename)
            # Skip if already present
            if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                skipped += 1
                continue
            file_id = rec.get('file_id')
            if not file_id:
                failed += 1
                continue
            try:
                # Download via Bot API
                tg_file = await context.bot.get_file(file_id)
                # Save to target path
                await tg_file.download_to_drive(custom_path=target_path)
                restored += 1
            except Exception as e:
                print(f"Failed to restore {filename}: {e}")
                failed += 1
    else:
        # Fallback: try Telethon history scan of the log channel
        channel_id = get_log_channel_id(user_id)
        if not channel_id:
            await update.message.reply_text(
                "📡 **Log Channel Not Set**\n\nSet it up first to restore from channel.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        await update.message.reply_text("🔎 **No stored records. Scanning log channel history for sessions...**", parse_mode=ParseMode.MARKDOWN)
        hist_result = await restore_sessions_via_telethon_history(user_id, channel_id)
        restored += hist_result.get('restored', 0)
        skipped += hist_result.get('skipped', 0)
        failed += hist_result.get('failed', 0)

    await update.message.reply_text(
        f"✅ **Restore complete**\n\n📥 Restored: {restored}\n⏭️ Skipped (already present): {skipped}\n❌ Failed: {failed}",
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def health_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check the health of all user sessions"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    
    if not os.path.exists(user_session_dir):
        await update.message.reply_text(
            "📭 **No Sessions Found**\n\nYou don't have any saved sessions.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
    if not session_files:
        await update.message.reply_text(
            "📭 **No Sessions Found**\n\nNo valid session files in your directory.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Show loading sticker
    loading_msg = await show_loading(update, "🔍 **Checking session health...**")
    
    await update.message.reply_text("🔍 **Checking session health...**", parse_mode=ParseMode.MARKDOWN)
    
    healthy_sessions = []
    unhealthy_sessions = []
    
    for session_file in session_files:
        session_name = session_file.replace('.session', '')
        session_path = ensure_user_session_path(user_id, session_name)
        
        account_info = await validate_session(session_path, session_name, user_id)
        if account_info['valid']:
            healthy_sessions.append({
                'name': session_name,
                'details': account_info
            })
        else:
            unhealthy_sessions.append({
                'name': session_name,
                'reason': account_info.get('reason', 'Unknown error')
            })
    
    health_report = f"🏥 **Session Health Report**\n\n"
    health_report += f"✅ **Healthy Sessions:** {len(healthy_sessions)}\n"
    health_report += f"❌ **Unhealthy Sessions:** {len(unhealthy_sessions)}\n\n"
    
    if healthy_sessions:
        health_report += "✅ **Working Sessions:**\n"
        for session in healthy_sessions[:5]:
            details = session['details']
            # Escape special characters for safe display
            safe_name = escape_markdown(details['name'])
            safe_username = escape_markdown(details['username'])
            health_report += f"   📱 {session['name']} - {safe_name} (@{safe_username})\n"
    
    if unhealthy_sessions:
        health_report += "\n❌ **Problem Sessions:**\n"
        for session in unhealthy_sessions[:5]:
            # Escape special characters for safe display
            safe_reason = escape_markdown(session['reason'])
            health_report += f"   📱 {session['name']} - {safe_reason}\n"
    
    if len(healthy_sessions) > 5:
        health_report += f"\n... and {len(healthy_sessions) - 5} more healthy sessions"
    
    if len(unhealthy_sessions) > 5:
        health_report += f"\n... and {len(unhealthy_sessions) - 5} more problem sessions"
    
    # Hide loading sticker before showing results
    await hide_loading(loading_msg)
    
    await update.message.reply_text(
        health_report,
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help and features information"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    help_text = (
        "ℹ️ **Help & Features**\n\n"
        "🚀 **Start Group Creation**\n"
        "   • Login with phone number\n"
        "   • Automatic OTP handling\n"
        "   • Session file provided\n\n"
        "👥 **View Accounts** (Admins only)\n"
        "   • See all logged admin accounts\n"
        "   • Account details and status\n\n"
        "📊 **Statistics**\n"
        "   • Bot usage statistics\n"
        "   • Admin and account counts\n\n"
        "⚙️ **Admin Management** (Owner only)\n"
        "   • Add/remove admins\n"
        "   • List current admins\n"
        "   • Channel verification setup\n\n"
        "🔐 **Security Features**\n"
        "   • Role-based access control\n"
        "   • Secure session storage\n"
        "   • Admin-only account access\n"
        "   • Restricted to authorized users only\n\n"
        "📢 **Access Control**\n"
        "   • Only owner and admins can use the bot\n"
        "   • Contact bot owner for access\n"
        "   • Secure admin management system\n\n"
        "💡 **Available Commands:**\n"
        "   • /start - Main menu\n"
        "   • /sessions - Account management\n"
        "   • /health - Account health check\n"
        "   • /help - This help message\n"
        "   • /stats - Bot statistics\n"
        "   • /accountstats - Account statistics & links\n"
        "   • /create - Start group creation\n"
        "   • /cancel - Stop current process\n"
        "   • /setup_channel - Setup channel verification (Admin)\n"
        "   • /channel_info - View channel settings (Admin)"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    total_admins = len(ADMIN_IDS)
    total_sessions = 0
    
    for admin_id in ADMIN_IDS:
        admin_session_dir = os.path.join(SESSIONS_DIR, str(admin_id))
        if os.path.exists(admin_session_dir):
            total_sessions += len([f for f in os.listdir(admin_session_dir) if f.endswith('.session')])
    
    stats_text = (
        f"📊 **Bot Statistics**\n\n"
        f"👨‍💼 **Total Admins:** {total_admins}\n"
        f"📱 **Logged Accounts:** {total_sessions}\n"
        f"⚙️ **Messages per Group:** {FIXED_MESSAGES_PER_GROUP}\n"
        f"⏱️ **Fixed Delay:** {FIXED_DELAY} seconds\n"
        f"🔄 **Active Processes:** {len([p for p in ACTIVE_PROCESSES.values() if p])}\n\n"
        f"🤖 **Bot Version:** 2.0 Enhanced\n"
        f"⚡ **Optimized for:** 20-minute group creation"
    )
    
    await update.message.reply_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]]),
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def account_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed account statistics and links"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    
    if not os.path.exists(user_session_dir):
        await update.message.reply_text(
            "📭 **No Accounts Found**\n\nYou don't have any logged-in accounts.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
    if not session_files:
        await update.message.reply_text(
            "📭 **No Valid Sessions Found**\n\nNo valid session files found.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Show loading sticker
    loading_msg = await show_loading(update, "🔍 **Gathering account statistics...**")
    
    await update.message.reply_text("🔍 **Gathering account statistics...**", parse_mode=ParseMode.MARKDOWN)
    
    accounts_stats = []
    total_groups_created = 0
    
    for session_file in session_files:
        session_name = session_file.replace('.session', '')
        
        # Get account statistics
        try:
            account_summary = get_account_summary(session_name)
            accounts_stats.append(account_summary)
            total_groups_created += account_summary["total_groups_created"]
        except Exception as e:
            print(f"Error getting stats for {session_name}: {e}")
            accounts_stats.append({
                "phone_number": session_name,
                "total_groups_created": 0,
                "groups_created_today": 0,
                "total_links_in_file": 0,
                "last_updated": "Unknown",
                "account_info": {}
            })
    
    if not accounts_stats:
        await update.message.reply_text(
            "❌ **No Account Statistics Available**\n\nCould not retrieve statistics for any accounts.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create detailed statistics message
    stats_text = f"📊 **Account Statistics Report**\n\n"
    stats_text += f"📱 **Total Accounts:** {len(accounts_stats)}\n"
    stats_text += f"🏗️ **Total Groups Created:** {total_groups_created}\n\n"
    
    # Show individual account stats
    for i, account in enumerate(accounts_stats[:10], 1):  # Limit to first 10
        phone = account["phone_number"]
        total_groups = account["total_groups_created"]
        today_groups = account["groups_created_today"]
        total_links = account["total_links_in_file"]
        last_updated = account["last_updated"]
        
        # Get account name if available
        account_name = "Unknown"
        if account["account_info"] and account["account_info"].get("name"):
            account_name = account["account_info"]["name"]
        
        stats_text += f"📱 **{i}. {phone}**\n"
        stats_text += f"   👤 **Name:** {escape_markdown(account_name)}\n"
        stats_text += f"   🏗️ **Total Groups:** {total_groups}\n"
        stats_text += f"   📅 **Today:** {today_groups}\n"
        stats_text += f"   🔗 **Links File:** {total_links} links\n"
        stats_text += f"   ⏰ **Last Updated:** {last_updated}\n\n"
    
    if len(accounts_stats) > 10:
        stats_text += f"... and {len(accounts_stats) - 10} more accounts\n\n"
    
    # Create keyboard with options
    keyboard = []
    
    # Add buttons for each account to view their links file
    for i, account in enumerate(accounts_stats[:5]):  # Limit to first 5 for keyboard
        if account["total_links_in_file"] > 0:
            keyboard.append([InlineKeyboardButton(
                f"📁 {account['phone_number']} Links ({account['total_links_in_file']})", 
                callback_data=f"view_links_{account['phone_number']}"
            )])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")])
    
    # Hide loading sticker before showing results
    await hide_loading(loading_msg)
    
    await update.message.reply_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

@authorized
async def create_groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start group creation process directly via command"""
    user_id = update.effective_user.id
    
    # Only owner and admins can access the bot
    if user_id not in OWNER_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text(
            "⛔ **Access Denied!**\n\n"
            "This bot is restricted to authorized users only.\n"
            "Contact the bot owner for access.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if ACTIVE_PROCESSES.get(user_id):
        await update.message.reply_text(
            "⚠️ **Process Already Running!**\n\nYou already have a group creation process active.\n\n🛑 **Use the cancel button below to stop it**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 Cancel Running Process", callback_data="cancel_process")],
                [InlineKeyboardButton("🔙 Back to Main", callback_data="main_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check for existing accounts
    user_session_dir = os.path.join(SESSIONS_DIR, str(user_id))
    if not os.path.exists(user_session_dir):
        await update.message.reply_text(
            "❌ **No Accounts Found**\n\nPlease use /start to login with an account first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session_files = [f for f in os.listdir(user_session_dir) if f.endswith('.session')]
    if not session_files:
        await update.message.reply_text(
            "❌ **No Valid Sessions Found**\n\nPlease use /start to login with an account first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Show account selection
    account_keyboard = [
        
        [InlineKeyboardButton("➕ Add New Account", callback_data="add_new_account")],

    ]
    
    await update.message.reply_text(
        f"🔐 **Account Selection**\n\n"
        f"📱 **Found {len(session_files)} existing accounts**\n\n"
        f"Choose to use existing accounts or add new ones:",
        reply_markup=InlineKeyboardMarkup(account_keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data.clear()
    context.user_data['conversation_state'] = LOGIN_METHOD_CHOICE

@admin_only
async def setup_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setup channel verification settings (admin only)"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "⚙️ **Channel Setup**\n\n"
            "Usage: `/setup_channel @channel_username https://t.me/channel_username`\n\n"
            "Example: `/setup_channel @MyChannel https://t.me/MyChannel`\n\n"
            "💡 **Note:** Only admins can change channel settings",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    global REQUIRED_CHANNEL, CHANNEL_LINK
    
    channel_username = context.args[0]
    channel_link = context.args[1]
    
    # Validate channel username format
    if not channel_username.startswith('@'):
        await update.message.reply_text(
            "❌ **Invalid Channel Username!**\n\n"
            "Channel username must start with @\n"
            "Example: @MyChannel",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Validate channel link format
    if not channel_link.startswith('https://t.me/'):
        await update.message.reply_text(
            "❌ **Invalid Channel Link!**\n\n"
            "Channel link must be in format: https://t.me/channel_username\n"
            "Example: https://t.me/MyChannel",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Update global variables
    REQUIRED_CHANNEL = channel_username
    CHANNEL_LINK = channel_link
    
    await update.message.reply_text(
        f"✅ **Channel Setup Complete!**\n\n"
        f"📢 **Channel:** {channel_username}\n"
        f"🔗 **Link:** {channel_link}\n\n"
        f"🔄 **Bot will now require users to join this channel**",
        parse_mode=ParseMode.MARKDOWN
    )

@admin_only
async def channel_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current channel verification settings (admin only)"""
    await update.message.reply_text(
        f"📢 **Current Channel Settings**\n\n"
        f"🔗 **Required Channel:** {REQUIRED_CHANNEL}\n"
        f"🌐 **Channel Link:** {CHANNEL_LINK}\n\n"
        f"💡 **To change settings, use:**\n"
        f"`/setup_channel @new_channel https://t.me/new_channel`",
        parse_mode=ParseMode.MARKDOWN
    )

def main():
    application = Application.builder().token(config['BOT_TOKEN']).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("sessions", sessions_command))
    application.add_handler(CommandHandler("health", health_check_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("accountstats", account_stats_command))
    application.add_handler(CommandHandler("create", create_groups_command))
    application.add_handler(CommandHandler("reconfig", reconfig_command))
    application.add_handler(CommandHandler("getmylogins", getmylogins_command))
    application.add_handler(CommandHandler("changepass", changepass_command))
    application.add_handler(CommandHandler("setup_channel", setup_channel_command))
    application.add_handler(CommandHandler("channel_info", channel_info_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_conversation_input))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_conversation_input))
    application.add_handler(CommandHandler("cancel", cancel))

    application.run_polling()

if __name__ == '__main__':
    main()
