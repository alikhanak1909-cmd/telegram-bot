import os
import re
import json
import logging
import asyncio  
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    ChatMemberHandler,
    filters,
)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

FOOTER = "\n\n— Owner: @Alikhan0636"
OWNER_ID = 7381655543      

DB_FILE = "bot_database.json"

DEFAULT_RULES = (
    "1. No spam or flooding in the group.\n"
    "2. Sharing links or promotion without permission is strictly banned.\n"
    "3. Respect all members and admins.\n"
    "4. Do not share scam, crypto-scam, or fake reward content."
)

DEFAULT_WELCOME = (
    "✦ ◜ **NEW MEMBER ALERT** ◝ ✦\n\n"
    "Welcome to the family, **{display}**! ✨\n"
    "We are absolutely thrilled to have you here with us.\n\n"
    "Have a great time chatting with our community! 💕\n"
    "───────────────────\n"
    "⏱️ *Cleaning chat in 30 seconds...*"
)

DEFAULT_GOODBYE = (
    "✦ ◜ **GOODBYE FRIEND** ◝ ✦\n\n"
    "**{display}** just left the group chat. 🍃\n\n"
    "Take care and goodbye until next time!\n"
    "───────────────────\n"
    "⏱️ *Cleaning chat in 30 seconds...*"
)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                if "approved_groups" not in data: data["approved_groups"] = {}
                if "group_settings" not in data: data["group_settings"] = {}
                return data
        except Exception as e:
            pass
    return {"approved_groups": {}, "group_settings": {}}

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        pass

db = load_db()

warnings = {}                    
group_locked = set()        
flood_tracker = defaultdict(list)   
rate_limit_cooldowns = {}  

SCAM_KEYWORDS = ["free-crypto", "giftcard-scam", "earn-money-fast", "free-rewards", "claim-drop"]
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True,
)

NO_PERMISSIONS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False,
)

def ft(text: str) -> str:
    return text + FOOTER

def init_group_settings(chat_id: str):
    if chat_id not in db["group_settings"]:
        db["group_settings"][chat_id] = {
            "rules": DEFAULT_RULES,
            "welcome": DEFAULT_WELCOME,
            "goodbye": DEFAULT_GOODBYE,
            "flood_limit": 5,
            "cooldown": 3
        }
        save_db(db)

def contains_spam(text: str) -> bool:
    if URL_PATTERN.search(text):
        return True
    lower = text.lower()
    return any(kw in lower for kw in SCAM_KEYWORDS)

def is_flooding(chat_id: int, user_id: int, limit: int) -> bool:
    import time
    now = time.monotonic()
    key = (chat_id, user_id)
    times = flood_tracker[key]
    times.append(now)
    flood_tracker[key] = [t for t in times if now - t <= 5.0]
    return len(flood_tracker[key]) > limit

def parse_duration(arg: str) -> int | None:
    arg = arg.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(arg) >= 2 and arg[-1] in units:
        try: return int(arg[:-1]) * units[arg[-1]]
        except ValueError: return None
    try: return int(arg)
    except ValueError: return None

async def is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.message
    if not message: return False
    sender = message.from_user
    if sender and sender.id == OWNER_ID: return True
    if sender and (sender.id == 1087968824 or sender.username == "GroupAnonymousBot"): return True
    
    chat = update.effective_chat
    if chat and chat.type != "private" and sender:
        try:
            member = await context.bot.get_chat_member(chat.id, sender.id)
            return member.status in ("administrator", "creator")
        except Exception: pass
    return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return None, None
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u, (f"@{u.username}" if u.username else u.first_name)
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                u = entity.user
                return u, (f"@{u.username}" if u.username else u.first_name)
    if context.args:
        raw = context.args[0].lstrip("@")
        try:
            uid = int(raw)
            cm = await context.bot.get_chat_member(message.chat_id, uid)
            u = cm.user
            return u, (f"@{u.username}" if u.username else u.first_name)
        except (ValueError, Exception): pass
        
        if context.args[0].startswith("@"):
            uname = context.args[0].replace("@", "").strip()
            if message.entities:
                for entity in message.entities:
                    if entity.type == "mention":
                        offset = entity.offset
                        length = entity.length
                        extracted = message.text[offset:offset+length].replace("@", "").strip()
    return None, None

async def apply_mute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, seconds: int) -> None:
    until = datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)
    await context.bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=NO_PERMISSIONS, until_date=until)

