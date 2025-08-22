import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
import uuid
import logging
import os
from messages import get_text  # Импортируем функцию для получения текста

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('bot.log'),  # Запись в файл
        logging.StreamHandler()         # Вывод в консоль
    ]
)

logger = logging.getLogger(__name__)

# Конфигурация бота
BOT_TOKEN = "7966029450:AAHX4jAOvJ9dg42GtYcxyMjy3uwc7AL7d3k"  # Замените на ваш токен
ADMIN_IDS = {8207989150}  # Множество ID администраторов
VALUTE = "RUB"  # По умолчанию валюта - RUB

# Хранение данных
user_data = {}  # Данные пользователей: {user_id: {'wallet': 'адрес', 'balance': float, 'successful_deals': int, 'lang': 'ru'}}
deals = {}  # Сделки: {deal_id: {'amount': float, 'description': str, 'seller_id': int, 'buyer_id': int}}
admin_commands = {}  # Команды админа: {user_id: 'command'}

# Подключение к базе данных
DB_NAME = 'bot_data.db'


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Создаем таблицу users, если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            wallet TEXT,
            balance REAL,
            successful_deals INTEGER,
            lang TEXT
        )
    ''')

    # Создаем таблицу admins, если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    ''')

    # Проверяем, существует ли столбец lang в таблице users
    cursor.execute("PRAGMA table_info(users)")
    columns = cursor.fetchall()
    column_names = [column[1] for column in columns]  # Получаем список имен столбцов

    if 'lang' not in column_names:
        # Добавляем столбец lang, если его нет
        cursor.execute('ALTER TABLE users ADD COLUMN lang TEXT DEFAULT "ru"')

    # Создаем таблицу deals, если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            deal_id TEXT PRIMARY KEY,
            amount REAL,
            description TEXT,
            seller_id INTEGER,
            buyer_id INTEGER
        )
    ''')

    # Добавляем первого администратора, если таблица пуста
    cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (1805496851,))

    # Загружаем администраторов из базы данных
    cursor.execute('SELECT user_id FROM admins')
    admin_ids = cursor.fetchall()
    for admin_id in admin_ids:
        ADMIN_IDS.add(admin_id[0])

    conn.commit()
    conn.close()


def load_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Загрузка данных о пользователях
    cursor.execute('SELECT * FROM users')
    rows = cursor.fetchall()
    for row in rows:
        user_id, wallet, balance, successful_deals, lang = row
        user_data[user_id] = {
            'wallet': wallet,
            'balance': balance,
            'successful_deals': successful_deals,
            'lang': lang or 'ru'  # По умолчанию язык - русский
        }

    # Загрузка данных о сделках
    cursor.execute('SELECT deal_id, amount, description, seller_id, buyer_id FROM deals')
    rows = cursor.fetchall()
    for row in rows:
        deal_id, amount, description, seller_id, buyer_id = row
        deals[deal_id] = {
            'amount': amount,
            'description': description,
            'seller_id': seller_id,
            'buyer_id': buyer_id
        }

    conn.close()


def save_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    user = user_data.get(user_id, {})
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, wallet, balance, successful_deals, lang)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, user.get('wallet', ''), user.get('balance', 0.0), user.get('successful_deals', 0), user.get('lang', 'ru')))
    conn.commit()
    conn.close()


def save_deal(deal_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    deal = deals.get(deal_id, {})
    cursor.execute('''
        INSERT OR REPLACE INTO deals (deal_id, amount, description, seller_id, buyer_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (deal_id, deal.get('amount', 0.0), deal.get('description', ''), deal.get('seller_id', None), deal.get('buyer_id', None)))
    conn.commit()
    conn.close()


