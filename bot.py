import asyncio
import logging
import json
import os
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from telebot.async_telebot import AsyncTeleBot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
)

# ========================
# 🎯 CONFIGURATION
# ========================
class Config:
    API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8517027491:AAEUZVzbAMjj99d4JHVUi1c-IJXAC_apPT0')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '7609512291'))
    MANDATORY_CHANNEL = "@hu_quizzes"
    DB_FILE = "quiz_bot.db"

# ========================
# 📊 DATA MODELS
# ========================
@dataclass
class User:
    user_id: int
    name: str
    username: str
    profile_confirmed: bool = False
    joined_channel: bool = False

@dataclass
class Question:
    question: str
    options: List[str]
    correct: int
    explanation: str

@dataclass
class QuizProgress:
    user_id: int
    chapter_id: int
    current_index: int
    score: int
    answers: List[int]
    last_message_id: Optional[int] = None
    completed: bool = False

@dataclass
class HelpRequest:
    user_id: int
    message: str
    admin_reply: Optional[str] = None
    created_at: str = None
    replied_at: Optional[str] = None

# ========================
# 🗄️ DATABASE MANAGER
# ========================
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT,
                    profile_confirmed BOOLEAN DEFAULT FALSE,
                    joined_channel BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Subjects and chapters
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_id INTEGER,
                    name TEXT NOT NULL,
                    FOREIGN KEY (subject_id) REFERENCES subjects(id)
                )
            """)
            
            # Quizzes
            await db.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_id INTEGER,
                    questions TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chapter_id) REFERENCES chapters(id)
                )
            """)
            
            # User progress
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    user_id INTEGER,
                    chapter_id INTEGER,
                    current_index INTEGER DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    answers TEXT DEFAULT '[]',
                    completed BOOLEAN DEFAULT FALSE,
                    last_message_id INTEGER,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    PRIMARY KEY (user_id, chapter_id)
                )
            """)
            
            # Help requests
            await db.execute("""
                CREATE TABLE IF NOT EXISTS help_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    message TEXT NOT NULL,
                    admin_reply TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    replied_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            # Admin settings
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admin_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            await db.commit()

    async def save_user(self, user: User):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO users 
                (user_id, name, username, profile_confirmed, joined_channel)
                VALUES (?, ?, ?, ?, ?)
            """, (user.user_id, user.name, user.username, user.profile_confirmed, user.joined_channel))
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[User]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT name, username, profile_confirmed, joined_channel FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return User(
                        user_id=user_id,
                        name=row[0],
                        username=row[1],
                        profile_confirmed=bool(row[2]),
                        joined_channel=bool(row[3])
                    )
                return None

    async def update_user_channel_status(self, user_id: int, joined: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET joined_channel = ? WHERE user_id = ?",
                (joined, user_id)
            )
            await db.commit()

    async def confirm_user_profile(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET profile_confirmed = TRUE WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()

    # Admin methods
    async def add_subject(self, name: str, description: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO subjects (name, description) VALUES (?, ?)",
                (name, description)
            )
            await db.commit()

    async def add_chapter(self, subject_name: str, chapter_name: str):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,)) as cursor:
                subject_row = await cursor.fetchone()
                if subject_row:
                    await db.execute(
                        "INSERT OR IGNORE INTO chapters (subject_id, name) VALUES (?, ?)",
                        (subject_row[0], chapter_name)
                    )
            await db.commit()

    async def save_quiz(self, subject_name: str, chapter_name: str, questions: List[Question]):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,)) as cursor:
                subject_row = await cursor.fetchone()
                if not subject_row:
                    return False
                
            async with db.execute(
                "SELECT id FROM chapters WHERE subject_id = ? AND name = ?", 
                (subject_row[0], chapter_name)
            ) as cursor:
                chapter_row = await cursor.fetchone()
                if not chapter_row:
                    return False

            questions_json = json.dumps([{
                'question': q.question,
                'options': q.options,
                'correct': q.correct,
                'explanation': q.explanation
            } for q in questions])

            await db.execute(
                "INSERT OR REPLACE INTO quizzes (chapter_id, questions) VALUES (?, ?)",
                (chapter_row[0], questions_json)
            )
            await db.commit()
            return True

    async def get_subjects(self) -> List[Tuple[int, str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id, name, description FROM subjects") as cursor:
                return await cursor.fetchall()

    async def get_chapters(self, subject_id: int) -> List[Tuple[int, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, name FROM chapters WHERE subject_id = ?", 
                (subject_id,)
            ) as cursor:
                return await cursor.fetchall()

    async def get_quiz(self, chapter_id: int) -> Optional[List[Question]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT questions FROM quizzes WHERE chapter_id = ?", 
                (chapter_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    return [Question(**q) for q in data]
                return None

    async def get_progress(self, user_id: int, chapter_id: int) -> QuizProgress:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT current_index, score, answers, completed, last_message_id FROM user_progress WHERE user_id = ? AND chapter_id = ?",
                (user_id, chapter_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return QuizProgress(
                        user_id=user_id,
                        chapter_id=chapter_id,
                        current_index=row[0],
                        score=row[1],
                        answers=json.loads(row[2]),
                        completed=bool(row[3]),
                        last_message_id=row[4]
                    )
                return QuizProgress(user_id=user_id, chapter_id=chapter_id, current_index=0, score=0, answers=[])

    async def save_progress(self, user_id: int, channel_id: int, progress: QuizProgress):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_progress 
                (user_id, chapter_id, current_index, score, answers, completed, last_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, channel_id, progress.current_index, 
                progress.score, json.dumps(progress.answers), 
                progress.completed, progress.last_message_id
            ))
            await db.commit()

    async def get_user_total_score(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT SUM(score) FROM user_progress WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row[0] else 0

    async def get_top_scorers_weekly(self, limit: int = 3) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            async with db.execute("""
                SELECT u.name, u.username, SUM(up.score) as total_score
                FROM user_progress up
                JOIN users u ON u.user_id = up.user_id
                WHERE up.completed_at >= ?
                GROUP BY u.user_id
                ORDER BY total_score DESC
                LIMIT ?
            """, (week_ago, limit)) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"name": row[0], "username": row[1], "total_score": row[2], "rank": idx+1}
                    for idx, row in enumerate(rows)
                ]

    async def get_all_scores(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT u.name, u.username, SUM(up.score) as total_score
                FROM user_progress up
                JOIN users u ON u.user_id = up.user_id
                GROUP BY u.user_id
                ORDER BY total_score ASC
            """) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"name": row[0], "username": row[1], "total_score": row[2], "rank": idx+1}
                    for idx, row in enumerate(rows)
                ]

    async def delete_user(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM help_requests WHERE user_id = ?", (user_id,))
            await db.commit()

    # Help request methods
    async def create_help_request(self, user_id: int, message: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO help_requests (user_id, message) VALUES (?, ?)",
                (user_id, message)
            )
            await db.commit()

    async def get_pending_help_requests(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT hr.id, u.name, u.user_id, hr.message, hr.created_at 
                FROM help_requests hr
                JOIN users u ON u.user_id = hr.user_id
                WHERE hr.admin_reply IS NULL
                ORDER BY hr.created_at DESC
            """) as cursor:
                return await cursor.fetchall()

    async def reply_to_help_request(self, request_id: int, admin_reply: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE help_requests SET admin_reply = ?, replied_at = CURRENT_TIMESTAMP WHERE id = ?",
                (admin_reply, request_id)
            )
            await db.commit()

    async def get_user_help_requests(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT message, admin_reply, created_at, replied_at 
                FROM help_requests 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (user_id,)) as cursor:
                return await cursor.fetchall()

# ========================
# 🎮 QUIZ SERVICE
# ========================
class QuizService:
    @staticmethod
    def create_progress_bar(current: int, total: int, width: int = 10) -> str:
        percentage = min(100, (current / total) * 100)
        filled = int((percentage / 100) * width)
        bar = "🟩" * filled + "⬜" * (width - filled)
        return f"{bar} {percentage:.0f}%"

    @staticmethod
    def validate_quiz_data(quiz_data: dict) -> bool:
        try:
            if not isinstance(quiz_data, list):
                return False
            for question in quiz_data:
                required_fields = ['question', 'options', 'correct', 'explanation']
                if not all(field in question for field in required_fields):
                    return False
                if not isinstance(question['options'], list) or len(question['options']) < 2:
                    return False
                if not 0 <= question['correct'] < len(question['options']):
                    return False
            return True
        except:
            return False

# ========================
# 🤖 MODERN QUIZ BOT
# ========================
class ModernQuizBot:
    def __init__(self, token: str, admin_id: int):
        self.bot = AsyncTeleBot(token)
        self.db = DatabaseManager(Config.DB_FILE)
        self.quiz_service = QuizService()
        self.admin_id = admin_id
        self.user_states = {}
        self._register_handlers()

    async def initialize(self):
        await self.db.initialize()

    def _register_handlers(self):
        self.bot.message_handler(commands=['start'])(self._start_handler)
        self.bot.message_handler(commands=['help'])(self._help_handler)
        self.bot.message_handler(commands=['admin'])(self._admin_handler)
        self.bot.message_handler(content_types=['text'])(self._text_handler)
        self.bot.message_handler(content_types=['document'])(self._document_handler)
        self.bot.callback_query_handler(func=lambda call: True)(self._callback_handler)

    async def _check_channel_membership(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(Config.MANDATORY_CHANNEL, user_id)
            return member.status in ['member', 'administrator', 'creator']
        except Exception as e:
            print(f"Channel check error: {e}")
            return False

    async def _cleanup_previous_message(self, chat_id: int, message_id: int):
        """Delete previous message to keep chat clean"""
        try:
            await self.bot.delete_message(chat_id, message_id)
        except:
            pass

    async def _start_handler(self, message: Message):
        user_id = message.from_user.id
        
        # Save user info
        user = User(
            user_id=user_id,
            name=message.from_user.first_name,
            username=message.from_user.username or "NoUsername"
        )
        await self.db.save_user(user)
        
        # Check if admin
        if user_id == self.admin_id:
            await self._show_admin_panel(message.chat.id)
            return
        
        # Check channel membership
        in_channel = await self._check_channel_membership(user_id)
        await self.db.update_user_channel_status(user_id, in_channel)
        
        if not in_channel:
            await self._show_channel_requirement(message.chat.id)
            return
        
        user_data = await self.db.get_user(user_id)
        if not user_data.profile_confirmed:
            await self._ask_profile_confirmation(message.chat.id)
            return
        
        await self._show_main_menu(message.chat.id)

    async def _show_channel_requirement(self, chat_id: int):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{Config.MANDATORY_CHANNEL[1:]}"))
        markup.add(InlineKeyboardButton("✅ I've Joined", callback_data="check_channel"))
        
        text = f"""🔒 Channel Membership Required

To access amazing quizzes, please join our official channel first!

📢 Mandatory Channel: {Config.MANDATORY_CHANNEL}

After joining, click I've Joined below!"""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _ask_profile_confirmation(self, chat_id: int):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Confirm My Profile", callback_data="confirm_profile"))
        
        text = """👤 Profile Confirmation

Please confirm your profile to continue!

Your data will be kept secure and private."""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _show_main_menu(self, chat_id: int):
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            KeyboardButton("🎯 Take Quiz"),
            KeyboardButton("📊 My Profile"),
            KeyboardButton("🏆 Top Scorers"),
            KeyboardButton("💬 Help & Support")
        )
        
        text = """✨ Welcome to HU Quizzes! ✨

🎯 Test your knowledge with interactive quizzes
📊 Track your progress and rankings
🏆 Compete with other learners
💬 Get instant support

Choose an option below:"""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _show_admin_panel(self, chat_id: int):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📥 Upload Quiz", callback_data="admin_upload"),
            InlineKeyboardButton("📊 View Scores", callback_data="admin_scores"),
            InlineKeyboardButton("👥 Manage Users", callback_data="admin_users"),
            InlineKeyboardButton("📩 Help Requests", callback_data="admin_help"),
            InlineKeyboardButton("➕ Add Subject", callback_data="admin_subject"),
            InlineKeyboardButton("📖 Add Chapter", callback_data="admin_chapter")
        )
        
        text = """👑 Admin Panel

Choose an action:"""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _help_handler(self, message: Message):
        await self._show_help_options(message.chat.id)

    async def _show_help_options(self, chat_id: int):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📝 Ask Question", callback_data="ask_question"))
        markup.add(InlineKeyboardButton("📋 My Questions", callback_data="my_questions"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        text = """💬 Help & Support

Need assistance? Choose an option below:"""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _text_handler(self, message: Message):
        user_id = message.from_user.id
        text = message.text
        
        # Handle admin commands
        if user_id == self.admin_id:
            if self.user_states.get(user_id) == 'waiting_subject':
                await self._handle_admin_subject(message)
                return
            elif self.user_states.get(user_id) == 'waiting_chapter':
                await self._handle_admin_chapter(message)
                return
            elif self.user_states.get(user_id) == 'waiting_help_reply':
                await self._handle_admin_help_reply(message)
                return
        
        # Handle user help messages
        if self.user_states.get(user_id) == 'asking_question':
            await self._handle_user_question(message)
            return
        
        # Normal text handling
        if text == "🎯 Take Quiz":
            await self._show_subjects(message.chat.id)
        elif text == "📊 My Profile":
            await self._show_user_profile(message.chat.id, user_id)
        elif text == "🏆 Top Scorers":
            await self._show_top_scorers(message.chat.id)
        elif text == "💬 Help & Support":
            await self._show_help_options(message.chat.id)
        else:
            await self.bot.send_message(message.chat.id, "🤔 Use the buttons below to navigate!", reply_markup=self._get_main_menu_markup())

    async def _handle_user_question(self, message: Message):
        user_id = message.from_user.id
        question = message.text
        
        await self.db.create_help_request(user_id, question)
        self.user_states.pop(user_id, None)
        
        # Notify admin
        user = await self.db.get_user(user_id)
        admin_text = f"🆘 New Help Request\n\nFrom: {user.name}\nUser ID: {user_id}\n\nQuestion: {question}"
        
        try:
            await self.bot.send_message(self.admin_id, admin_text)
        except:
            pass
        
        await self.bot.send_message(message.chat.id, "✅ Your question has been sent to admin! You'll receive a reply soon.", reply_markup=self._get_main_menu_markup())

    async def _handle_admin_subject(self, message: Message):
        subject_name = message.text
        self.user_states[message.from_user.id] = {'waiting_chapter': subject_name}
        await self.bot.send_message(message.chat.id, f"📝 Now send the chapter name for subject '{subject_name}':")

    async def _handle_admin_chapter(self, message: Message):
        user_id = message.from_user.id
        chapter_name = message.text
        subject_name = self.user_states[user_id]['waiting_chapter']
        
        await self.db.add_subject(subject_name)
        await self.db.add_chapter(subject_name, chapter_name)
        
        self.user_states.pop(user_id, None)
        await self.bot.send_message(message.chat.id, f"✅ Subject '{subject_name}' and chapter '{chapter_name}' added! Now upload the quiz JSON file.")

    async def _handle_admin_help_reply(self, message: Message):
        user_id = message.from_user.id
        admin_reply = message.text
        request_id = self.user_states[user_id]['help_request_id']
        
        await self.db.reply_to_help_request(request_id, admin_reply)
        
        # Get user ID from request
        async with aiosqlite.connect(Config.DB_FILE) as db:
            async with db.execute("SELECT user_id FROM help_requests WHERE id = ?", (request_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    target_user_id = row[0]
                    try:
                        await self.bot.send_message(target_user_id, f"📨 Reply from Admin:\n\n{admin_reply}")
                    except:
                        pass
        
        self.user_states.pop(user_id, None)
        await self.bot.send_message(message.chat.id, "✅ Reply sent to user!")

    async def _document_handler(self, message: Message):
        if message.from_user.id != self.admin_id:
            return

        try:
            file_info = await self.bot.get_file(message.document.file_id)
            downloaded_file = await self.bot.download_file(file_info.file_path)
            quiz_data = json.loads(downloaded_file.decode('utf-8'))
            
            if not self.quiz_service.validate_quiz_data(quiz_data):
                await self.bot.send_message(message.chat.id, "❌ Invalid quiz format!")
                return
            
            # Get the last subject/chapter from user state or ask
            user_state = self.user_states.get(message.from_user.id, {})
            if 'waiting_chapter' in user_state:
                subject_name = user_state['waiting_chapter']
                chapter_name = message.document.file_name.replace('.json', '')
                
                questions = [Question(**q) for q in quiz_data]
                success = await self.db.save_quiz(subject_name, chapter_name, questions)
                
                if success:
                    await self.bot.send_message(message.chat.id, f"✅ Quiz uploaded successfully!\nSubject: {subject_name}\nChapter: {chapter_name}\nQuestions: {len(questions)}")
                else:
                    await self.bot.send_message(message.chat.id, "❌ Failed to save quiz. Make sure subject and chapter exist.")
            else:
                await self.bot.send_message(message.chat.id, "📝 Please set up subject and chapter first using the admin panel.")
            
        except Exception as e:
            await self.bot.send_message(message.chat.id, f"❌ Error processing file: {str(e)}")

    def _get_main_menu_markup(self):
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            KeyboardButton("🎯 Take Quiz"),
            KeyboardButton("📊 My Profile"), 
            KeyboardButton("🏆 Top Scorers"),
            KeyboardButton("💬 Help & Support")
        )
        return markup

    async def _show_subjects(self, chat_id: int):
        subjects = await self.db.get_subjects()
        
        if not subjects:
            await self.bot.send_message(chat_id, "📭 No subjects available yet!")
            return

        markup = InlineKeyboardMarkup(row_width=2)
        for subject_id, name, description in subjects:
            btn_text = f"📚 {name}"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"subject_{subject_id}"))
        
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, "🎯 Choose a Subject:", reply_markup=markup)

    async def _show_chapters(self, chat_id: int, subject_id: int, subject_name: str):
        chapters = await self.db.get_chapters(subject_id)
        
        if not chapters:
            await self.bot.send_message(chat_id, f"📭 No chapters available for {subject_name}!")
            return

        markup = InlineKeyboardMarkup(row_width=2)
        for chapter_id, chapter_name in chapters:
            markup.add(InlineKeyboardButton(f"📖 {chapter_name}", callback_data=f"chapter_{chapter_id}"))
        
        markup.add(InlineKeyboardButton("🔙 Back to Subjects", callback_data="back_subjects"))
        
        await self.bot.send_message(chat_id, f"📚 {subject_name}\n\nChoose a chapter:", reply_markup=markup)

    async def _start_quiz(self, chat_id: int, user_id: int, chapter_id: int):
        quiz = await self.db.get_quiz(chapter_id)
        if not quiz:
            await self.bot.send_message(chat_id, "❌ Quiz not available!")
            return

        progress = await self.db.get_progress(user_id, chapter_id)
        
        if progress.completed:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔄 Retake Quiz", callback_data=f"retake_{chapter_id}"))
            markup.add(InlineKeyboardButton("📚 Other Chapters", callback_data="back_subjects"))
            
            await self.bot.send_message(chat_id, f"✅ You've completed this quiz!\n🎯 Score: {progress.score}/{len(quiz)}\n\nWant to retake?", reply_markup=markup)
            return

        await self._send_question(chat_id, user_id, chapter_id, 0)

    async def _send_question(self, chat_id: int, user_id: int, chapter_id: int, question_index: int):
        quiz = await self.db.get_quiz(chapter_id)
        progress = await self.db.get_progress(user_id, chapter_id)
        
        if question_index >= len(quiz):
            await self._complete_quiz(chat_id, user_id, chapter_id)
            return

        question = quiz[question_index]
        progress_bar = self.quiz_service.create_progress_bar(question_index + 1, len(quiz))
        question_text = f"""📊 Progress: {progress_bar}
🏆 Current Score: {progress.score}

❓ Question {question_index + 1}/{len(quiz)}:
{question.question}"""

        markup = InlineKeyboardMarkup(row_width=2)
        for i, option in enumerate(question.options):
            emoji = ["🅰️", "🅱️", "🇨", "🇩"][i] if i < 4 else f"{i+1}️⃣"
            markup.add(InlineKeyboardButton(f"{emoji} {option}", callback_data=f"answer_{chapter_id}_{question_index}_{i}"))

        # Cleanup previous message
        if progress.last_message_id:
            await self._cleanup_previous_message(chat_id, progress.last_message_id)

        msg = await self.bot.send_message(chat_id, question_text, reply_markup=markup)
        
        progress.current_index = question_index
        progress.last_message_id = msg.message_id
        await self.db.save_progress(user_id, chapter_id, progress)

    async def _handle_answer(self, call: CallbackQuery):
        try:
            _, chapter_id, question_index, answer_idx = call.data.split("_")
            chapter_id = int(chapter_id)
            question_index = int(question_index)
            answer_idx = int(answer_idx)
            
            user_id = call.from_user.id
            chat_id = call.message.chat.id

            quiz = await self.db.get_quiz(chapter_id)
            question = quiz[question_index]
            progress = await self.db.get_progress(user_id, chapter_id)

            if len(progress.answers) <= question_index:
                progress.answers.append(answer_idx)
                
                if answer_idx == question.correct:
                    progress.score += 1
                    response_text = f"✅ Correct! 🎉\n\n💡 {question.explanation}"
                else:
                    correct_answer = question.options[question.correct]
                    response_text = f"❌ Incorrect!\n✅ {correct_answer}\n\n💡 {question.explanation}"
                
                await self.db.save_progress(user_id, chapter_id, progress)
                await self.bot.answer_callback_query(call.id, response_text, show_alert=True)
                
                # Cleanup current question
                await self._cleanup_previous_message(chat_id, call.message.message_id)
                
                await asyncio.sleep(1)
                await self._send_question(chat_id, user_id, chapter_id, question_index + 1)
            else:
                await self.bot.answer_callback_query(call.id, "⚠️ Already answered!", show_alert=True)
                
        except Exception as e:
            await self.bot.answer_callback_query(call.id, "❌ Error!", show_alert=True)

    async def _complete_quiz(self, chat_id: int, user_id: int, chapter_id: int):
        quiz = await self.db.get_quiz(chapter_id)
        progress = await self.db.get_progress(user_id, chapter_id)
        
        progress.completed = True
        await self.db.save_progress(user_id, chapter_id, progress)
        
        score = progress.score
        total = len(quiz)
        percentage = (score / total) * 100
        
        if percentage >= 90:
            message = "🎉 Outstanding! 🌟"
        elif percentage >= 70:
            message = "👍 Great job! 💪"
        elif percentage >= 50:
            message = "😊 Good effort! 📚"
        else:
            message = "💪 Keep learning! 🚀"
        
        completion_text = f"""🎊 Quiz Completed!

📊 Score: {score}/{total}
📈 Percentage: {percentage:.1f}%

{message}"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📊 Profile", callback_data="view_profile"))
        markup.add(InlineKeyboardButton("🎯 Another Quiz", callback_data="back_subjects"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, completion_text, reply_markup=markup)

    async def _show_user_profile(self, chat_id: int, user_id: int):
        user = await self.db.get_user(user_id)
        total_score = await self.db.get_user_total_score(user_id)
        top_scorers = await self.db.get_top_scorers_weekly(limit=10)
        
        user_rank = "Not ranked"
        for scorer in top_scorers:
            if scorer['username'] == user.username or scorer['name'] == user.name:
                user_rank = scorer['rank']
                break
        
        profile_text = f"""👤 Your Profile