def fmt_duration(seconds: int) -> str:
    if seconds < 60: return f"{seconds}s"
    if seconds < 3600: return f"{seconds // 60}m"
    if seconds < 86400: return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message: return
    user = update.message.from_user
    bot_username = context.bot.username or "GroupManagerBot"
    
    if update.message.chat.type == "private":
        if user.id != OWNER_ID:
            url = f"https://t.me/{bot_username}?startgroup=true"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Add me to group", url=url)]])
            await update.message.reply_text(
                ft("Hello! I am a professional Group Management Bot. Please add me to your group to activate standard protection and moderation tools."),
                reply_markup=keyboard
            )
            return
        await update.message.reply_text(ft("🤖 Welcome Owner! I am active in private chat.\n\nPlease use /help to see all configuration commands."))
    else:
        chat_id_str = str(update.message.chat_id)
        init_group_settings(chat_id_str)
        if chat_id_str not in db["approved_groups"]:
            db["approved_groups"][chat_id_str] = {
                "group_name": update.message.chat.title or "Group Chat",
                "group_id": update.message.chat_id,
                "join_date": datetime.now(tz=timezone.utc).isoformat()
            }
            save_db(db)
        await update.message.reply_text(ft("🤖 Bot is active and protecting this group!"))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message: return
    help_text = (
        "🛡️ **Bot Control Panel Commands:**\n\n"
        "👑 **Owner Setup:**\n"
        "/groups - List all active groups\n\n"
        "⚙️ **Live Group Management:**\n"
        "/setwelcome <text> - Set custom welcome message\n"
        "/setgoodbye <text> - Set custom goodbye message\n"
        "/setrules <text> - Set group rules\n"
        "/rules - View group rules\n"
        "/setflood <number> - Change anti-flood message limit\n"
        "/status - Check current group configurations\n\n"
        "⚔️ **Moderation Tools (Mute Only):**\n"
        "/warn - Warn a user (Reply or @username)\n"
        "/unwarn - Remove user warning (Reply or @username)\n"
        "/mute <time> - Mute user (e.g., 10m, 2h)\n"
        "/unmute - Unmute user\n"
        "/purge <count> - Bulk delete messages\n\n"
        "🔒 **Privacy Locks:**\n"
        "/lock /unlock - Close or open group chat\n"
        "/slowmode <sec> - Enable slow mode\n"
        "/addkeyword <word> - Add blacklisted word\n"
        "/keywords - View blocked keywords list"
    )
    await update.message.reply_text(ft(help_text))

async def cmd_setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    if not context.args:
        await update.message.reply_text("❌ Use: `/setwelcome Welcome to our group {display}!`", parse_mode="Markdown")
        return
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    db["group_settings"][chat_id_str]["welcome"] = " ".join(context.args)
    save_db(db)
    await update.message.reply_text(ft("✅ Welcome message updated successfully for this group!"))

async def cmd_setgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    if not context.args:
        await update.message.reply_text("❌ Use: `/setgoodbye Bye bye {display}!`", parse_mode="Markdown")
        return
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    db["group_settings"][chat_id_str]["goodbye"] = " ".join(context.args)
    save_db(db)
    await update.message.reply_text(ft("✅ Goodbye message updated successfully for this group!"))

