# Save this file as BigBotFinal.py
import asyncio
import os
import json
import time
import random
from datetime import datetime
from typing import Optional, Tuple
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChatAdminRequiredError
from telethon.tl.functions.channels import CreateChannelRequest, InviteToChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import ChannelParticipantsAdmins


# Import usage tracking functions from telegram_bot.py
try:
    from telegram_bot import record_group_creation
except ImportError:
    # Fallback if import fails
    def record_group_creation(user_id, phone_number, count=1):
        print(f"Warning: Usage tracking not available - would record {count} groups for {phone_number}")

# Global variables for process tracking
ACTIVE_PROCESSES = {}
CANCELLATION_REQUESTED = {}


# API_ID = 22566208

# API_HASH = "fa18dcf886c0d78f20e849f54be62940"
# --- Configuration ---
CONFIG_FILE = 'bot_config.json'
SESSIONS_DIR = 'sessions'
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



# ==========configuration===========
config = load_config()
API_ID = config['API_ID']
API_HASH = config['API_HASH']

# Fixed process settings
FIXED_GROUP_TARGET = 50
RANDOM_DELAY_RANGE = (5, 25)  # default jitter window (seconds)
TOTAL_RUN_SECONDS = 24 * 3600
PER_GROUP_TARGET_SECONDS = TOTAL_RUN_SECONDS // FIXED_GROUP_TARGET

AFTER_LOGIN_DELAY_BASE = 120
AFTER_LOGIN_JITTER = (60, 180)
POST_CREATION_DELAY_BASE = 45
POST_CREATION_JITTER = (15, 45)
BETWEEN_MESSAGE_DELAY_BASE = 5
BETWEEN_MESSAGE_JITTER = (2, 5)
BETWEEN_GROUP_DELAY_BASE = max(120, PER_GROUP_TARGET_SECONDS - 300)
BETWEEN_GROUP_JITTER = (120, 360)
SAFETY_DELAY_BASE = 180
SAFETY_DELAY_JITTER = (60, 180)
BOT_INVITE_DELAY_BASE = 15
BOT_INVITE_JITTER = (5, 15)
LEAVE_DELAY_SECONDS = 20
LEAVE_DELAY_JITTER = (5, 20)
FINAL_DELAY_BASE = 60
FINAL_DELAY_JITTER = (30, 60)



