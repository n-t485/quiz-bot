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
    ADMIN_USERNAME = "nasir_tajudin"
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

# ========================
# 🗄️ DATABASE MANAGER
# ========================
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
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
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_id INTEGER,
                    questions TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chapter_id) REFERENCES chapters(id)
                )
            """)
            
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

    async def save_progress(self, user_id: int, chapter_id: int, progress: QuizProgress):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_progress 
                (user_id, chapter_id, current_index, score, answers, completed, last_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, chapter_id, progress.current_index, 
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
            await db.commit()

    async def set_mandatory_channel(self, channel: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO admin_settings (key, value) VALUES (?, ?)",
                ("mandatory_channel", channel)
            )
            await db.commit()

    async def get_mandatory_channel(self) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM admin_settings WHERE key = ?", 
                ("mandatory_channel",)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else Config.MANDATORY_CHANNEL

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
        await self.db.set_mandatory_channel(Config.MANDATORY_CHANNEL)

    def _register_handlers(self):
        self.bot.message_handler(commands=['start'])(self._start_handler)
        self.bot.message_handler(commands=['help'])(self._help_handler)
        self.bot.message_handler(content_types=['text'])(self._text_handler)
        self.bot.message_handler(content_types=['document'])(self._document_handler)
        self.bot.callback_query_handler(func=lambda call: True)(self._callback_handler)

    async def _check_channel_membership(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(Config.MANDATORY_CHANNEL, user_id)
            return member.status in ['member', 'administrator', 'creator']
        except:
            return False

    async def _start_handler(self, message: Message):
        user_id = message.from_user.id
        
        user = User(
            user_id=user_id,
            name=message.from_user.first_name,
            username=message.from_user.username or "NoUsername"
        )
        await self.db.save_user(user)
        
        in_channel = await self._check_channel_membership(user_id)
        await self.db.update_user_channel_status(user_id, in_channel)
        
        if not in_channel:
            await self._show_channel_requirement(message.chat.id)
            return
        
        user_data = await self.db.get_user(user_id)
        if not user_data.profile_confirmed:
            await self._ask_profile_confirmation(message.chat.id, user)
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

    async def _ask_profile_confirmation(self, chat_id: int, user: User):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Confirm My Profile", callback_data="confirm_profile"))
        
        text = f"""👤 Profile Confirmation

We've detected your Telegram profile:
• Name: {user.name}
• Username: @{user.username}

Please confirm this is correct to continue!"""
        
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

    async def _help_handler(self, message: Message):
        await self._show_help(message.chat.id)

    async def _show_help(self, chat_id: int):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{Config.ADMIN_USERNAME}"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        text = f"""💬 Help & Support

🤔 Need assistance? 
📞 Contact our admin directly: @{Config.ADMIN_USERNAME}

🎯 How to use:
1. Use buttons for navigation
2. Choose subjects and chapters
3. Answer quiz questions
4. Track your progress

🔧 Issues? Contact admin above!"""
        
        await self.bot.send_message(chat_id, text, reply_markup=markup)

    async def _text_handler(self, message: Message):
        text = message.text
        
        if text == "🎯 Take Quiz":
            await self._show_subjects(message.chat.id)
        elif text == "📊 My Profile":
            await self._show_user_profile(message.chat.id, message.from_user.id)
        elif text == "🏆 Top Scorers":
            await self._show_top_scorers(message.chat.id)
        elif text == "💬 Help & Support":
            await self._show_help(message.chat.id)
        else:
            await self.bot.send_message(message.chat.id, "🤔 Use the buttons below to navigate!", reply_markup=self._get_main_menu_markup())

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
            if description:
                btn_text += f" - {description}"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"subject_{subject_id}"))
        
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(
            chat_id, 
            "🎯 Choose a Subject:\n\nSelect a subject to see available chapters:", 
            reply_markup=markup
        )

    async def _show_chapters(self, chat_id: int, subject_id: int, subject_name: str):
        chapters = await self.db.get_chapters(subject_id)
        
        if not chapters:
            await self.bot.send_message(chat_id, f"📭 No chapters available for {subject_name}!")
            return

        markup = InlineKeyboardMarkup(row_width=2)
        for chapter_id, chapter_name in chapters:
            markup.add(InlineKeyboardButton(
                f"📖 {chapter_name}", 
                callback_data=f"chapter_{chapter_id}"
            ))
        
        markup.add(InlineKeyboardButton("🔙 Back to Subjects", callback_data="back_subjects"))
        
        await self.bot.send_message(
            chat_id,
            f"📚 {subject_name}\n\nChoose a chapter to start quiz:",
            reply_markup=markup
        )

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
            
            await self.bot.send_message(
                chat_id,
                f"✅ You've already completed this quiz!\n🎯 Score: {progress.score}/{len(quiz)}\n\nWant to retake?",
                reply_markup=markup
            )
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
            markup.add(InlineKeyboardButton(
                f"{emoji} {option}", 
                callback_data=f"answer_{chapter_id}_{question_index}_{i}"
            ))

        if progress.last_message_id:
            try:
                await self.bot.delete_message(chat_id, progress.last_message_id)
            except:
                pass

        msg = await self.bot.send_message(
            chat_id, 
            question_text, 
            reply_markup=markup
        )
        
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
                    response_text = f"✅ Correct! 🎉\n\n💡 Explanation: {question.explanation}"
                else:
                    correct_answer = question.options[question.correct]
                    response_text = f"❌ Incorrect!\n✅ Correct Answer: {correct_answer}\n\n💡 Explanation: {question.explanation}"
                
                await self.db.save_progress(user_id, chapter_id, progress)
                await self.bot.answer_callback_query(call.id, response_text, show_alert=True)
                
                await asyncio.sleep(2)
                await self._send_question(chat_id, user_id, chapter_id, question_index + 1)
            else:
                await self.bot.answer_callback_query(call.id, "⚠️ You've already answered this question!", show_alert=True)
                
        except Exception as e:
            await self.bot.answer_callback_query(call.id, "❌ Error processing answer!", show_alert=True)

    async def _complete_quiz(self, chat_id: int, user_id: int, chapter_id: int):
        quiz = await self.db.get_quiz(chapter_id)
        progress = await self.db.get_progress(user_id, chapter_id)
        
        progress.completed = True
        await self.db.save_progress(user_id, chapter_id, progress)
        
        score = progress.score
        total = len(quiz)
        percentage = (score / total) * 100
        
        if percentage >= 90:
            message = "🎉 Outstanding! You're a genius! 🌟"
        elif percentage >= 70:
            message = "👍 Great job! You really know your stuff! 💪"
        elif percentage >= 50:
            message = "😊 Good effort! Keep learning and improving! 📚"
        else:
            message = "💪 Don't give up! Every expert was once a beginner! 🚀"
        
        completion_text = f"""🎊 Quiz Completed! 🎊

