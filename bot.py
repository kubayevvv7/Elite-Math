import telebot
import os
import random
import sqlite3
from datetime import datetime
from telebot import types
from dotenv import load_dotenv

# .envdan token 
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# ID
ADMIN_IDS = [7926224444, 1229135388]

# SQ
DB_FILE = "data.db"
user_state = {}

# DB

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            test_name TEXT,
            correct_answers TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT,
            username TEXT,
            test_id TEXT,
            correct_count INTEGER,
            incorrect_count INTEGER,
            date TEXT,
            FOREIGN KEY (test_id) REFERENCES tests(test_id)
        )
    ''')
    conn.commit()
    conn.close()

def query_db(query, params=(), fetch=False, many=False):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    if many:
        cur.executemany(query, params)
    else:
        cur.execute(query, params)
    result = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return result

# func

def generate_test_id():
    prefix = random.choice("TABCDEF")
    digits = ''.join(random.choices("0123456789", k=4))
    return prefix + digits

def extract_answers(text):
    return [ch.lower() for ch in text if ch.lower() in ['a', 'b', 'c', 'd', 'e']]

def admin_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Test qo'shish", "📊 Natijalarni ko'rish")
    markup.add("🗑 Testni o'chirish")
    return markup

def back_button():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Orqaga")
    return markup

def generate_tests_menu():
    tests = query_db("SELECT test_id, test_name FROM tests ORDER BY created_at DESC", fetch=True)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in tests:
        markup.add(f"{test_name} ({test_id})")
    markup.add("⬅️ Orqaga")
    return markup

# boshlash (/start)

@bot.message_handler(commands=['start', 'admin'])
def start(message):
    username = message.from_user.username or f"id_{message.from_user.id}"

    if message.from_user.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🧑‍💼 Salom, admin!", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "Assalomu alaykum! Ism familiyangizni kiriting:")
        user_state[message.chat.id] = {"step": "get_name", "username": username}

# admin

@bot.message_handler(func=lambda m: m.text == "➕ Test qo'shish")
def add_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "🧾 Test nomini kiriting:", reply_markup=back_button())
    user_state[message.chat.id] = {"step": "get_test_name"}

@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni ko'rish")
def show_test_list(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tests = query_db("SELECT * FROM tests", fetch=True)
    if not tests:
        bot.send_message(message.chat.id, "📭 Hozircha testlar mavjud emas.", reply_markup=admin_main_menu())
        return
    bot.send_message(message.chat.id, "📋 Testlar ro'yxati:", reply_markup=generate_tests_menu())

@bot.message_handler(func=lambda m: m.text == "⬅️ Orqaga")
def go_back(message):
    bot.send_message(message.chat.id, "🏠 Bosh menyu", reply_markup=admin_main_menu())

# admin test ochiradi

@bot.message_handler(func=lambda m: m.text == "🗑 Testni o'chirish")
def delete_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    tests = query_db("SELECT test_id, test_name FROM tests ORDER BY created_at DESC", fetch=True)
    if not tests:
        bot.send_message(message.chat.id, "📭 O'chirish uchun testlar mavjud emas.", reply_markup=admin_main_menu())
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test_id, test_name in tests:
        markup.add(f"❌ {test_name} ({test_id})")
    markup.add("⬅️ Orqaga")
    bot.send_message(message.chat.id, "🗑 O'chirmoqchi bo'lgan testni tanlang:", reply_markup=markup)
    user_state[message.chat.id] = {"step": "delete_test"}

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "delete_test")
def delete_selected_test(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        return go_back(message)

    test_id = message.text.split("(")[-1].replace(")", "")
    test = query_db("SELECT test_name FROM tests WHERE test_id = ?", (test_id,), fetch=True)
    if not test:
        bot.send_message(message.chat.id, "❌ Test topilmadi.")
        return

    query_db("DELETE FROM tests WHERE test_id = ?", (test_id,))
    query_db("DELETE FROM results WHERE test_id = ?", (test_id,))
    user_state.pop(message.chat.id, None)

    bot.send_message(message.chat.id, f"✅ Test o'chirildi!\n🆔 {test_id}", reply_markup=admin_main_menu())

# admin test qoshadi

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "get_test_name")
def get_test_name(message):
    user_state[message.chat.id]["test_name"] = message.text
    user_state[message.chat.id]["step"] = "get_correct_answers"
    bot.send_message(message.chat.id, "✅ Endi to'g'ri javoblarni kiriting (masalan: 1a2b3c...30a):")

@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "get_correct_answers")
def save_test(message):
    step_data = user_state.pop(message.chat.id)
    test_name = step_data["test_name"]
    text = message.text.strip()
    if '-' in text:
        test_id, answers = text.split('-', 1)
    else:
        test_id = generate_test_id()
        answers = text
    correct = ''.join(extract_answers(answers))
    query_db(
        "INSERT OR REPLACE INTO tests (test_id, test_name, correct_answers, created_at) VALUES (?, ?, ?, ?)",
        (test_id, test_name, correct, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    bot.send_message(message.chat.id, f"✅ Test saqlandi!\n🆔 {test_id}\n📘 {test_name}", reply_markup=admin_main_menu())



@bot.message_handler(func=lambda m: True)
def handle_message(message):
    username = message.from_user.username or f"id_{message.from_user.id}"

# admin uchun test natijalari oquvchilarniki
    if message.from_user.id in ADMIN_IDS and "(" in message.text and ")" in message.text:
        test_id = message.text.split("(")[-1].replace(")", "")
        test = query_db("SELECT test_name FROM tests WHERE test_id = ?", (test_id,), fetch=True)
        if not test:
            return
        results = query_db(
            "SELECT student_name, username, correct_count, incorrect_count, date FROM results WHERE test_id = ?",
            (test_id,), fetch=True)
        if not results:
            bot.send_message(message.chat.id, f"📭 Bu testni hali hech kim ishlamagan.\n🆔 {test_id}")
            return
        text = f"📊 <b>{test[0][0]}</b>\n🆔 {test_id}\n\n"
        for r in results:
            text += f"🧑‍🎓 {r[0]} (@{r[1]})\n✅ {r[2]} | ❌ {r[3]}\n🕓 {r[4]}\n\n"
        bot.send_message(message.chat.id, text, parse_mode="HTML")
        return

# oquvchi ism kiritadi
    if message.chat.id in user_state and user_state[message.chat.id].get("step") == "get_name":
        user_state[message.chat.id]["student_name"] = message.text.strip()
        user_state[message.chat.id]["step"] = "get_test_answers"
        bot.send_message(message.chat.id, "✅ Endi test ID va javoblaringizni yuboring (masalan: XXXXX 1a2b3c...30a):")
        return

# oquvchi javob yuboradi
    if message.chat.id in user_state and user_state[message.chat.id].get("step") == "get_test_answers":
        step_data = user_state[message.chat.id]
        student_name = step_data["student_name"]
        text = message.text.strip()
        parts = text.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ Noto'g'ri format. Masalan: XXXXX 1a2b3c...30a")
            return
        test_id, user_answers = parts[0], ''.join(parts[1:])
        test = query_db("SELECT correct_answers FROM tests WHERE test_id = ?", (test_id,), fetch=True)
        if not test:
            bot.send_message(message.chat.id, "❌ Bu test topilmadi.")
            return

        correct_list = extract_answers(test[0][0])
        user_list = extract_answers(user_answers)
        total = min(len(user_list), len(correct_list))
        correct = sum(1 for i in range(total) if user_list[i] == correct_list[i])
        incorrect = len(correct_list) - correct

        query_db(
            "INSERT INTO results (student_name, username, test_id, correct_count, incorrect_count, date) VALUES (?, ?, ?, ?, ?, ?)",
            (student_name, username, test_id, correct, incorrect, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )

        bot.send_message(message.chat.id, f"📊 Natijangiz:\n🧑‍🎓 {student_name}\n🆔 {test_id}\n✅ {correct}\n❌ {incorrect}")
        for admin in ADMIN_IDS:
            bot.send_message(admin, f"📥 {student_name} (@{username})\n🆔 {test_id}\n✅ {correct}\n❌ {incorrect}")
        user_state.pop(message.chat.id, None)



print("🤖 Bot SQLite bilan ishga tushdi...")
init_db()
bot.polling(none_stop=True)
