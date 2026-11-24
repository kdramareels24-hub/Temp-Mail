import os
import re
import sys
import time
import html
import email
import random
import string
import sqlite3
import imaplib
import threading
from email.header import decode_header
from bs4 import BeautifulSoup
import telebot
from telebot import types

# ================= ENV VARIABLES =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()

DOMAIN = os.getenv("DOMAIN", "heoo.in").strip().lower()
IMAP_HOST = os.getenv("IMAP_HOST", "imap.hostinger.com").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_EMAIL = os.getenv("IMAP_EMAIL", "").strip()
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "").strip()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))
START_LENGTH = int(os.getenv("START_LENGTH", "5"))
DB_FILE = os.getenv("DB_FILE", "mailbot.db").strip()

# Startup validation
missing = []
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
if not ADMIN_ID_RAW.isdigit():
    missing.append("ADMIN_ID (numeric)")
if not IMAP_EMAIL:
    missing.append("IMAP_EMAIL")
if not IMAP_PASSWORD:
    missing.append("IMAP_PASSWORD")

if missing:
    print(f"[ERROR] Missing required Railway Variables: {', '.join(missing)}", flush=True)
    sys.exit(1)

ADMIN_ID = int(ADMIN_ID_RAW)

# Agar Railway Volume path use ho raha ho (e.g. /data/mailbot.db) toh folder ensure karein
db_dir = os.path.dirname(DB_FILE)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
# =================================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
db_lock = threading.Lock()


# ================= DATABASE =================
def init_db():
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (ADMIN_ID,))
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('mail_len', ?)", (str(START_LENGTH),))
        conn.commit()