📊 Your Score: {score}/{total}
📈 Percentage: {percentage:.1f}%

{message}

🏆 Check your profile to see your updated rankings!"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📊 View Profile", callback_data="view_profile"))
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
🔗 Username: @{user.username}
🏆 Total Score: {total_score} points
📊 Weekly Rank: #{user_rank}

✅ Profile Status: Confirmed
✅ Channel Membership: Active

🎯 Keep taking quizzes to improve your rank!"""
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="view_profile"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, profile_text, reply_markup=markup)

    async def _show_top_scorers(self, chat_id: int):
        top_scorers = await self.db.get_top_scorers_weekly(limit=3)
        
        if not top_scorers:
            await self.bot.send_message(chat_id, "📭 No scores yet this week! Be the first!")
            return

        leaderboard_text = "🏆 Top Scorers This Week 🏆\n\n"
        
        medals = ["🥇", "🥈", "🥉"]
        for i, scorer in enumerate(top_scorers):
            if i < 3:
                medal = medals[i]
                leaderboard_text += f"{medal} {scorer['name']} (@{scorer['username']})\n   💎 Score: {scorer['total_score']} points\n\n"
        
        leaderboard_text += "💪 Take quizzes to climb the leaderboard!"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔄 Refresh", callback_data="top_scorers"))
        markup.add(InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"))
        
        await self.bot.send_message(chat_id, leaderboard_text, reply_markup=markup)

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
            
            self.user_states[message.from_user.id] = {
                'quiz_data': quiz_data,
                'waiting_for': 'subject'
            }
            
            await self.bot.send_message(
                message.chat.id,
                "📥 Quiz file received!\n\n📝 Now send me the subject name:",
            )
            
        except Exception as e:
            await self.bot.send_message(
                message.chat.id, 
                f"❌ Error processing file: {str(e)}"
            )

    async def _callback_handler(self, call: CallbackQuery):
        try:
            data = call.data
            
            if data == "check_channel":
                user_id = call.from_user.id
                in_channel = await self._check_channel_membership(user_id)
                await self.db.update_user_channel_status(user_id, in_channel)
                
                if in_channel:
                    user = await self.db.get_user(user_id)
                    await self._ask_profile_confirmation(call.message.chat.id, user)
                else:
                    await self.bot.answer_callback_query(call.id, "❌ Please join the channel first!", show_alert=True)
                    
            elif data == "confirm_profile":
                user_id = call.from_user.id
                await self.db.confirm_user_profile(user_id)
                await self.bot.answer_callback_query(call.id, "✅ Profile confirmed!", show_alert=True)
                await self._show_main_menu(call.message.chat.id)
                
            elif data == "main_menu":
                await self._show_main_menu(call.message.chat.id)
                
            elif data == "back_subjects":
                await self._show_subjects(call.message.chat.id)
                
            elif data.startswith("subject_"):
                subject_id = int(data.split("_")[1])
                subjects = await self.db.get_subjects()
                subject_name = next((name for id, name, desc in subjects if id == subject_id), "Unknown")
                await self._show_chapters(call.message.chat.id, subject_id, subject_name)
                
            elif data.startswith("chapter_"):
                chapter_id = int(data.split("_")[1])
                await self._start_quiz(call.message.chat.id, call.from_user.id, chapter_id)
                
            elif data.startswith("answer_"):
                await self._handle_answer(call)
                
            elif data == "view_profile":
                await self._show_user_profile(call.message.chat.id, call.from_user.id)
                
            elif data == "top_scorers":
                await self._show_top_scorers(call.message.chat.id)
                
            elif data.startswith("retake_"):
                chapter_id = int(data.split("_")[1])
                progress = await self.db.get_progress(call.from_user.id, chapter_id)
                progress.current_index = 0
                progress.score = 0
                progress.answers = []
                progress.completed = False
                await self.db.save_progress(call.from_user.id, chapter_id, progress)
                await self._start_quiz(call.message.chat.id, call.from_user.id, chapter_id)
                
        except Exception as e:
            await self.bot.answer_callback_query(call.id, "❌ An error occurred!", show_alert=True)

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