def delete_deal(deal_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM deals WHERE deal_id = ?', (deal_id,))
    conn.commit()
    conn.close()


def add_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()
    ADMIN_IDS.add(user_id)


def remove_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    ADMIN_IDS.discard(user_id)


def get_admins():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM admins')
    admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    return admins


# ------------------------------
#  Бесконечный баланс для админов
# ------------------------------
# Возвращает бесконечный баланс, если user_id принадлежит админу.
# Используется в проверках при оплате сделок.

def get_user_balance(user_id):
    if user_id in ADMIN_IDS:
        return float('inf')
    return user_data.get(user_id, {}).get('balance', 0.0)


# Функция для проверки и создания записи пользователя, если её нет
def ensure_user_exists(user_id):
    if user_id not in user_data:
        user_data[user_id] = {'wallet': '', 'balance': 0.0, 'successful_deals': 0, 'lang': 'ru'}
        save_user_data(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Получаем user_id в зависимости от типа обновления
        if update.message:  # Если это сообщение
            user_id = update.message.from_user.id
            chat_id = update.message.chat_id
            args = context.args  # Получаем аргументы команды /start
        elif update.callback_query:  # Если это callback-запрос
            user_id = update.callback_query.from_user.id
            chat_id = update.callback_query.message.chat_id
            args = []
        else:
            return

        lang = user_data.get(user_id, {}).get('lang', 'ru')  # Получаем язык пользователя

        # Если передан deal_id и сделка существует
        if args and args[0] in deals:
            deal_id = args[0]
            deal = deals[deal_id]
            seller_id = deal['seller_id']
            seller_username = (await context.bot.get_chat(seller_id)).username if seller_id else "Неизвестно"

            # Добавляем покупателя в сделку
            deals[deal_id]['buyer_id'] = user_id
            save_deal(deal_id)  # Сохраняем сделку в базу данных

            # Уведомление покупателю
            await context.bot.send_message(
                chat_id,
                get_text(lang, "deal_info_message", 
                         deal_id=deal_id, 
                         seller_username=seller_username, 
                         successful_deals=user_data.get(seller_id, {}).get('successful_deals', 0), 
                         description=deal['description'], 
                         wallet=user_data.get(seller_id, {}).get('wallet', 'Не указан'), 
                         amount=deal['amount'], 
                         valute=VALUTE),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(get_text(lang, "pay_from_balance_button"), callback_data=f'pay_from_balance_{deal_id}')],
                    [InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]
                ])
            )

            # Уведомление продавцу
            buyer_username = (await context.bot.get_chat(user_id)).username if user_id else "Неизвестно"
            await context.bot.send_message(
                seller_id,
                get_text(lang, "seller_notification_message", 
                         buyer_username=buyer_username, 
                         deal_id=deal_id, 
                         successful_deals=user_data.get(seller_id, {}).get('successful_deals', 0))
            )

            return  # Завершаем выполнение функции, чтобы не показывать главное меню 

        if user_id in ADMIN_IDS:
            # Админ-панель
            keyboard = [
                [InlineKeyboardButton(get_text(lang, "admin_view_deals_button"), callback_data='admin_view_deals')],
                [InlineKeyboardButton(get_text(lang, "admin_change_balance_button"), callback_data='admin_change_balance')],
                [InlineKeyboardButton(get_text(lang, "admin_change_successful_deals_button"), callback_data='admin_change_successful_deals')],
                [InlineKeyboardButton(get_text(lang, "admin_change_valute_button"), callback_data='admin_change_valute')],
                [InlineKeyboardButton(get_text(lang, "admin_manage_admins_button"), callback_data='admin_manage_admins')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(chat_id, get_text(lang, "admin_panel_message"), reply_markup=reply_markup)
        else:
            # Обычное меню для пользователей
            keyboard = [
                [InlineKeyboardButton(get_text(lang, "add_wallet_button"), callback_data='wallet')],
                [InlineKeyboardButton(get_text(lang, "create_deal_button"), callback_data='create_deal')],
                [InlineKeyboardButton(get_text(lang, "referral_button"), callback_data='referral')],
                [InlineKeyboardButton(get_text(lang, "change_lang_button"), callback_data='change_lang')],
                [InlineKeyboardButton(get_text(lang, "support_button"), url='https://t.me/OTCELFsupBot')],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_photo(
                chat_id,
                photo="https://postimg.cc/8sHq27HV",
                caption=get_text(lang, "start_message"),
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Ошибка в функции start: {e}")
        await context.bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, попробуйте позже.")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id
        chat_id = query.message.chat_id
        lang = user_data.get(user_id, {}).get('lang', 'ru')

        # Обработка выбора языка
        if data.startswith('lang_'):
            new_lang = data.split('_')[-1]
            ensure_user_exists(user_id)
            user_data[user_id]['lang'] = new_lang
            save_user_data(user_id)  # Сохраняем изменения в базе данных
            await query.edit_message_text(get_text(new_lang, "lang_set_message"))

            # После смены языка показываем меню
            await start(update, context)  # Вызываем функцию start для отображения меню
            return  # Завершаем выполнение, чтобы не обрабатывать другие условия

        # Остальные условия обработки кнопок
        elif data == 'wallet':
            try:
                wallet = user_data.get(user_id, {}).get('wallet', None)
                if wallet:
                    await context.bot.send_message(
                        chat_id,
                        get_text(lang, "wallet_message", wallet=wallet),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                    )
                else:
                    await context.bot.send_message(
                        chat_id,
                        get_text(lang, "wallet_message", wallet="Не указан"),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                    )
                context.user_data['awaiting_wallet'] = True  # Устанавливаем флаг ожидания кошелька
            except Exception as e:
                logger.error(f"Ошибка в обработке кнопки 'wallet': {e}")
                await query.edit_message_text("Произошла ошибка. Пожалуйста, попробуйте позже.")

        elif data == 'create_deal':
            await context.bot.send_photo(
                chat_id,
                photo="https://postimg.cc/8sHq27HV",
                caption=get_text(lang, "create_deal_message", valute=VALUTE),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
            )
            context.user_data['awaiting_amount'] = True  # Устанавливаем флаг ожидания суммы

        elif data == 'referral':
            referral_link = f"https://t.me/GlffElfBot?start={user_id}"
            await context.bot.send_message(
                chat_id,
                get_text(lang, "referral_message", referral_link=referral_link, valute=VALUTE),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
            )

        elif data == 'change_lang':
            await context.bot.send_message(
                chat_id,
                get_text(lang, "change_lang_message"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(get_text(lang, "english_lang_button"), callback_data='lang_en')],
                    [InlineKeyboardButton(get_text(lang, "russian_lang_button"), callback_data='lang_ru')]
                ])
            )

        elif data == 'menu':
            # Возврат в главное меню
            await start(update, context)

        # Админ-панель
        elif data == 'admin_view_deals':
            if user_id in ADMIN_IDS:
                if not deals:
                    await context.bot.send_message(chat_id, "Нет активных сделок.")
                else:
                    deals_list = []
                    for deal_id, deal in deals.items():
                        seller_username = (await context.bot.get_chat(deal['seller_id'])).username if deal['seller_id'] else "Неизвестно"
                        buyer_username = (await context.bot.get_chat(deal['buyer_id'])).username if deal['buyer_id'] else "Неизвестно"
                        deals_list.append(
                            f"Сделка {deal_id}:\n"
                            f"Сумма: {deal['amount']} {VALUTE}\n"
                            f"Описание: {deal['description']}\n"
                            f"Продавец: @{seller_username} (ID: {deal['seller_id']})\n"
                            f"Покупатель: @{buyer_username} (ID: {deal['buyer_id']})\n"
                        )
                    await context.bot.send_message(chat_id, "Активные сделки:\n\n" + "\n".join(deals_list))

        elif data == 'admin_change_balance':
            if user_id in ADMIN_IDS:
                await query.edit_message_text(get_text(lang, "admin_change_balance_message"))
                admin_commands[user_id] = 'change_balance'

        elif data == 'admin_change_successful_deals':
            if user_id in ADMIN_IDS:
                await query.edit_message_text(get_text(lang, "admin_change_successful_deals_message"))
                admin_commands[user_id] = 'change_successful_deals'

        elif data == 'admin_change_valute':
            if user_id in ADMIN_IDS:
                await query.edit_message_text(get_text(lang, "admin_change_valute_message"))
                admin_commands[user_id] = 'change_valute'

        elif data == 'admin_manage_admins':
            if user_id in ADMIN_IDS:
                current_admins = get_admins()
                admins_list = []
                for admin_id in current_admins:
                    try:
                        username = (await context.bot.get_chat(admin_id)).username
                        admins_list.append(f"@{username} (ID: {admin_id})")
                    except:
                        admins_list.append(f"Неизвестный пользователь (ID: {admin_id})")

                keyboard = [
                    [InlineKeyboardButton(get_text(lang, "admin_add_admin_button"), callback_data='admin_add_admin')],
                    [InlineKeyboardButton(get_text(lang, "admin_remove_admin_button"), callback_data='admin_remove_admin')],
                    [InlineKeyboardButton(get_text(lang, "back_button"), callback_data='menu')]
                ]

                await query.edit_message_text(
                    get_text(lang, "admin_manage_admins_message", admins_list="\n".join(admins_list)),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        elif data == 'admin_add_admin':
            if user_id in ADMIN_IDS:
                await query.edit_message_text(get_text(lang, "admin_add_admin_message"))
                admin_commands[user_id] = 'add_admin'

        elif data == 'admin_remove_admin':
            if user_id in ADMIN_IDS:
                current_admins = get_admins()
                keyboard = []
                for admin_id in current_admins:
                    if admin_id != user_id:  # Нельзя удалить себя
                        try:
                            username = (await context.bot.get_chat(admin_id)).username
                            keyboard.append([InlineKeyboardButton(f"@{username} (ID: {admin_id})", callback_data=f'remove_admin_{admin_id}')])
                        except:
                            keyboard.append([InlineKeyboardButton(f"Неизвестный пользователь (ID: {admin_id})", callback_data=f'remove_admin_{admin_id}')])
                keyboard.append([InlineKeyboardButton(get_text(lang, "back_button"), callback_data='admin_manage_admins')])

                await query.edit_message_text(
                    get_text(lang, "admin_remove_admin_message"),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        elif data.startswith('remove_admin_'):
            if user_id in ADMIN_IDS:
                target_admin_id = int(data.split('_')[-1])
                if target_admin_id != user_id:  # Нельзя удалить себя
                    remove_admin(target_admin_id)
                    await query.edit_message_text(
                        get_text(lang, "admin_removed_message", admin_id=target_admin_id),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "back_button"), callback_data='admin_manage_admins')]])
                    )

        # Обработка оплаты с баланса (с учетом бесконечного баланса админов)
        elif data.startswith('pay_from_balance_'):
            deal_id = data.split('_')[-1]  # Извлекаем deal_id из callback_data
            deal = deals.get(deal_id)
            if deal:
                buyer_id = user_id
                seller_id = deal['seller_id']
                amount = deal['amount']

                # Проверяем и создаем записи, если их нет
                ensure_user_exists(buyer_id)
                ensure_user_exists(seller_id)

                # Используем функцию get_user_balance(), которая возвращает бесконечность для админов
                if get_user_balance(buyer_id) >= amount:
                    # Списание средств у покупателя (если он не админ)
                    if buyer_id not in ADMIN_IDS:
                        user_data[buyer_id]['balance'] -= amount
                        save_user_data(buyer_id)  # Сохраняем изменения в базе данных

                    # Зачисление средств продавцу
                    user_data[seller_id]['balance'] += amount
                    save_user_data(seller_id)  # Сохраняем изменения в базе данных

                    # Уведомление покупателю
                    await context.bot.send_message(
                        chat_id,
                        get_text(lang, "payment_confirmed_message", deal_id=deal_id, amount=amount, valute=VALUTE, description=deal['description']),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                    )

                    # Возврат покупателя в главное меню
                    await start(update, context)

                    # Уведомление продавцу
                    buyer_username = (await context.bot.get_chat(buyer_id)).username if buyer_id else "Неизвестно"
                    await context.bot.send_message(
                        seller_id,
                        get_text(lang, "payment_confirmed_seller_message", 
                                 deal_id=deal_id, 
                                 description=deal['description'], 
                                 buyer_username=buyer_username)
                    )

                    # Увеличение количества успешных сделок у продавца
                    user_data[seller_id]['successful_deals'] += 1
                    save_user_data(seller_id)  # Сохраняем изменения в базе данных

                    # Удаление сделки из списка активных
                    del deals[deal_id]
                    delete_deal(deal_id)  # Удаляем сделку из базы данных
                else:
                    await context.bot.send_message(
                        chat_id,
                        get_text(lang, "insufficient_balance_message"),
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                    )

    except Exception as e:
        logger.error(f"Ошибка в функции button: {e}")
        await context.bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, попробуйте позже.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global VALUTE  
        user_id = update.message.from_user.id
        text = update.message.text
        lang = user_data.get(user_id, {}).get('lang', 'ru')

        if user_id in ADMIN_IDS and admin_commands.get(user_id) == 'change_balance':
            try:
                target_user_id, new_balance = map(str.strip, text.split())
                target_user_id = int(target_user_id)
                new_balance = float(new_balance)
                ensure_user_exists(target_user_id)
                user_data[target_user_id]['balance'] = new_balance
                save_user_data(target_user_id)  # Сохраняем изменения в базе данных
                await update.message.reply_text(f"Баланс пользователя {target_user_id} изменен на {new_balance} {VALUTE}.")
            except ValueError:
                await update.message.reply_text("Неверный формат. Введите ID пользователя и баланс через пробел.")
            admin_commands[user_id] = None

        elif user_id in ADMIN_IDS and admin_commands.get(user_id) == 'change_successful_deals':
            try:
                target_user_id, new_successful_deals = map(str.strip, text.split())
                target_user_id = int(target_user_id)
                new_successful_deals = int(new_successful_deals)
                ensure_user_exists(target_user_id)
                user_data[target_user_id]['successful_deals'] = new_successful_deals
                save_user_data(target_user_id)  # Сохраняем изменения в базе данных
                await update.message.reply_text(f"Количество успешных сделок пользователя {target_user_id} изменено на {new_successful_deals}.")
            except ValueError:
                await update.message.reply_text("Неверный формат. Введите ID пользователя и количество успешных сделок через пробел.")
            admin_commands[user_id] = None

        elif user_id in ADMIN_IDS and admin_commands.get(user_id) == 'change_valute':
            VALUTE = text.strip().upper()  
            await update.message.reply_text(f"Валюта изменена на {VALUTE}.")
            admin_commands[user_id] = None

        elif user_id in ADMIN_IDS and admin_commands.get(user_id) == 'add_admin':
            try:
                new_admin_id = int(text.strip())
                add_admin(new_admin_id)
                try:
                    username = (await context.bot.get_chat(new_admin_id)).username
                    await update.message.reply_text(f"Пользователь @{username} (ID: {new_admin_id}) добавлен в администраторы.")
                except:
                    await update.message.reply_text(f"Пользователь (ID: {new_admin_id}) добавлен в администраторы.")
                admin_commands[user_id] = None
            except ValueError:
                await update.message.reply_text("Неверный формат. Введите ID пользователя.")
            except Exception as e:
                await update.message.reply_text(f"Ошибка: {e}")

        elif context.user_data.get('awaiting_amount', False):
            try:
                context.user_data['amount'] = float(text)
                context.user_data['awaiting_amount'] = False
                context.user_data['awaiting_description'] = True
                await update.message.reply_text(
                    get_text(lang, "awaiting_description_message"),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                )
            except ValueError:
                await update.message.reply_text("Неверный формат. Введите число.")

        elif context.user_data.get('awaiting_description', False):
            deal_id = str(uuid.uuid4())
            deals[deal_id] = {
                'amount': context.user_data['amount'],
                'description': text,
                'seller_id': user_id,
                'buyer_id': None
            }
            save_deal(deal_id)  # Сохраняем сделку в базу данных
            context.user_data.clear()

            await update.message.reply_text(
                get_text(lang, "deal_created_message", amount=deals[deal_id]['amount'], valute=VALUTE, description=deals[deal_id]['description'], deal_link=f"https://t.me/GlffElfBot?start={deal_id}"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
            )
            # Уведомление всем администраторам
            seller_username = (await context.bot.get_chat(user_id)).username if user_id else "Неизвестно"
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"Новая сделка создана:\n"
                        f"ID: {deal_id}\n"
                        f"Сумма: {deals[deal_id]['amount']} {VALUTE}\n"
                        f"Описание: {deals[deal_id]['description']}\n"
                        f"Продавец: @{seller_username} (ID: {user_id})"
                    )
                except:
                    continue

        elif context.user_data.get('awaiting_wallet', False):
            try:
                ensure_user_exists(user_id)  # Убедимся, что запись пользователя существует
                user_data[user_id]['wallet'] = text  # Обновляем кошелек
                save_user_data(user_id)  # Сохраняем изменения в базе данных
                context.user_data.pop('awaiting_wallet', None)  # Очищаем флаг ожидания
                await update.message.reply_text(
                    get_text(lang, "wallet_updated_message", wallet=text),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text(lang, "menu_button"), callback_data='menu')]])
                )
            except Exception as e:
                logger.error(f"Ошибка при обновлении кошелька: {e}")
                await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")

    except Exception as e:
        logger.error(f"Ошибка в функции handle_message: {e}")
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")


# Запуск бота
def main() -> None:
    init_db()  # Инициализация базы данных
    load_data()  # Загрузка данных из базы данных

    application = Application.builder().token(BOT_TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    application.run_polling()


if __name__ == "__main__":
    main()
