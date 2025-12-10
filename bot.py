import os
import random
import sqlite3
import time
import logging
import signal
import sys
import re
from datetime import datetime
from dotenv import load_dotenv
from telebot import types
import telebot

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN .env da topilmadi")

# optional toolbelt
try:
    from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
    HAS_TOOLBELT = True
except Exception:
    HAS_TOOLBELT = False

# config
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DB_FILE = os.getenv("DB_FILE", "data.db")
VIDEOS_FOLDER = os.getenv("VIDEOS_FOLDER", "videos")
POLLING = os.getenv("BOT_POLLING", "1") == "1"

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

user_state = {}
user_profiles = {}

if VIDEOS_FOLDER and not os.path.exists(VIDEOS_FOLDER):
    os.makedirs(VIDEOS_FOLDER, exist_ok=True)

# Database helpers
def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass

    # create users table with name_changes column (for new DBs)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id TEXT PRIMARY KEY,
            student_name TEXT,
            username TEXT,
            updated_at TEXT,
            name_changes INTEGER DEFAULT 0
        )
    ''')
    # other tables
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            test_name TEXT,
            correct_answers TEXT,
            created_at TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT,
            username TEXT,
            tg_id TEXT,
            test_id TEXT,
            correct_count INTEGER,
            incorrect_count INTEGER,
            date TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            video_id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT UNIQUE,
            video_url TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()

    # ensure existing DB has name_changes column (safe migration)
    cur.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in cur.fetchall()]  # second col is name
    if "name_changes" not in cols:
        try:
            cur.execute("ALTER TABLE users ADD COLUMN name_changes INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

    conn.close()

def query_db(query, params=(), fetch=False, many=False):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
        cur = conn.cursor()
        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return rows
    except sqlite3.Error as e:
        logger.exception("DB error")
        try:
            conn.close()
        except Exception:
            pass
        return None

def save_profile(chat_id, student_name, username=None, name_changes=None):
    """
    Insert or update user profile. If name_changes is None, preserve existing value (or default 0).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = query_db("SELECT name_changes FROM users WHERE chat_id = ?", (str(chat_id),), fetch=True)
    existing_count = existing[0][0] if existing else 0
    if name_changes is None:
        name_changes = existing_count or 0
    query_db(
        "INSERT OR REPLACE INTO users (chat_id, student_name, username, updated_at, name_changes) VALUES (?, ?, ?, ?, ?)",
        (str(chat_id), student_name, username, now, name_changes)
    )
    user_profiles[chat_id] = student_name

def load_profile(chat_id):
    if chat_id in user_profiles:
        return user_profiles[chat_id]
    r = query_db("SELECT student_name FROM users WHERE chat_id = ?", (str(chat_id),), fetch=True)
    if r:
        user_profiles[chat_id] = r[0][0]
        return r[0][0]
    return None

def get_name_changes(chat_id):
    r = query_db("SELECT name_changes FROM users WHERE chat_id = ?", (str(chat_id),), fetch=True)
    if r and r[0][0] is not None:
        return int(r[0][0])
    return 0

def increment_name_changes(chat_id):
    current = get_name_changes(chat_id)
    new = current + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exists = query_db("SELECT 1 FROM users WHERE chat_id = ?", (str(chat_id),), fetch=True)
    if exists:
        query_db("UPDATE users SET name_changes = ?, updated_at = ? WHERE chat_id = ?", (new, now, str(chat_id)))
    else:
        # insert minimal record (student_name empty) to track count
        query_db("INSERT INTO users (chat_id, student_name, username, updated_at, name_changes) VALUES (?, ?, ?, ?, ?)",
                 (str(chat_id), "", None, now, new))
    return new

# Utilities
def generate_test_id():
    prefix = random.choice("TABCDEF")
    digits = ''.join(random.choices("0123456789", k=4))
    return prefix + digits

def extract_answers(text):
    return [ch.lower() for ch in text if ch.lower() in ['a', 'b', 'c', 'd', 'e']]

def admin_main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("➕ Test qo'shish", "📊 Natijalarni ko'rish")
    m.add("🗑 Testni o'chirish", "🎬 Video qo'shish")
    m.add("🗑 Videoni o'chirish", "📅 Bugungi natijalar")
    return m

def user_main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("📝 Test topshirish", "📈 Mening natijalarim")
    m.add("🎬 Videolar", "✏️ Ismni tahrirlash")
    return m

def back_button():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("⬅️ Orqaga")
    return m

def generate_tests_menu():
    tests = query_db("SELECT test_id, test_name FROM tests ORDER BY created_at DESC", fetch=True) or []
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in tests:
        m.add(f"{test_name} ({test_id})")
    m.add("⬅️ Orqaga")
    return m

# Handlers
@bot.message_handler(commands=['start', 'admin'])
def start(message):
    if message.from_user.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🧑‍💼 Salom, admin!", reply_markup=admin_main_menu())
        return
    existing_name = load_profile(message.chat.id)
    if existing_name:
        bot.send_message(message.chat.id, f"Assalomu alaykum, {existing_name}!", reply_markup=user_main_menu())
        user_state.setdefault(message.chat.id, {})["username"] = message.from_user.username or None
    else:
        bot.send_message(message.chat.id, "Assalomu alaykum! Ism familiyangizni kiriting:")
        user_state[message.chat.id] = {"step": "get_name", "username": message.from_user.username or None}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_name")
def get_name(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)
    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "❌ Ism familiyangizni kiriting, bo'sh bo'lmaydi.")
        return
    user_state.setdefault(message.chat.id, {})["student_name"] = name
    user_profiles[message.chat.id] = name
    user_state[message.chat.id]["step"] = "main_menu"
    save_profile(message.chat.id, name, message.from_user.username or None)
    bot.send_message(message.chat.id, f"👋 Xush kelibsiz, {name}!", reply_markup=user_main_menu())

@bot.message_handler(func=lambda m: m.text == "✏️ Ismni tahrirlash")
def edit_name_start(message):
    existing_name = load_profile(message.chat.id)
    if not existing_name:
        bot.send_message(message.chat.id, "📭 Profil topilmadi. Iltimos /start orqali ism kiriting.")
        return

    changes = get_name_changes(message.chat.id)
    remaining = max(0, 3 - changes)
    if remaining <= 0:
        bot.send_message(message.chat.id, "❌ Siz allaqachon ismni 3 marotaba o'zgartirdingiz. Yana o'zgartira olmaysiz.", reply_markup=user_main_menu())
        return

    # Show special warning/messages depending on how many times changed already
    if changes == 0:
        info = "Siz ismingizni 3 marotaba o'zgartira olishingiz mumkin."
    else:
        info = f"Siz avval {changes} marta o'zgartirgansiz — sizda {remaining} ta qoldi."

    bot.send_message(message.chat.id, f"🖊 Hozirgi ismingiz: {existing_name}\n{info}\nYangi ismni kiriting:", reply_markup=back_button())
    user_state[message.chat.id] = {"step": "edit_name", "old_name": existing_name}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "edit_name")
def handle_edit_name(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)

    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "❌ Ism bo'sh bo'lmasligi kerak. Iltimos yangi ism kiriting:")
        return

    # Check remaining before applying
    changes = get_name_changes(message.chat.id)
    remaining = max(0, 3 - changes)
    if remaining <= 0:
        bot.send_message(message.chat.id, "❌ Siz allaqachon ismni 3 marotaba o'zgartirgansiz. Yana o'zgartira olmaysiz.", reply_markup=user_main_menu())
        user_state.pop(message.chat.id, None)
        return

    # increment and save
    new_count = increment_name_changes(message.chat.id)
    username = message.from_user.username or None
    save_profile(message.chat.id, new_name, username, name_changes=new_count)
    user_state.pop(message.chat.id, None)
    remaining_after = max(0, 3 - new_count)
    bot.send_message(message.chat.id, f"✅ Ismingiz yangilandi: {new_name}\nSiz yana {remaining_after} marta o'zgartira olasiz.", reply_markup=user_main_menu())

@bot.message_handler(func=lambda m: m.text == "➕ Test qo'shish")
def add_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "🧾 Test nomini kiriting:", reply_markup=back_button())
    user_state[message.chat.id] = {"step": "get_test_name"}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_test_name")
def get_test_name(message):
    # handle back button here
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)

    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "❌ Test nomi bo'sh bo'lmasligi kerak.")
        return
    user_state[message.chat.id]["test_name"] = name
    user_state[message.chat.id]["step"] = "get_correct_answers"
    bot.send_message(message.chat.id, "To'g'ri javoblarni kiriting (masalan: XXX 1a2b3c...):")

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_correct_answers")
def save_test(message):
    # handle back button here
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)

    data = user_state.pop(message.chat.id, {})
    text = message.text.strip()
    if not text or not any(ch.isdigit() for ch in text):
        bot.send_message(message.chat.id, "❌ Javoblar to'g'ri formatda bo'lishi kerak.")
        return
    if "-" in text:
        test_id, answers = text.split("-", 1)
        test_id = test_id.strip()
    else:
        test_id = generate_test_id()
        answers = text
    correct = "".join(extract_answers(answers))
    query_db(
        "INSERT OR REPLACE INTO tests (test_id, test_name, correct_answers, created_at) VALUES (?, ?, ?, ?)",
        (test_id, data.get("test_name"), correct, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    bot.send_message(message.chat.id, f"✅ Test saqlandi!\n🆔 {test_id}\n📘 {data.get('test_name')}", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.text == "🎬 Video qo'shish")
def add_video_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tests = query_db("SELECT test_id, test_name FROM tests ORDER BY created_at DESC", fetch=True)
    if not tests:
        bot.send_message(message.chat.id, "📭 Hozircha testlar mavjud emas. Avval test qo'shing.", reply_markup=admin_main_menu())
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in tests:
        kb.add(f"🎬 {test_name} ({test_id})")
    kb.add("⬅️ Orqaga")
    bot.send_message(message.chat.id, "Video qo'shish uchun test tanlang:", reply_markup=kb)
    user_state[message.chat.id] = {"step": "select_test_for_video"}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "select_test_for_video")
def select_test_for_video(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)
    test_id = message.text.split("(")[-1].replace(")", "").strip()
    user_state[message.chat.id] = {"step": "get_video_url", "test_id": test_id}
    bot.send_message(message.chat.id, "🎥 YouTube video linkini kiriting:")

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_video_url")
def get_video_url(message):
    video_url = message.text.strip()
    chat_id = message.chat.id
    state = user_state.get(chat_id, {})
    test_id = state.get("test_id")
    if not test_id:
        bot.send_message(chat_id, "❌ Ichki xato: test ID topilmadi.", reply_markup=admin_main_menu())
        user_state.pop(chat_id, None)
        return
    if not video_url.startswith("http"):
        bot.send_message(chat_id, "❌ To'g'ri YouTube linki kiriting!")
        return
    query_db(
        "INSERT OR REPLACE INTO videos (test_id, video_url, created_at) VALUES (?, ?, ?)",
        (test_id, video_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    user_state.pop(chat_id, None)
    bot.send_message(chat_id, f"✅ YouTube link saqlandi.\n🆔 {test_id}\n🔗 {video_url}", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.text == "🗑 Videoni o'chirish")
def delete_video_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    videos = query_db("SELECT v.test_id, t.test_name FROM videos v LEFT JOIN tests t ON v.test_id = t.test_id ORDER BY v.created_at DESC", fetch=True)
    if not videos:
        bot.send_message(message.chat.id, "📭 Hozircha hech qanday video qo'shilmagan.", reply_markup=admin_main_menu())
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in videos:
        kb.add(f"🗑 {test_name or 'Nomalum'} ({test_id})")
    kb.add("⬅️ Orqaga")
    bot.send_message(message.chat.id, "O'chirish uchun videoni tanlang:", reply_markup=kb)
    user_state[message.chat.id] = {"step": "delete_video"}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "delete_video")
def delete_selected_video(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)
    test_id = message.text.split("(")[-1].replace(")", "").strip()
    video = query_db("SELECT video_url FROM videos WHERE test_id = ?", (test_id,), fetch=True)
    if not video:
        bot.send_message(message.chat.id, "❌ Bunday video topilmadi.", reply_markup=admin_main_menu())
        user_state.pop(message.chat.id, None)
        return
    query_db("DELETE FROM videos WHERE test_id = ?", (test_id,))
    if VIDEOS_FOLDER and os.path.isdir(VIDEOS_FOLDER):
        try:
            for f in os.listdir(VIDEOS_FOLDER):
                if f.startswith(test_id):
                    try:
                        os.remove(os.path.join(VIDEOS_FOLDER, f))
                    except Exception:
                        pass
        except Exception:
            pass
    user_state.pop(message.chat.id, None)
    bot.send_message(message.chat.id, f"✅ Video o'chirildi.\n🆔 {test_id}", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.text == "🗑 Testni o'chirish")
def delete_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tests = query_db("SELECT test_id, test_name FROM tests ORDER BY created_at DESC", fetch=True)
    if not tests:
        bot.send_message(message.chat.id, "📭 O'chirish uchun testlar yo'q.", reply_markup=admin_main_menu())
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in tests:
        kb.add(f"❌ {test_name} ({test_id})")
    kb.add("⬅️ Orqaga")
    bot.send_message(message.chat.id, "O'chirish uchun testni tanlang:", reply_markup=kb)
    user_state[message.chat.id] = {"step": "delete_test"}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "delete_test")
def delete_selected_test(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)
    test_id = message.text.split("(")[-1].replace(")", "").strip()
    test = query_db("SELECT test_name FROM tests WHERE test_id = ?", (test_id,), fetch=True)
    if not test:
        bot.send_message(message.chat.id, "❌ Test topilmadi.")
        return
    query_db("DELETE FROM tests WHERE test_id = ?", (test_id,))
    query_db("DELETE FROM results WHERE test_id = ?", (test_id,))
    query_db("DELETE FROM videos WHERE test_id = ?", (test_id,))
    if VIDEOS_FOLDER and os.path.isdir(VIDEOS_FOLDER):
        try:
            for f in os.listdir(VIDEOS_FOLDER):
                if f.startswith(test_id):
                    try:
                        os.remove(os.path.join(VIDEOS_FOLDER, f))
                    except Exception:
                        pass
        except Exception:
            pass
    user_state.pop(message.chat.id, None)
    bot.send_message(message.chat.id, f"✅ Test o'chirildi!\n🆔 {test_id}", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and "(" in m.text and ")" in m.text and m.text != "⬅️ Orqaga")
def admin_view_results(message):
    test_id = message.text.split("(")[-1].replace(")", "").strip()
    test = query_db("SELECT test_name FROM tests WHERE test_id = ?", (test_id,), fetch=True)
    if not test:
        return
    results = query_db("SELECT student_name, username, tg_id, correct_count, incorrect_count, date FROM results WHERE test_id = ? ORDER BY id ASC", (test_id,), fetch=True)
    if not results:
        bot.send_message(message.chat.id, f"📭 Bu testni hali hech kim ishlamagan.\n🆔 {test_id}")
        return
    
    text = f"📊 <b>{test[0][0]}</b>\n🆔 {test_id}\n\n"
    
    # Group results by student
    grouped_by_student = {}
    for r in results:
        student_name, username, tg_id, correct, incorrect, date = r
        key = (student_name, username, tg_id)
        if key not in grouped_by_student:
            grouped_by_student[key] = []
        grouped_by_student[key].append((correct, incorrect, date))
    
    # Display results grouped by student with attempt numbers
    for (student_name, username, tg_id), attempts in grouped_by_student.items():
        user_display = f"@{username}" if username else f"tg:{tg_id}"
        text += f"🧑‍🎓 <b>{student_name}</b> ({user_display})\n"
        for attempt_num, (correct, incorrect, date) in enumerate(attempts, 1):
            text += f"  {attempt_num}-natijasi: ✅ {correct} | ❌ {incorrect} | 🕓 {date}\n"
        text += "\n"
    
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni ko'rish")
def show_test_list(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tests = query_db("SELECT * FROM tests", fetch=True)
    if not tests:
        bot.send_message(message.chat.id, "📭 Hozircha testlar mavjud emas.", reply_markup=admin_main_menu())
        return
    bot.send_message(message.chat.id, "📋 Testlar ro'yxati:", reply_markup=generate_tests_menu())

# grouped today/results handlers (unchanged) ...
@bot.message_handler(commands=['results'])
def results_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) >= 2 and parts[1].lower() in ("today", "bugun"):
        today = datetime.now().strftime("%Y-%m-%d")
        rows = query_db(
            "SELECT student_name, username, tg_id, test_id, correct_count, incorrect_count, date "
            "FROM results WHERE date LIKE ? ORDER BY student_name ASC, username ASC, tg_id ASC, test_id ASC, date ASC",
            (f"{today}%",),
            fetch=True
        )
        if not rows:
            bot.send_message(message.chat.id, f"📭 Bugun hozircha natijalar yo'q.", reply_markup=admin_main_menu())
            return

        grouped = {}
        for r in rows:
            student_name, username, tg_id, test_id, correct, incorrect, date = r
            key = (student_name, username, tg_id)
            student_entry = grouped.setdefault(key, {})
            student_entry.setdefault(test_id, []).append((correct, incorrect, date))

        text = f"📅 <b>Bugungi natijalar ({today})</b>\n\n"
        for (student_name, username, tg_id), tests in grouped.items():
            user_display = f"@{username}" if username else f"tg:{tg_id}"
            text += f"🧑‍🎓 <b>{student_name}</b> ({user_display})\n"
            for test_id, attempts in tests.items():
                text += f"  🆔 <b>{test_id}</b>\n"
                for idx, (correct, incorrect, date) in enumerate(attempts, 1):
                    text += f"    {idx}-natijasi: ✅ {correct} | ❌ {incorrect} | 🕓 {date}\n"
                text += "\n"
            text += "\n"

        bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "Foydalanish: /results today", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.text == "📅 Bugungi natijalar")
def show_today_results(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    rows = query_db(
        "SELECT student_name, username, tg_id, test_id, correct_count, incorrect_count, date "
        "FROM results WHERE date LIKE ? ORDER BY student_name ASC, username ASC, tg_id ASC, test_id ASC, date ASC",
        (f"{today}%",),
        fetch=True
    )
    if not rows:
        bot.send_message(message.chat.id, f"📭 Bugun hozircha natijalar yo'q.", reply_markup=admin_main_menu())
        return

    grouped = {}
    for r in rows:
        student_name, username, tg_id, test_id, correct, incorrect, date = r
        key = (student_name, username, tg_id)
        student_entry = grouped.setdefault(key, {})
        student_entry.setdefault(test_id, []).append((correct, incorrect, date))

    text = f"📅 <b>Bugungi natijalar ({today})</b>\n\n"
    for (student_name, username, tg_id), tests in grouped.items():
        user_display = f"@{username}" if username else f"tg:{tg_id}"
        text += f"🧑‍🎓 <b>{student_name}</b> ({user_display})\n"
        for test_id, attempts in tests.items():
            text += f"  🆔 <b>{test_id}</b>\n"
            for idx, (correct, incorrect, date) in enumerate(attempts, 1):
                text += f"    {idx}-natijasi: ✅ {correct} | ❌ {incorrect} | 🕓 {date}\n"
            text += "\n"
        text += "\n"

    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=admin_main_menu())

@bot.message_handler(func=lambda m: m.text == "📝 Test topshirish")
def submit_test_start(message):
    saved_name = load_profile(message.chat.id) or user_state.get(message.chat.id, {}).get("student_name")
    user_state[message.chat.id] = {"step": "get_test_answers", "student_name": saved_name}
    bot.send_message(message.chat.id, "Test ID va javoblaringizni yuboring:\nMasalan: <b>B4086 1a2b3c...</b>", reply_markup=back_button(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_test_answers")
def process_test_answers(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)

    state = user_state.get(message.chat.id, {})
    student_name = state.get("student_name") or load_profile(message.chat.id) or "Unknown"
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)

    text = (message.text or "").strip()
    parts = text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Noto'g'ri format. Masalan:\n<b>B4086 1a2b3c...</b>", parse_mode="HTML")
        return
    test_id, user_answers = parts[0], ''.join(parts[1:])
    test = query_db("SELECT correct_answers FROM tests WHERE test_id = ?", (test_id,), fetch=True)
    if not test:
        bot.send_message(message.chat.id, "❌ Bunday test topilmadi.")
        return

    correct_list = extract_answers(test[0][0])
    user_list = extract_answers(user_answers)
    if not user_list:
        bot.send_message(message.chat.id, "❌ Javoblarda A-E orasidagi harflar bo'lishi shart.")
        return

    total_questions = len(correct_list)
    correct = 0
    incorrect_details = []
    for i in range(total_questions):
        ua = user_list[i] if i < len(user_list) else None
        ca = correct_list[i]
        if ua is not None and ua == ca:
            correct += 1
        else:
            incorrect_details.append((i + 1, ua))

    incorrect = total_questions - correct

    query_db(
        "INSERT INTO results (student_name, username, tg_id, test_id, correct_count, incorrect_count, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (student_name, username, tg_id, test_id, correct, incorrect, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    user_display = f"@{username}" if username else f"tg:{tg_id}"

    # Count how many times this user has submitted this test
    result_count = query_db(
        "SELECT COUNT(*) FROM results WHERE (username = ? OR tg_id = ?) AND test_id = ?",
        (username, tg_id, test_id),
        fetch=True
    )
    attempt_number = result_count[0][0] if result_count else 1

    # Send confirmation to user WITH incorrect answers (only user answers, NOT correct answers)
    result_text = f"📊 Natijangiz:\n🧑‍🎓 {student_name} ({user_display})\n"
    result_text += f"🆔 {test_id}\n✅ {correct}\n❌ {incorrect}\n"
    
    if incorrect_details:
        result_text += "\n❗ Xato javoblar:\n"
        for qnum, ua in incorrect_details:
            ua_display = ua.upper() if ua else "—"
            result_text += f"{qnum}-savol: Siz belgilagan javob <b>{ua_display}</b> ❌\n"

    bot.send_message(message.chat.id, result_text, reply_markup=user_main_menu(), parse_mode="HTML")

    # send details to admins AFTER test submission
    admin_caption = f"📥 Test topshirildi ({attempt_number}-natijasi):\n🧑‍🎓 {student_name}\n🆔 {test_id}\n✅ {correct} | ❌ {incorrect}\n{('@' + username) if username else 'tg:' + tg_id}"

    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, admin_caption)
        except Exception:
            pass

    # clear state
    user_state.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📈 Mening natijalarim")
def show_my_results(message):
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)
    results = query_db("SELECT test_id, correct_count, incorrect_count, date FROM results WHERE username = ? OR tg_id = ? ORDER BY test_id ASC, date ASC", (username, tg_id), fetch=True)
    if not results:
        bot.send_message(message.chat.id, "📭 Siz hali testlarni topshirmadingiz.", reply_markup=user_main_menu())
        return
    
    text = "📊 <b>Sizning natijalaringiz:</b>\n\n"
    
    # Group results by test_id
    grouped_results = {}
    for r in results:
        test_id, correct, incorrect, date = r
        if test_id not in grouped_results:
            grouped_results[test_id] = []
        grouped_results[test_id].append((correct, incorrect, date))
    
    # Display results grouped by test
    for test_id, attempts in grouped_results.items():
        text += f"<b>🆔 {test_id}</b>\n"
        for attempt_num, (correct, incorrect, date) in enumerate(attempts, 1):
            total = correct + incorrect
            percentage = (correct / total * 100) if total > 0 else 0
            text += f"  {attempt_num}-natijangiz: ✅ {correct} | ❌ {incorrect} | 📊 {percentage:.1f}% | 🕓 {date}\n"
        text += "\n"
    
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=user_main_menu())

@bot.message_handler(func=lambda m: m.text == "🎬 Videolar")
def show_user_videos(message):
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)
    videos = query_db("SELECT v.test_id, t.test_name, v.video_url FROM videos v LEFT JOIN tests t ON v.test_id = t.test_id ORDER BY v.created_at ASC", fetch=True)
    
    if not videos:
        bot.send_message(message.chat.id, "📭 Hozircha hech qanday video qo'shilmagan.", reply_markup=user_main_menu())
        return
    
    kb = types.InlineKeyboardMarkup()
    any_button = False
    for idx, v in enumerate(videos, 1):
        test_id, test_name, video_url = v
        if video_url:
            test_name = test_name or "Noma'lum test"
            kb.add(types.InlineKeyboardButton(text=f"{idx}-{test_name} ({test_id})", url=video_url))
            any_button = True
    
    if not any_button:
        bot.send_message(message.chat.id, "📭 Hozircha hech qanday video qo'shilmagan.", reply_markup=user_main_menu())
        return
    
    bot.send_message(message.chat.id, "🎬 Quyidagi tugmalardan videoni oching:", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "⬅️ Orqaga")
def go_back(message):
    if message.from_user.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🏠 Bosh menyu", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "🏠 Bosh menyu", reply_markup=user_main_menu())
    user_state.pop(message.chat.id, None)

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = (
        "🤖 Bot funksiyalari:\n\n"
        "👤 O'quvchilar: /start, 📝 Test topshirish, 📈 Mening natijalarim, 🎬 Videolar\n"
        "🧑‍💼 Adminlar: /admin, ➕ Test qo'shish, 📊 Natijalar, 🗑 Testni o'chirish, 🎬 Video qo'shish, 🗑 Videoni o'chirish\n"
        "Qo'mondalar: /results today"
    )
    bot.send_message(message.chat.id, help_text)

# graceful shutdown
def shutdown(signum, frame):
    logger.info("Shutting down...")
    try:
        bot.stop_polling()
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    init_db()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    logger.info("🤖 Bot ishga tushdi...")

    if POLLING:
        while True:
            try:
                bot.polling(none_stop=True, timeout=20, long_polling_timeout=20)
            except Exception as e:
                logger.exception(f"Polling xatosi: {e}, 5 soniyadan so'ng qayta urinish...")
                time.sleep(5)
    else:
        logger.info("Webhook mode not configured. Set BOT_POLLING=1 to use polling.")
        
        
        