📛 Name: {user.name}
🏆 Total Score: {total_score} points
📊 Weekly Rank: #{user_rank}

✅ Profile Status: Confirmed
✅ Channel Membership: Active"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="view_profile"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, profile_text, reply_markup=markup)

    async def _show_top_scorers(self, chat_id: int):
        top_scorers = await self.db.get_top_scorers_weekly(limit=3)
        
        if not top_scorers:
            await self.bot.send_message(chat_id, "📭 No scores yet! Be the first!")
            return

        leaderboard_text = "🏆 Top Scorers This Week\n\n"
        
        medals = ["🥇", "🥈", "🥉"]
        for i, scorer in enumerate(top_scorers):
            if i < 3:
                medal = medals[i]
                leaderboard_text += f"{medal} {scorer['name']}\n   💎 Score: {scorer['total_score']}\n\n"
        
        leaderboard_text += "💪 Take quizzes to climb!"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="top_scorers"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, leaderboard_text, reply_markup=markup)

    async def _show_user_questions(self, chat_id: int, user_id: int):
        requests = await self.db.get_user_help_requests(user_id)
        
        if not requests:
            await self.bot.send_message(chat_id, "📭 No questions yet!")
            return

        text = "📋 Your Questions\n\n"
        for i, (question, reply, created, replied) in enumerate(requests, 1):
            text += f"❓ {question}\n"
            if reply:
                text += f"💬 Reply: {reply}\n"
            else:
                text += "⏳ Waiting for reply...\n"
            text += "\n"
        
        await self.bot.send_message(chat_id, text)

    async def _show_admin_help_requests(self, chat_id: int):
        requests = await self.db.get_pending_help_requests()
        
        if not requests:
            await self.bot.send_message(chat_id, "✅ No pending help requests!")
            return

        markup = InlineKeyboardMarkup()
        for request_id, name, user_id, message, created in requests:
            btn_text = f"🆘 {name} - {message[:20]}..."
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"admin_reply_{request_id}"))
        
        await self.bot.send_message(chat_id, "📩 Pending Help Requests:", reply_markup=markup)

    async def _callback_handler(self, call: CallbackQuery):
        try:
            data = call.data
            user_id = call.from_user.id
            chat_id = call.message.chat.id

            # Cleanup previous message
            await self._cleanup_previous_message(chat_id, call.message.message_id)

            if data == "check_channel":
                in_channel = await self._check_channel_membership(user_id)
                await self.db.update_user_channel_status(user_id, in_channel)
                
                if in_channel:
                    await self._ask_profile_confirmation(chat_id)
                else:
                    await self.bot.send_message(chat_id, "❌ Please join the channel first!")
                    
            elif data == "confirm_profile":
                await self.db.confirm_user_profile(user_id)
                await self._show_main_menu(chat_id)
                
            elif data == "main_menu":
                if user_id == self.admin_id:
                    await self._show_admin_panel(chat_id)
                else:
                    await self._show_main_menu(chat_id)
                    
            elif data == "back_subjects":
                await self._show_subjects(chat_id)
                
            elif data.startswith("subject_"):
                subject_id = int(data.split("_")[1])
                subjects = await self.db.get_subjects()
                subject_name = next((name for id, name, desc in subjects if id == subject_id), "Unknown")
                await self._show_chapters(chat_id, subject_id, subject_name)
                
            elif data.startswith("chapter_"):
                chapter_id = int(data.split("_")[1])
                await self._start_quiz(chat_id, user_id, chapter_id)
                
            elif data.startswith("answer_"):
                await self._handle_answer(call)
                
            elif data == "view_profile":
                await self._show_user_profile(chat_id, user_id)
                
            elif data == "top_scorers":
                await self._show_top_scorers(chat_id)
                
            elif data == "ask_question":
                self.user_states[user_id] = 'asking_question'
                await self.bot.send_message(chat_id, "📝 Please type your question:")
                
            elif data == "my_questions":
                await self._show_user_questions(chat_id, user_id)
                
            # Admin handlers
            elif data == "admin_upload":
                await self.bot.send_message(chat_id, "📁 Please upload a JSON quiz file.")
                
            elif data == "admin_scores":
                scores = await self.db.get_all_scores()
                text = "📊 All User Scores\n\n"
                for score in scores:
                    text += f"{score['rank']}. {score['name']} - {score['total_score']}\n"
                await self.bot.send_message(chat_id, text)
                
            elif data == "admin_users":
                await self.bot.send_message(chat_id, "👥 User management - use /admin commands")
                
            elif data == "admin_help":
                await self._show_admin_help_requests(chat_id)
                
            elif data == "admin_subject":
                self.user_states[user_id] = 'waiting_subject'
                await self.bot.send_message(chat_id, "📝 Enter subject name:")
                
            elif data == "admin_chapter":
                self.user_states[user_id] = 'waiting_subject'
                await self.bot.send_message(chat_id, "📝 Enter subject name for new chapter:")
                
            elif data.startswith("admin_reply_"):
                request_id = int(data.split("_")[2])
                self.user_states[user_id] = {'waiting_help_reply': True, 'help_request_id': request_id}
                await self.bot.send_message(chat_id, "💬 Enter your reply to this help request:")
                
            elif data.startswith("retake_"):
                chapter_id = int(data.split("_")[1])
                progress = await self.db.get_progress(user_id, chapter_id)
                progress.current_index = 0
                progress.score = 0
                progress.answers = []
                progress.completed = False
                await self.db.save_progress(user_id, chapter_id, progress)
                await self._start_quiz(chat_id, user_id, chapter_id)
                
        except Exception as e:
            await self.bot.send_message(chat_id, "❌ An error occurred!")

    async def run(self):
        await self.initialize()
        logging.info("🤖 Modern Quiz Bot is running...")
        await self.bot.polling(non_stop=True)

# ========================
# 🚀 APPLICATION ENTRY POINT
# ========================
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('quiz_bot.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    bot = ModernQuizBot(Config.API_TOKEN, Config.ADMIN_ID)
    
    try:
        await bot.run()
    except Exception as e:
        logging.error(f"❌ Bot crashed: {e}")
    finally:
        logging.info("🛑 Bot stopped")

if __name__ == "__main__":
    asyncio.run(main())
