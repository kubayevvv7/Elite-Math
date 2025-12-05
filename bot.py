import os
import random
import sqlite3
import time
import logging
import signal
import sys
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
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS").split(",") if x.strip()]
DB_FILE = os.getenv("DB_FILE", "data.db")
VIDEOS_FOLDER = os.getenv("VIDEOS_FOLDER", "videos")
POLLING = os.getenv("BOT_POLLING", "1") == "1"  # set 0 to use webhook (not configured here)

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")


user_state = {}
user_profiles = {}  

if VIDEOS_FOLDER and not os.path.exists(VIDEOS_FOLDER):
    os.makedirs(VIDEOS_FOLDER, exist_ok=True)


#Database helpers 
def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass

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
    # persistent users table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id TEXT PRIMARY KEY,
            student_name TEXT,
            username TEXT,
            updated_at TEXT
        )
    ''')
    conn.commit()
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


def save_profile(chat_id, student_name, username=None):
    query_db(
        "INSERT OR REPLACE INTO users (chat_id, student_name, username, updated_at) VALUES (?, ?, ?, ?)",
        (str(chat_id), student_name, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    user_profiles[chat_id] = student_name


def load_profile(chat_id):
    # check cache first
    if chat_id in user_profiles:
        return user_profiles[chat_id]
    r = query_db("SELECT student_name FROM users WHERE chat_id = ?", (str(chat_id),), fetch=True)
    if r:
        user_profiles[chat_id] = r[0][0]
        return r[0][0]
    return None


#Utilities
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
    m.add("🎬 Videolar")
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


#Handlers
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
    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "❌ Ism familiyangizni kiriting, bo'sh bo'lmaydi.")
        return
    user_state.setdefault(message.chat.id, {})["student_name"] = name
    user_profiles[message.chat.id] = name
    user_state[message.chat.id]["step"] = "main_menu"
    save_profile(message.chat.id, name, message.from_user.username or None)
    bot.send_message(message.chat.id, f"👋 Xush kelibsiz, {name}!", reply_markup=user_main_menu())


@bot.message_handler(func=lambda m: m.text == "➕ Test qo'shish")
def add_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "🧾 Test nomini kiriting:", reply_markup=back_button())
    user_state[message.chat.id] = {"step": "get_test_name"}


@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_test_name")
def get_test_name(message):
    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "❌ Test nomi bo'sh bo'lmasligi kerak.")
        return
    user_state[message.chat.id]["test_name"] = name
    user_state[message.chat.id]["step"] = "get_correct_answers"
    bot.send_message(message.chat.id, "To'g'ri javoblarni kiriting (masalan: XXX 1a2b3c...):")


@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_correct_answers")
def save_test(message):
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
        kb.add(f"🗑 {test_name or 'Noma\\lum'} ({test_id})")
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
    # remove any stored file with test_id prefix
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
    results = query_db("SELECT student_name, username, tg_id, correct_count, incorrect_count, date FROM results WHERE test_id = ?", (test_id,), fetch=True)
    if not results:
        bot.send_message(message.chat.id, f"📭 Bu testni hali hech kim ishlamagan.\n🆔 {test_id}")
        return
    text = f"📊 <b>{test[0][0]}</b>\n🆔 {test_id}\n\n"
    for r in results:
        student_name, username, tg_id, correct, incorrect, date = r
        user_display = f"@{username}" if username else f"tg:{tg_id}"
        text += f"🧑‍🎓 {student_name} ({user_display})\n✅ {correct} | ❌ {incorrect}\n🕓 {date}\n\n"
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


@bot.message_handler(commands=['results'])
def results_command(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) >= 2 and parts[1].lower() in ("today", "bugun"):
        today = datetime.now().strftime("%Y-%m-%d")
        results = query_db(
            "SELECT student_name, username, tg_id, test_id, correct_count, incorrect_count, date FROM results WHERE date LIKE ? ORDER BY date DESC",
            (f"{today}%",),
            fetch=True
        )
        if not results:
            bot.send_message(message.chat.id, f"📭 Bugun hozircha natijalar yo'q.", reply_markup=admin_main_menu())
            return
        text = f"📅 <b>Bugungi natijalar ({today})</b>\n\n"
        for r in results:
            student_name, username, tg_id, test_id, correct, incorrect, date = r
            user_display = f"@{username}" if username else f"tg:{tg_id}"
            time_part = date.split(" ")[1] if " " in date else date
            text += f"🧑‍🎓 {student_name} ({user_display})\n🆔 {test_id} | ✅ {correct} | ❌ {incorrect}\n🕓 {time_part}\n\n"
        bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "Foydalanish: /results today", reply_markup=admin_main_menu())


@bot.message_handler(func=lambda m: m.text == "📅 Bugungi natijalar")
def show_today_results(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    results = query_db(
        "SELECT student_name, username, tg_id, test_id, correct_count, incorrect_count, date FROM results WHERE date LIKE ? ORDER BY date DESC",
        (f"{today}%",),
        fetch=True
    )
    if not results:
        bot.send_message(message.chat.id, f"📭 Bugun hozircha natijalar yo'q.", reply_markup=admin_main_menu())
        return
    text = f"📅 <b>Bugungi natijalar ({today})</b>\n\n"
    for r in results:
        student_name, username, tg_id, test_id, correct, incorrect, date = r
        user_display = f"@{username}" if username else f"tg:{tg_id}"
        time_part = date.split(" ")[1] if " " in date else date
        text += f"🧑‍🎓 {student_name} ({user_display})\n🆔 {test_id} | ✅ {correct} | ❌ {incorrect}\n🕓 {time_part}\n\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=admin_main_menu())


@bot.message_handler(func=lambda m: m.text == "📝 Test topshirish")
def submit_test_start(message):
    saved_name = load_profile(message.chat.id) or user_state.get(message.chat.id, {}).get("student_name")
    user_state[message.chat.id] = {"step": "get_test_answers", "student_name": saved_name}
    bot.send_message(message.chat.id, "Test ID va javoblaringizni yuboring:\nMasalan: <b>B4086 1a2b3c...</b>", reply_markup=back_button())


@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id].get("step") == "get_test_answers")
def process_test_answers(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)
    state = user_state.get(message.chat.id, {})
    student_name = state.get("student_name", "Unknown")
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)
    text = message.text.strip()
    parts = text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Noto'g'ri format. Masalan:\n<b>B4086 1a2b3c...</b>", parse_mode="HTML")
        return
    test_id, user_answers = parts[0], ''.join(parts[1:])
    test = query_db("SELECT correct_answers FROM tests WHERE test_id = ?", (test_id,), fetch=True)
    if not test:
        bot.send_message(message.chat.id, "❌ Bunday test topilmadi.")
        return
    already = query_db(
        "SELECT date FROM results WHERE (username = ? OR tg_id = ?) AND test_id = ? ORDER BY date DESC LIMIT 1",
        (username, tg_id, test_id),
        fetch=True
    )
    if already:
        last_date = already[0][0].split(" ")[0]
        if last_date == datetime.now().strftime("%Y-%m-%d"):
            bot.send_message(message.chat.id, "⚠️ Siz bugun bu testni allaqachon topshirgansiz.", reply_markup=user_main_menu())
            return
    correct_list = extract_answers(test[0][0])
    user_list = extract_answers(user_answers)
    if not user_list:
        bot.send_message(message.chat.id, "❌ Javoblarda A-E orasidagi harflar bo'lishi shart.")
        return
    total = min(len(user_list), len(correct_list))
    correct = sum(1 for i in range(total) if user_list[i] == correct_list[i])
    incorrect = len(correct_list) - correct
    query_db(
        "INSERT INTO results (student_name, username, tg_id, test_id, correct_count, incorrect_count, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (student_name, username, tg_id, test_id, correct, incorrect, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    user_display = f"@{username}" if username else f"tg:{tg_id}"
    bot.send_message(message.chat.id, f"📊 Natijangiz:\n🧑‍🎓 {student_name} ({user_display})\n🆔 {test_id}\n✅ {correct}\n❌ {incorrect}", reply_markup=user_main_menu())
    for admin in ADMIN_IDS:
        try:
            bot.send_message(admin, f"📥 {student_name} ({user_display})\n🆔 {test_id}\n✅ {correct} | ❌ {incorrect}")
        except Exception:
            pass
    user_state.pop(message.chat.id, None)


@bot.message_handler(func=lambda m: m.text == "📈 Mening natijalarim")
def show_my_results(message):
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)
    results = query_db("SELECT test_id, correct_count, incorrect_count, date FROM results WHERE username = ? OR tg_id = ? ORDER BY date DESC", (username, tg_id), fetch=True)
    if not results:
        bot.send_message(message.chat.id, "📭 Siz hali testlarni topshirmadingiz.", reply_markup=user_main_menu())
        return
    text = "📊 <b>Sizning natijalaringiz:</b>\n\n"
    for r in results:
        test_id, correct, incorrect, date = r
        total = correct + incorrect
        percentage = (correct / total * 100) if total > 0 else 0
        text += f"🆔 {test_id}\n✅ {correct} | ❌ {incorrect} | 📊 {percentage:.1f}%\n🕓 {date}\n\n"
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=user_main_menu())


@bot.message_handler(func=lambda m: m.text == "🎬 Videolar")
def show_user_videos(message):
    username = message.from_user.username or None
    tg_id = str(message.from_user.id)
    completed_tests = query_db("SELECT DISTINCT test_id FROM results WHERE username = ? OR tg_id = ? ORDER BY date DESC", (username, tg_id), fetch=True)
    if not completed_tests:
        bot.send_message(message.chat.id, "📭 Siz hali testlarni topshirmadingiz.", reply_markup=user_main_menu())
        return
    kb = types.InlineKeyboardMarkup()
    any_button = False
    for t in completed_tests:
        test_id = t[0]
        video = query_db("SELECT video_url FROM videos WHERE test_id = ?", (test_id,), fetch=True)
        test_info = query_db("SELECT test_name FROM tests WHERE test_id = ?", (test_id,), fetch=True)
        test_name = test_info[0][0] if test_info else "Noma'lum test"
        if video and video[0] and video[0][0]:
            kb.add(types.InlineKeyboardButton(text=f"{test_name} ({test_id})", url=video[0][0]))
            any_button = True
    if not any_button:
        bot.send_message(message.chat.id, "📭 Bu testlar uchun video topilmadi.", reply_markup=user_main_menu())
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


#graceful shutdown
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
                bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
            except Exception as e:
                logger.exception("Polling error, retrying in 5s")
                time.sleep(5)
    else:
        logger.info("Webhook mode not configured. Set BOT_POLLING=1 to use polling.")




    
    