def is_authorized(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def get_setting(key: str, default: str = None):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def generate_unique_email(user_id: int) -> str:
    """Chhote random mail se start karta hai; collision hone par length auto badha deta hai."""
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cur_len = int(conn.execute("SELECT value FROM settings WHERE key = 'mail_len'").fetchone()[0])
        chars = string.ascii_lowercase + string.digits

        while True:
            for _ in range(15):
                prefix = random.choice(string.ascii_lowercase) + "".join(random.choices(chars, k=cur_len - 1))
                addr = f"{prefix}@{DOMAIN}"
                exists = conn.execute("SELECT 1 FROM emails WHERE address = ?", (addr,)).fetchone()
                if not exists:
                    conn.execute("INSERT INTO emails (user_id, address) VALUES (?, ?)", (user_id, addr))
                    conn.commit()
                    return addr

            cur_len += 1
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('mail_len', ?)", (str(cur_len),))
            conn.commit()


def get_user_emails(user_id: int):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        return conn.execute(
            "SELECT id, address FROM emails WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        ).fetchall()


def find_email_owner(address: str):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            "SELECT user_id FROM emails WHERE LOWER(address) = LOWER(?)",
            (address.strip(),)
        ).fetchone()
        return row[0] if row else None


def deny_access(message):
    bot.reply_to(
        message,
        "⛔ <b>Access Denied</b>\n\n"
        "Aapko is bot ko use karne ki permission nahi hai.\n"
        f"Apna Telegram ID admin ko bhejein: <code>{message.from_user.id}</code>"
    )


# ================= COMMANDS =================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    if not is_authorized(uid):
        return deny_access(message)

    text = (
        f"👋 <b>Welcome to {DOMAIN} Mail Bot</b>\n\n"
        "Is bot se aap instant private email generate kar sakte hain aur sabhi OTP / messages direct yahan live receive honge.\n\n"
        "<b>Commands:</b>\n"
        "• /getmail — Naya random email generate karein\n"
        "• /mymails — Apne saare active emails dekhein\n"
        "• /deletemail — Kisi ek ya saare emails delete karein\n\n"
        "⚡ <i>Note: Kisi bhi generated mail par OTP ya message aate hi automatically yahan show ho jayega.</i>"
    )

    if uid == ADMIN_ID:
        text += (
            "\n\n🔐 <b>Admin Commands:</b>\n"
            "• <code>/adduser 12345678</code> — User ko access dein\n"
            "• <code>/deluser 12345678</code> — User ka access hatayein"
        )

    bot.reply_to(message, text)


@bot.message_handler(commands=["getmail"])
def cmd_getmail(message):
    uid = message.from_user.id
    if not is_authorized(uid):
        return deny_access(message)

    new_mail = generate_unique_email(uid)
    bot.reply_to(
        message,
        "✅ <b>Naya Email Generate Ho Gaya:</b>\n\n"
        f"📧 <code>{new_mail}</code>\n\n"
        "<i>(Copy karne ke liye email par tap karein. Is par aane wale saare OTP aur mails yahin aayenge.)</i>"
    )


@bot.message_handler(commands=["mymails"])
def cmd_mymails(message):
    uid = message.from_user.id
    if not is_authorized(uid):
        return deny_access(message)

    mails = get_user_emails(uid)
    if not mails:
        return bot.reply_to(
            message,
            "📭 Aapne abhi tak koi email create nahi kiya hai.\nNaya banane ke liye /getmail bhejein."
        )

    # Line-by-line chunking taaki koi HTML tag beech me na tute
    chunk = f"<b>📋 Aapke Active Emails ({len(mails)}):</b>\n\n"
    for idx, (_, addr) in enumerate(mails, start=1):
        line = f"{idx}. <code>{addr}</code>\n"
        if len(chunk) + len(line) > 3900:
            bot.send_message(message.chat.id, chunk)
            chunk = line
        else:
            chunk += line

    if chunk.strip():
        bot.send_message(message.chat.id, chunk)


def build_delete_keyboard(user_id: int):
    mails = get_user_emails(user_id)
    if not mails:
        return None
    markup = types.InlineKeyboardMarkup(row_width=1)
    for mail_id, addr in mails:
        markup.add(types.InlineKeyboardButton(f"🗑 {addr}", callback_data=f"del_one:{mail_id}"))
    markup.add(types.InlineKeyboardButton("🔥 Delete All Mails", callback_data="del_all_ask"))
    return markup


@bot.message_handler(commands=["deletemail"])
def cmd_deletemail(message):
    uid = message.from_user.id
    if not is_authorized(uid):
        return deny_access(message)

    markup = build_delete_keyboard(uid)
    if not markup:
        return bot.reply_to(message, "📭 Delete karne ke liye koi bhi email nahi hai.")

    bot.reply_to(
        message,
        "🗑 <b>Konsa Email Delete Karna Hai?</b>\nNeeche kisi bhi email par tap karein:",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("del_"))
def handle_delete_callbacks(call):
    uid = call.from_user.id
    if not is_authorized(uid):
        return bot.answer_callback_query(call.id, "Access Denied!", show_alert=True)

    data = call.data

    if data.startswith("del_one:"):
        mail_id = int(data.split(":")[1])
        with db_lock, sqlite3.connect(DB_FILE) as conn:
            row = conn.execute(
                "SELECT address FROM emails WHERE id = ? AND user_id = ?", (mail_id, uid)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM emails WHERE id = ? AND user_id = ?", (mail_id, uid))
                conn.commit()
                bot.answer_callback_query(call.id, f"Deleted: {row[0]}")
            else:
                bot.answer_callback_query(call.id, "Email pehle hi delete ho chuka hai.")

        markup = build_delete_keyboard(uid)
        if markup:
            bot.edit_message_text(
                "🗑 <b>Konsa Email Delete Karna Hai?</b>\nNeeche kisi bhi email par tap karein:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
        else:
            bot.edit_message_text(
                "✅ Saare emails delete ho chuke hain.",
                call.message.chat.id,
                call.message.message_id
            )

    elif data == "del_all_ask":
        confirm_kb = types.InlineKeyboardMarkup(row_width=2)
        confirm_kb.add(
            types.InlineKeyboardButton("✅ Haan, Sab Delete Karein", callback_data="del_all_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="del_cancel")
        )
        bot.edit_message_text(
            "⚠️ <b>Kya aap pakka apne saare emails delete karna chahte hain?</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=confirm_kb
        )

    elif data == "del_all_confirm":
        with db_lock, sqlite3.connect(DB_FILE) as conn:
            conn.execute("DELETE FROM emails WHERE user_id = ?", (uid,))
            conn.commit()
        bot.answer_callback_query(call.id, "Saare emails delete ho gaye!")
        bot.edit_message_text(
            "🔥 <b>Aapke saare emails records se hata diye gaye hain.</b>",
            call.message.chat.id,
            call.message.message_id
        )

    elif data == "del_cancel":
        markup = build_delete_keyboard(uid)
        if markup:
            bot.edit_message_text(
                "🗑 <b>Konsa Email Delete Karna Hai?</b>\nNeeche kisi bhi email par tap karein:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )


# ================= ADMIN COMMANDS =================
@bot.message_handler(commands=["adduser"])
def cmd_adduser(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ Ye command sirf Admin ke liye hai.")

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(message, "⚠️ Sahi format use karein:\n<code>/adduser 12345678</code>")

    target_id = int(parts[1])
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_id,))
        conn.commit()

    bot.reply_to(message, f"✅ User <code>{target_id}</code> ko bot access de diya gaya hai.")


@bot.message_handler(commands=["deluser"])
def cmd_deluser(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "⛔ Ye command sirf Admin ke liye hai.")

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(message, "⚠️ Sahi format use karein:\n<code>/deluser 12345678</code>")

    target_id = int(parts[1])
    if target_id == ADMIN_ID:
        return bot.reply_to(message, "⚠️ Admin khud ko remove nahi kar sakta.")

    with db_lock, sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (target_id,))
        conn.commit()

    bot.reply_to(message, f"🚫 User <code>{target_id}</code> ka access hata diya gaya hai.")


# ================= IMAP LIVE MAIL POLLER =================
def decode_mime_str(value):
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, enc in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def extract_body(msg) -> str:
    plain_text = ""
    html_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp.lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and not plain_text:
                plain_text = decoded
            elif ctype == "text/html" and not html_text:
                html_text = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_text = decoded
            else:
                plain_text = decoded

    if plain_text.strip():
        return plain_text.strip()

    if html_text.strip():
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
        return "\n".join(lines)

    return "(Empty Message)"


def extract_target_domain_emails(msg) -> set:
    headers_to_check = [
        msg.get("To", ""),
        msg.get("Delivered-To", ""),
        msg.get("X-Original-To", ""),
        msg.get("Envelope-To", ""),
        msg.get("Cc", ""),
    ]
    combined = " ".join(headers_to_check)
    pattern = rf"[a-zA-Z0-9._%+-]+@{re.escape(DOMAIN)}"
    return set(addr.lower() for addr in re.findall(pattern, combined, flags=re.IGNORECASE))


def process_email_message(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    recipients = extract_target_domain_emails(msg)

    sender = html.escape(decode_mime_str(msg.get("From", "Unknown")))
    subject = html.escape(decode_mime_str(msg.get("Subject", "(No Subject)")))
    body = extract_body(msg)

    for recipient in recipients:
        owner_id = find_email_owner(recipient)
        if not owner_id or not is_authorized(owner_id):
            continue

        safe_body = html.escape(body)
        header_text = (
            f"📩 <b>Naya Mail Aaya Hai!</b>\n\n"
            f"<b>To:</b> <code>{html.escape(recipient)}</code>\n"
            f"<b>From:</b> {sender}\n"
            f"<b>Subject:</b> {subject}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        max_body_len = 3800 - len(header_text)
        if len(safe_body) <= max_body_len:
            try:
                bot.send_message(owner_id, header_text + safe_body)
            except Exception as e:
                print(f"[TG SEND ERROR] {e}", flush=True)
        else:
            try:
                bot.send_message(owner_id, header_text + safe_body[:max_body_len])
                rest = safe_body[max_body_len:]
                for i in range(0, len(rest), 3900):
                    bot.send_message(owner_id, rest[i:i + 3900])
            except Exception as e:
                print(f"[TG SEND ERROR] {e}", flush=True)


def imap_poller():
    print(f"[IMAP] Connected watcher starting for {IMAP_EMAIL} ({DOMAIN})...", flush=True)
    while True:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(IMAP_EMAIL, IMAP_PASSWORD)
            mail.select("INBOX")

            last_uid = get_setting("last_uid")
            if last_uid is None:
                status, data = mail.uid("search", None, "ALL")
                uids = data[0].split() if status == "OK" and data[0] else []
                last_uid = int(uids[-1]) if uids else 0
                set_setting("last_uid", str(last_uid))
            else:
                last_uid = int(last_uid)

            print(f"[IMAP] Watching INBOX from UID {last_uid}", flush=True)

            while True:
                mail.noop()
                mail.select("INBOX")
                status, data = mail.uid("search", None, f"UID {last_uid + 1}:*")
                if status == "OK" and data[0]:
                    uids = [int(u) for u in data[0].split() if int(u) > last_uid]
                    for uid in sorted(uids):
                        res, msg_data = mail.uid("fetch", str(uid), "(RFC822)")
                        if res == "OK" and msg_data and isinstance(msg_data[0], tuple):
                            process_email_message(msg_data[0][1])
                        last_uid = uid
                        set_setting("last_uid", str(last_uid))

                time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"[IMAP RECONNECTING] Error: {e}", flush=True)
            time.sleep(5)
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass


if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=imap_poller, daemon=True)
    t.start()

    print("[BOT] Telegram Bot is running...", flush=True)
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