async def cmd_setflood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Use: `/setflood 5` (Enter number of messages)", parse_mode="Markdown")
        return
    limit = int(context.args[0])
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    db["group_settings"][chat_id_str]["flood_limit"] = limit
    save_db(db)
    await update.message.reply_text(ft(f"✅ Anti-flood limit set to **{limit}** messages."))

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    g_set = db["group_settings"][chat_id_str]
    
    status_text = (
        f"📊 **Group Settings Status:**\n\n"
        f"🚫 **Anti-Flood Limit:** {g_set.get('flood_limit', 5)} messages\n"
        f"⏳ **Anti-Flood Mute Delay:** {g_set.get('cooldown', 3)}s\n"
        f"🔒 **Locked Status:** {'Locked' if update.message.chat_id in group_locked else 'Open'}\n\n"
        f"👋 **Welcome Message Template:**\n`{g_set.get('welcome')[:60]}...`\n\n"
        f"🏃‍♂️ **Goodbye Message Template:**\n`{g_set.get('goodbye')[:60]}...`"
    )
    await update.message.reply_text(ft(status_text), parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or message.chat.type == "private": return
    chat_id = message.chat_id
    chat_id_str = str(chat_id)
    init_group_settings(chat_id_str)
    
    g_set = db["group_settings"][chat_id_str]

    if not message.text: return
    user = message.from_user
    if not user or user.id == OWNER_ID: return

    try:
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status in ("administrator", "creator"): return
    except Exception: pass

    display = f"@{user.username}" if user.username else user.first_name
    key = (chat_id, user.id)
    import time

    if key in rate_limit_cooldowns:
        remaining = int(rate_limit_cooldowns[key] - time.time())
        if remaining > 0:
            try: await message.delete()
            except Exception: pass
            alert = await context.bot.send_message(
                chat_id=chat_id,
                text=ft(f"⏳ {display}, you are sending messages too fast!\nPlease wait {remaining} seconds before trying again.")
            )
            await asyncio.sleep(2)
            try: await alert.delete()
            except Exception: pass
            return
        else:
            rate_limit_cooldowns.pop(key, None)

    if chat_id in group_locked:
        try: await message.delete()
        except Exception: pass
        return

    flood_limit = g_set.get("flood_limit", 5)
    if is_flooding(chat_id, user.id, flood_limit):
        try: await message.delete()
        except Exception: pass
        
        cooldown_duration = g_set.get("cooldown", 3)
        rate_limit_cooldowns[key] = time.time() + cooldown_duration
        flood_tracker.pop(key, None)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=ft(f"🌊 FLOOD SYSTEM ACTIVATED — {display}\nYou have triggered our automated anti-flood system. A strict {cooldown_duration} seconds message delay timer has been applied.")
        )
        return

    if not contains_spam(message.text): return
    try: await message.delete()
    except Exception: pass

    current = warnings.get(user.id, 0) + 1
    warnings[user.id] = current

    if current == 1:
        await apply_mute(context, chat_id, user.id, 5 * 60)
        await context.bot.send_message(chat_id=chat_id, text=ft(f"⚠️ WARNING [1/3] — {display}\nMuted for 5 minutes."))
    elif current == 2:
        await apply_mute(context, chat_id, user.id, 60 * 60)
        await context.bot.send_message(chat_id=chat_id, text=ft(f"⚠️ WARNING [2/3] — {display}\nMuted for 1 hour."))
    else:
        await apply_mute(context, chat_id, user.id, 24 * 60 * 60)
        warnings[user.id] = 0
        await context.bot.send_message(chat_id=chat_id, text=ft(f"🚫 FINAL WARNING [3/3] — {display}\nMuted for 24 hours."))

async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 30):
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception: pass

async def handle_chat_member_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chat_member
    if not result: return
    
    chat_id = result.chat.id
    chat_id_str = str(chat_id)
    chat_name = result.chat.title or "Unknown Group"
    
    init_group_settings(chat_id_str)
    g_set = db["group_settings"][chat_id_str]
    
    if result.new_chat_member.user.id == context.bot.id:
        if chat_id_str not in db["approved_groups"]:
            db["approved_groups"][chat_id_str] = {
                "group_name": chat_name,
                "group_id": chat_id,
                "join_date": datetime.now(tz=timezone.utc).isoformat()
            }
            save_db(db)
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user
    display = f"@{user.username}" if user.username else user.first_name

    # Check for JOIN event
    if old_status in ("left", "kicked") and new_status in ("member", "administrator", "creator"):
        welcome_tmpl = g_set.get("welcome", DEFAULT_WELCOME)
        welcome_text = welcome_tmpl.replace("{display}", display)
        try:
            msg = await context.bot.send_message(chat_id=chat_id, text=welcome_text, parse_mode="Markdown")
            asyncio.create_task(delete_after_delay(context, chat_id, msg.message_id, 30))
        except Exception: pass
            
    # Check for LEAVE event
    elif old_status in ("member", "administrator", "creator") and new_status in ("left", "kicked"):
        goodbye_tmpl = g_set.get("goodbye", DEFAULT_GOODBYE)
        goodbye_text = goodbye_tmpl.replace("{display}", display)
        try:
            msg = await context.bot.send_message(chat_id=chat_id, text=goodbye_text, parse_mode="Markdown")
            asyncio.create_task(delete_after_delay(context, chat_id, msg.message_id, 30))
        except Exception: pass

async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or update.message.from_user.id != OWNER_ID: return
    if not db["approved_groups"]:
        await update.message.reply_text("📊 No active groups.")
        return
    lines = ["📊 Active Groups:"]
    for gid, info in db["approved_groups"].items(): lines.append(f"• {info.get('group_name', 'Group')} ({gid})")
    await update.message.reply_text("\n".join(lines))

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    user, name = await resolve_target(update, context)
    
    if not user:
        await update.message.reply_text("❌ Target user not found. User must reply or be mentioned correctly.")
        return

    current = warnings.get(user.id, 0) + 1
    warnings[user.id] = current
    
    await apply_mute(context, update.message.chat_id, user.id, 5 * 60 if current == 1 else 3600 if current == 2 else 86400)
    await update.message.reply_text(ft(f"⚠️ Warning [{current}/3] given to {name}."))

