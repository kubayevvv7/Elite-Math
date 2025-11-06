import telebot
import json
import os
import random
from datetime import datetime
from telebot import types
from dotenv import load_dotenv

load_dotenv()  # .env faylni yuklaydi

TOKEN = os.getenv("BOT_TOKEN")  # tokenni .env dan oladi
bot = telebot.TeleBot(TOKEN)

# 🔐 Admin ID-lari
ADMIN_IDS = [7926224444]

DATA_FILE = "data.json"
user_state = {}

# 📂 JSON bilan ishlash
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"tests": [], "results": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 🆔 Tasodifiy test ID yaratish
def generate_test_id():
    prefix = random.choice("TABCDEF")
    digits = ''.join(random.choices("0123456789", k=4))
    return prefix + digits

# Harflarni ajratish
def extract_answers(text):
    return [ch.lower() for ch in text if ch.lower() in ['a', 'b', 'c', 'd', 'e']]

# --- Reply Keyboard Menyular ---
def admin_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Test qo‘shish", "📊 Natijalarni ko‘rish")
    markup.add("🗑 Testni o‘chirish")
    return markup

def back_button():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Orqaga")
    return markup

def generate_tests_menu():
    data = load_data()
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test in sorted(data["tests"], key=lambda x: x["created_at"], reverse=True):
        markup.add(f"{test['test_name']} ({test['test_id']})")
    markup.add("⬅️ Orqaga")
    return markup


# --- START / ADMIN ---
@bot.message_handler(commands=['start', 'admin'])
def start(message):
    username = message.from_user.username or f"id_{message.from_user.id}"

    if message.from_user.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "🧑‍💼 Salom, admin!", reply_markup=admin_main_menu())
    else:
        bot.send_message(message.chat.id, "Assalomu alaykum! Ism familiyangizni kiriting:")
        user_state[message.chat.id] = {"step": "get_name", "username": username}


# --- ADMIN MENYULAR ---
@bot.message_handler(func=lambda m: m.text == "➕ Test qo‘shish")
def add_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(message.chat.id, "🧾 Test nomini kiriting:", reply_markup=back_button())
    user_state[message.chat.id] = {"step": "get_test_name"}


@bot.message_handler(func=lambda m: m.text == "📊 Natijalarni ko‘rish")
def show_test_list(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = load_data()
    if not data["tests"]:
        bot.send_message(message.chat.id, "📭 Hozircha testlar mavjud emas.", reply_markup=admin_main_menu())
        return
    bot.send_message(message.chat.id, "📋 Testlar ro‘yxati:", reply_markup=generate_tests_menu())


@bot.message_handler(func=lambda m: m.text == "⬅️ Orqaga")
def go_back(message):
    bot.send_message(message.chat.id, "🏠 Bosh menyu", reply_markup=admin_main_menu())


# --- ✅ TEST O‘CHIRISH FUNKSIYASI ---
@bot.message_handler(func=lambda m: m.text == "🗑 Testni o‘chirish")
def delete_test_start(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = load_data()
    if not data["tests"]:
        bot.send_message(message.chat.id, "📭 O‘chirish uchun testlar mavjud emas.", reply_markup=admin_main_menu())
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for test in sorted(data["tests"], key=lambda x: x["created_at"], reverse=True):
        markup.add(f"❌ {test['test_name']} ({test['test_id']})")
    markup.add("⬅️ Orqaga")
    bot.send_message(message.chat.id, "🗑 O‘chirmoqchi bo‘lgan testni tanlang:", reply_markup=markup)
    user_state[message.chat.id] = {"step": "delete_test"}


@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "delete_test")
def delete_selected_test(message):
    if message.text == "⬅️ Orqaga":
        user_state.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "🏠 Bosh menyu", reply_markup=admin_main_menu())
        return

    data = load_data()
    # Tanlangan testni aniqlash
    test_id = None
    for test in data["tests"]:
        if message.text.endswith(f"({test['test_id']})") or test["test_id"] in message.text:
            test_id = test["test_id"]
            break

    if not test_id:
        bot.send_message(message.chat.id, "❌ Test topilmadi, qayta urinib ko‘ring.")
        return

    # O‘chirish tasdig‘i
    test = next((t for t in data["tests"] if t["test_id"] == test_id), None)
    if not test:
        bot.send_message(message.chat.id, "❌ Test topilmadi.")
        return

    data["tests"] = [t for t in data["tests"] if t["test_id"] != test_id]
    data["results"] = [r for r in data["results"] if r["test_id"] != test_id]
    save_data(data)

    user_state.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        f"✅ Test o‘chirildi!\n🆔 {test_id}\n📘 {test['test_name']}",
        reply_markup=admin_main_menu()
    )