def get_user_folder_path(user_id, phone_number):
    """Get the user-specific folder path for storing account data"""
    sessions_dir = 'sessions'
    user_folder = os.path.join(sessions_dir, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_account_stats_file(user_id, phone_number):
    """Get the stats file path for an account in user's folder"""
    user_folder = get_user_folder_path(user_id, phone_number)
    return os.path.join(user_folder, f"{phone_number.replace('+', '')}_stats.json")

def get_account_groups_file(user_id, phone_number, groups_count=None):
    """Get the groups file path with format: accountnumber_date.txt"""
    user_folder = get_user_folder_path(user_id, phone_number)
    date_str = datetime.now().strftime("%Y%m%d")
    clean_phone = phone_number.replace('+', '')
    filename = f"{clean_phone}_{date_str}.txt"
    return os.path.join(user_folder, filename)

def load_account_stats(user_id, phone_number):
    """Load existing account statistics from user's folder"""
    stats_file = get_account_stats_file(user_id, phone_number)
    if os.path.exists(stats_file):
        try:
            with open(stats_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "total_groups_created": 0,
        "groups_created_today": 0,
        "all_group_links": [],
        "last_updated": "",
        "account_info": {}
    }

def save_account_stats(user_id, phone_number, stats):
    """Save account statistics to user's folder"""
    stats_file = get_account_stats_file(user_id, phone_number)
    stats["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save stats for {phone_number}: {e}")

def save_group_link(user_id, phone_number, group_title, invite_link, groups_file_path):
    """Save a group link to the account's groups file in user's folder"""
    try:
        with open(groups_file_path, 'a', encoding='utf-8') as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {group_title}: {invite_link}\n")
        print(f"Link saved to {groups_file_path}")
    except Exception as e:
        print(f"Failed to save link to {groups_file_path}: {e}")

def cleanup_account_data(user_id, phone_number):
    """Delete account stats and related files after sending data"""
    try:
        # Delete stats file
        stats_file = get_account_stats_file(user_id, phone_number)
        if os.path.exists(stats_file):
            os.remove(stats_file)
            print(f"Deleted stats file: {stats_file}")
        
        # Find and delete groups files for this account
        user_folder = get_user_folder_path(user_id, phone_number)
        clean_phone = phone_number.replace('+', '')
        
        for filename in os.listdir(user_folder):
            if filename.startswith(f"{clean_phone}_") and filename.endswith('.txt'):
                file_path = os.path.join(user_folder, filename)
                os.remove(file_path)
                print(f"Deleted groups file: {file_path}")
                
    except Exception as e:
        print(f"Error cleaning up account data for {phone_number}: {e}")

async def safe_sleep(seconds: int, reason: str = ""):
    """Safe sleep with logging"""
    if seconds > 0:
        print(f"Sleeping {seconds}s - {reason}")
        await asyncio.sleep(seconds)

async def random_delay_sleep(base_seconds: int = 0, reason: str = "", jitter_range: Optional[Tuple[int, int]] = None):
    """Apply base delay plus random jitter from configuration."""
    extra_range = jitter_range or RANDOM_DELAY_RANGE
    low = max(0, extra_range[0])
    high = max(low, extra_range[1])
    extra = random.randint(low, high) if high > 0 else 0
    await safe_sleep(max(1, base_seconds + extra), reason)

async def account_worker(account_info, groups_to_create, messages_to_send, delay, progress_queue, user_id=None):
    session_path = account_info['session_path']
    phone_number = account_info.get('phone', 'session').replace('+', '')
    account_details = "Could not log in."
    total_created_this_run = 0
    
    # Enforce fixed group target
    if groups_to_create != FIXED_GROUP_TARGET:
        print(f"Requested {groups_to_create} groups, overriding to fixed target {FIXED_GROUP_TARGET}")
        groups_to_create = FIXED_GROUP_TARGET

    # Load existing account statistics
    account_stats = load_account_stats(user_id, phone_number)
    
    # Get the groups file path with current count and date
    groups_file = get_account_groups_file(user_id, phone_number, groups_to_create)
    
    # Create groups file if it doesn't exist
    if not os.path.exists(groups_file):
        with open(groups_file, 'w', encoding='utf-8') as f:
            f.write(f"Group Links for Account: {phone_number}\n")
            f.write(f"Created on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Target Groups: {groups_to_create}\n")
            f.write("=" * 50 + "\n\n")

    try:
        # Connect to client
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        
        # Check if authorized
        if not await client.is_user_authorized():
            print(f"Account {phone_number} not authorized")
            await client.disconnect()
            return {
                "created_count": 0,
                "account_details": "Session expired or invalid",
                "output_file": None,
                "total_groups_created": account_stats["total_groups_created"]
            }
        
        # Get account details (but don't log them to avoid detection)
        try:
            me = await client.get_me()
            account_details = (
                f"👤 **Name:** {me.first_name} {me.last_name or ''}\n"
                f"🔖 **Username:** @{me.username or 'N/A'}\n"
                f"🆔 **ID:** `{me.id}`"
            )
            
            # Update account info in stats
            account_stats["account_info"] = {
                "name": f"{me.first_name} {me.last_name or ''}".strip(),
                "username": me.username or 'N/A',
                "id": me.id
            }
            
            print(f"Account loaded: {me.first_name} (@{me.username})")
        except Exception as e:
            print(f"Could not get account details: {e}")
            account_details = "Account details unavailable"
        
        # Initial delay after login to avoid immediate automation detection
        print("Waiting after login to avoid account freezing...")
        await random_delay_sleep(AFTER_LOGIN_DELAY_BASE, "Safety delay after login", jitter_range=AFTER_LOGIN_JITTER)
        
        for i in range(groups_to_create):
            # Check for cancellation only if user clicked cancel button
            if user_id and CANCELLATION_REQUESTED.get(user_id, False):
                print(f"User requested cancellation for {phone_number}, stopping at group {i+1}")
                break
                
            try:
                # Random group title to avoid pattern detection
                adjectives = ['Golden', 'Silent', 'Hidden', 'Secret', 'Private', 'Elite', 'Premium', 'Exclusive']
                nouns = ['Oasis', 'Sanctuary', 'Valley', 'Garden', 'Haven', 'Retreat', 'Club', 'Society']
                group_title = f"{random.choice(adjectives)} {random.choice(nouns)} {random.randint(100, 999)}"
                
                print(f"Creating group {i+1}/{groups_to_create}: {group_title}")
                
                # Create group with random delay
                result = await client(CreateChannelRequest(
                    title=group_title, 
                    about="Welcome to our community!", 
                    megagroup=True
                ))
                new_group = result.chats[0]
                
                # Delay after group creation
                await random_delay_sleep(POST_CREATION_DELAY_BASE, f"Delay after creating group {group_title}", jitter_range=POST_CREATION_JITTER)
                
                # Get invite link
                try:
                    invite_result = await client(ExportChatInviteRequest(new_group.id))
                    invite_link = invite_result.link
                    
                    # Save link to file
                    save_group_link(user_id, phone_number, group_title, invite_link, groups_file)
                    
                    # Update account statistics
                    account_stats["all_group_links"].append({
                        "title": group_title,
                        "link": invite_link,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    })
                    account_stats["total_groups_created"] += 1
                    account_stats["groups_created_today"] += 1
                    
                    # Record group creation for 24-hour limit tracking
                    try:
                        record_group_creation(user_id, phone_number, 1)
                    except Exception as e:
                        print(f"Failed to record group creation for usage tracking: {e}")
                    
                    print(f"Invite link generated for {group_title}")

                    # Invite keep-alive bot to the group
                    try:
                        keep_bot = await client.get_entity('NexoKeepAliveRobot')
                        await client(InviteToChannelRequest(channel=new_group, users=[keep_bot]))
                        print("Invited @NexoKeepAliveRobot to the group")
                        # Small delay to be gentle
                        await random_delay_sleep(BOT_INVITE_DELAY_BASE, "Delay after inviting keep-alive bot", jitter_range=BOT_INVITE_JITTER)
                    except Exception as e:
                        print(f"Failed to invite @NexoKeepAliveRobot: {e}")
                    
                    # Emit per-group event for logging in bot
                    try:
                        if progress_queue is not None:
                            progress_queue.put({
                                'event': 'group_created',
                                'phone': phone_number,
                                'title': group_title,
                                'link': invite_link
                            })
                    except Exception as e:
                        print(f"Failed to emit group_created event: {e}")
                except ChatAdminRequiredError:
                    print(f"Could not export invite for {group_title} - admin rights issue")
                    continue
                
                # Send messages with proper delays
                for j, msg in enumerate(messages_to_send):
                    try:
                        await client.send_message(new_group.id, msg)
                        print(f"Sent message {j+1}/{len(messages_to_send)} to {group_title}")
                        
                        # Random delay between messages (2-5 seconds)
                        if j < len(messages_to_send) - 1:
                            await random_delay_sleep(BETWEEN_MESSAGE_DELAY_BASE, f"Delay between messages in {group_title}", jitter_range=BETWEEN_MESSAGE_JITTER)
                    except Exception as e:
                        print(f"Failed to send message {j+1} to {group_title}: {e}")
                        continue
                
                # Note: Commands message removed - should not be sent to groups automatically
                # Users can access bot commands directly in the bot chat
                
                # Leave the group after sending messages
                await random_delay_sleep(LEAVE_DELAY_SECONDS, f"Leave delay before leaving {group_title}", jitter_range=LEAVE_DELAY_JITTER)
                try:
                    await client(LeaveChannelRequest(new_group))
                    print(f"Left group {group_title}")
                except Exception as e:
                    print(f"Failed to leave group {group_title}: {e}")
                
                total_created_this_run += 1
                progress_queue.put(1)
                
                # Delay between groups
                if i < groups_to_create - 1:
                    await random_delay_sleep(BETWEEN_GROUP_DELAY_BASE, "Delay before next group", jitter_range=BETWEEN_GROUP_JITTER)
                
                # Safety delay every 5 groups
                if (i + 1) % 5 == 0:
                    await random_delay_sleep(SAFETY_DELAY_BASE, f"Safety delay after {i+1} groups", jitter_range=SAFETY_DELAY_JITTER)
                
            except FloodWaitError as fwe:
                print(f"FloodWait for {group_title}: sleeping {fwe.seconds}s")
                await safe_sleep(fwe.seconds + 10, "FloodWait recovery")
                continue
            except Exception as e:
                print(f"Error creating group {group_title}: {e}")
                continue
        
        # Save updated account statistics
        save_account_stats(user_id, phone_number, account_stats)
        
        # Record total groups created for usage tracking (in case individual records failed)
        try:
            if total_created_this_run > 0:
                # This will ensure the usage tracking is updated even if individual records failed
                record_group_creation(user_id, phone_number, 0)  # Just trigger cleanup/validation
        except Exception as e:
            print(f"Failed to validate usage tracking: {e}")
        
        # Final delay before disconnecting
        await random_delay_sleep(FINAL_DELAY_BASE, "Final delay before disconnecting", jitter_range=FINAL_DELAY_JITTER)
        
    except Exception as e:
        print(f"FATAL ERROR for {phone_number}: {e}")
    finally:
        try:
            await client.disconnect()
        except:
            pass
        
        return {
            "created_count": total_created_this_run,
            "account_details": account_details,
            "output_file": groups_file if total_created_this_run > 0 else None,
            "total_groups_created": account_stats["total_groups_created"],
            "phone_number": phone_number
        }

async def run_group_creation_process(account_config, total_groups, msgs_per_group, delay, messages, progress_queue, user_id=None):
    results = await asyncio.gather(account_worker(account_config, total_groups, messages[:msgs_per_group], delay, progress_queue, user_id))
    progress_queue.put(f"DONE:{json.dumps(results)}")

def get_account_summary(user_id, phone_number):
    """Get a summary of account statistics from user's folder"""
    stats = load_account_stats(user_id, phone_number)
    
    # Count total links in all groups files for this account
    total_links = 0
    user_folder = get_user_folder_path(user_id, phone_number)
    clean_phone = phone_number.replace('+', '')
    
    try:
        for filename in os.listdir(user_folder):
            if filename.startswith(f"{clean_phone}_") and filename.endswith('.txt'):
                file_path = os.path.join(user_folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    # Count lines that contain links (skip header lines)
                    content = f.read()
                    total_links += content.count('https://t.me/')
    except Exception as e:
        print(f"Error counting links for {phone_number}: {e}")
    
    return {
        "phone_number": phone_number,
        "total_groups_created": stats["total_groups_created"],
        "groups_created_today": stats["groups_created_today"],
        "total_links_in_file": total_links,
        "last_updated": stats["last_updated"],
        "account_info": stats["account_info"]
    }

def send_account_stats_and_cleanup(user_id, phone_number):
    """Return account statistics and groups files paths without deleting persistent stats."""
    try:
        # Get account summary
        summary = get_account_summary(user_id, phone_number)
        
        # Find all groups files for this account
        user_folder = get_user_folder_path(user_id, phone_number)
        clean_phone = phone_number.replace('+', '')
        groups_files = []
        
        for filename in os.listdir(user_folder):
            if filename.startswith(f"{clean_phone}_") and filename.endswith('.txt'):
                groups_files.append(os.path.join(user_folder, filename))
        
        # Do NOT cleanup data here to preserve totals across runs
        return {
            "summary": summary,
            "groups_files": groups_files,
            "cleaned_up": False
        }
        
    except Exception as e:
        print(f"Error in send_account_stats_and_cleanup for {phone_number}: {e}")
        return {
            "summary": None,
            "groups_files": [],
            "cleaned_up": False,
            "error": str(e)
        }