async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    user, name = await resolve_target(update, context)
    
    if not user:
        await update.message.reply_text("❌ Target user not found. Use reply or mention @username.")
        return

    current = warnings.get(user.id, 0)
    if current > 0:
        warnings[user.id] = current - 1
        new_count = current - 1
    else:
        new_count = 0
        
    await update.message.reply_text(ft(f"✅ Warning removed from {name}. Current warnings: [{new_count}/3]"))

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    user, name = await resolve_target(update, context)
    if not user or not context.args: return
    seconds = parse_duration(context.args[0] if update.message.reply_to_message else context.args[1])
    if not seconds: return
    await apply_mute(context, update.message.chat_id, user.id, seconds)
    await update.message.reply_text(ft(f"🔇 {name} muted for {fmt_duration(seconds)}."))

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    user, name = await resolve_target(update, context)
    if not user: return
    await context.bot.restrict_chat_member(chat_id=update.message.chat_id, user_id=user.id, permissions=FULL_PERMISSIONS)
    await update.message.reply_text(ft(f"🔊 {name} unmuted."))

async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    group_locked.add(update.message.chat_id)
    await update.message.reply_text(ft("🔒 Group chat locked."))

async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    group_locked.discard(update.message.chat_id)
    await update.message.reply_text(ft("🔓 Group chat unlocked."))

async def cmd_slowmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context) or not context.args: return
    try:
        seconds = int(context.args[0])
        await context.bot.set_chat_slow_mode_delay(chat_id=update.message.chat_id, slow_mode_delay=seconds)
        await update.message.reply_text(ft(f"🐢 Slow mode set to {seconds}s."))
    except Exception: pass

async def cmd_setrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context) or not context.args: return
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    db["group_settings"][chat_id_str]["rules"] = " ".join(context.args)
    save_db(db)
    await update.message.reply_text(ft("✅ Rules updated!"))

async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message: return
    chat_id_str = str(update.message.chat_id)
    init_group_settings(chat_id_str)
    rules_text = db["group_settings"][chat_id_str].get("rules", DEFAULT_RULES)
    await update.message.reply_text(ft(f"📋 Group Rules:\n\n{rules_text}"))

async def cmd_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    await update.message.reply_text(ft("📋 Keywords:\n" + "\n".join(SCAM_KEYWORDS)))

async def cmd_addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context) or not context.args: return
    kw = context.args[0].lower().strip()
    if kw not in SCAM_KEYWORDS: SCAM_KEYWORDS.append(kw)
    await update.message.reply_text(ft(f"✅ Added keyword: '{kw}'"))

async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not await is_authorized(update, context): return
    chat_id = update.message.chat_id
    try: await update.message.delete()
    except Exception: pass

    if update.message.reply_to_message:
        target = update.message.reply_to_message.message_id
        for msg_id in range(target, update.message.message_id):
            try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception: pass
        return

    if context.args:
        try: limit = int(context.args[0])
        except ValueError: return
        curr = update.message.message_id
        for i in range(1, limit + 1):
            try: await context.bot.delete_message(chat_id=chat_id, message_id=curr - i)
            except Exception: pass

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "8885327969:AAF7TPote1Ewwyb5J9s7NdFOD0Nbg48Pp0c")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("groups", cmd_groups))
    
    app.add_handler(CommandHandler("setwelcome", cmd_setwelcome))
    app.add_handler(CommandHandler("setgoodbye", cmd_setgoodbye))
    app.add_handler(CommandHandler("setflood", cmd_setflood))
    app.add_handler(CommandHandler("status", cmd_status))
    
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("lock", cmd_lock))
    app.add_handler(CommandHandler("unlock", cmd_unlock))
    app.add_handler(CommandHandler("slowmode", cmd_slowmode))
    app.add_handler(CommandHandler("setrules", cmd_setrules))
    app.add_handler(CommandHandler("rules", cmd_rules))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("addkeyword", cmd_addkeyword))
    app.add_handler(CommandHandler("purge", cmd_purge))

    app.add_handler(ChatMemberHandler(handle_chat_member_updates, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.run_polling()

if __name__ == "__main__":
    main()