# --- TEST QO‘SHISH ---
@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "get_test_name")
def get_test_name(message):
    user_state[message.chat.id]["test_name"] = message.text
    user_state[message.chat.id]["step"] = "get_correct_answers"
    bot.send_message(message.chat.id, "✅ Endi to‘g‘ri javoblarni kiriting (masalan: B4086-1a2b3c...):")


@bot.message_handler(func=lambda m: m.chat.id in user_state and user_state[m.chat.id]["step"] == "get_correct_answers")
def save_test(message):
    data = load_data()
    step_data = user_state.pop(message.chat.id)
    test_name = step_data["test_name"]
    text = message.text.strip()

    if '-' in text:
        test_id, answers = text.split('-', 1)
    else:
        test_id = generate_test_id()
        answers = text

    correct = ''.join(extract_answers(answers))
    new_test = {
        "test_id": test_id,
        "test_name": test_name,
        "correct_answers": correct,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    data["tests"] = [t for t in data["tests"] if t["test_id"] != test_id]
    data["tests"].append(new_test)
    save_data(data)

    bot.send_message(
        message.chat.id,
        f"✅ Test saqlandi!\n🆔 {test_id}\n📘 {test_name}",
        reply_markup=admin_main_menu()
    )


# --- TESTNI TANLAGANDA / O‘QUVCHI QISMI ---
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    data = load_data()

    # 🧑‍💼 ADMIN TEST KO‘RISH
    if message.from_user.id in ADMIN_IDS:
        for test in data["tests"]:
            display_text = f"{test['test_name']} ({test['test_id']})"
            if message.text.strip() == display_text:
                test_id = test['test_id']
                results = [r for r in data["results"] if r["test_id"] == test_id]

                if not results:
                    bot.send_message(
                        message.chat.id,
                        f"📭 Bu testni hali hech kim ishlamagan.\n🆔 {test_id} ({test['test_name']})",
                        reply_markup=generate_tests_menu()
                    )
                    return

                text = f"📊 <b>{test['test_name']}</b>\n🆔 {test_id}\n\n"
                for r in results:
                    text += (
                        f"🧑‍🎓 {r['student_name']} (@{r['username']})\n"
                        f"✅ {r['correct_count']} | ❌ {r['incorrect_count']}\n"
                        f"🕓 {r['date']}\n\n"
                    )
                bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=generate_tests_menu())
                return

    # 🧾 O‘QUVCHI ISM
    if message.chat.id in user_state and user_state[message.chat.id].get("step") == "get_name":
        user_state[message.chat.id]["student_name"] = message.text.strip()
        user_state[message.chat.id]["step"] = "get_test_answers"
        bot.send_message(
            message.chat.id,
            "✅ Endi test ID va javoblaringizni yuboring (masalan: B4086 1a2b3c...):"
        )
        return

    # 🧑‍🎓 TEST YECHISH
    if message.chat.id in user_state and user_state[message.chat.id].get("step") == "get_test_answers":
        step_data = user_state[message.chat.id]
        student_name = step_data["student_name"]
        username = step_data["username"]
        text = message.text.strip()

        # Formatni tekshirish
        parts = text.replace("\n", " ").split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ Noto‘g‘ri format. Masalan: XXXXX 1a2b3c...")
            return

        test_id, user_answers = parts[0], ''.join(parts[1:])
        test = next((t for t in data["tests"] if t["test_id"] == test_id), None)
        if not test:
            bot.send_message(message.chat.id, "❌ Bu test topilmadi.")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        for r in data["results"]:
            if (
                r.get("username") == username and
                r["test_id"] == test_id and
                r["date"].startswith(today)
            ):
                bot.send_message(message.chat.id, "⚠️ Siz bu testni bugun allaqachon topshirgansiz.")
                return

        correct_list = extract_answers(test["correct_answers"])
        user_list = extract_answers(user_answers)
        total = min(len(user_list), len(correct_list))
        correct = sum(1 for i in range(total) if user_list[i] == correct_list[i])
        incorrect = len(correct_list) - correct

        result = {
            "student_name": student_name,
            "username": username,
            "test_id": test_id,
            "correct_count": correct,
            "incorrect_count": incorrect,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data["results"].append(result)
        save_data(data)

        bot.send_message(
            message.chat.id,
            f"📊 Natijangiz:\n🧑‍🎓 {student_name} (@{username})\n🆔 {test_id}\n✅ {correct}\n❌ {incorrect}"
        )

        # 🔔 Adminlarga yuborish
        for admin in ADMIN_IDS:
            bot.send_message(admin, f"📥 {student_name} (@{username})\n🆔 {test_id}\n✅ {correct}\n❌ {incorrect}")

        user_state.pop(message.chat.id, None)


print("🤖 JSON versiya bot ishga tushdi (test o‘chirish funksiyasi bilan)...")
bot.polling(none_stop=True)
