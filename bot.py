import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import aiosqlite
from telebot.async_telebot import AsyncTeleBot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
)
import json
import os
from pathlib import Path
from datetime import datetime

# ========================
# 🎯 MODERN CONFIGURATION
# ========================
class Config:
    API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8517027491:AAEUZVzbAMjj99d4JHVUi1c-IJXAC_apPT0')
    ADMIN_ID = int(os.getenv('ADMIN_ID', '7609512291'))
    DB_FILE = Path("data/quiz_bot.db")
    LOG_LEVEL = logging.INFO

# ========================
# 📊 MODERN DATA MODELS
# ========================
@dataclass
class User:
    user_id: int
    name: str
    username: str

@dataclass
class Question:
    question: str
    options: List[str]
    correct: int

@dataclass
class QuizProgress:
    user_id: int
    subject: str
    current_index: int
    score: int
    last_message_id: Optional[int] = None

@dataclass
class UserScore:
    name: str
    username: str
    total_score: int
    rank: int

# ========================
# 🗄️ MODERN DATABASE LAYER
# ========================
class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Initialize database with proper schema"""
        async with aiosqlite.connect(self.db_path) as db:
            # Users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Subjects table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    name TEXT PRIMARY KEY,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Quizzes table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS quizzes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    questions TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES subjects(name)
                )
            """)
            
            # Progress table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS progress (
                    user_id INTEGER,
                    subject TEXT,
                    current_index INTEGER DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    last_message_id INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, subject),
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (subject) REFERENCES subjects(name)
                )
            """)
            
            await db.commit()

    async def save_user(self, user: User) -> None:
        """Save or update user information"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, name, username) VALUES (?, ?, ?)",
                (user.user_id, user.name, user.username)
            )
            await db.commit()

    async def save_subject(self, subject_name: str) -> None:
        """Save or update subject"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO subjects (name) VALUES (?)",
                (subject_name,)
            )
            await db.commit()

    async def save_quiz(self, subject: str, questions: List[Question]) -> None:
        """Save quiz data"""
        questions_json = json.dumps([{
            'question': q.question,
            'options': q.options,
            'correct': q.correct
        } for q in questions])
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO quizzes (subject, questions) VALUES (?, ?)",
                (subject, questions_json)
            )
            await db.commit()

    async def get_subjects(self) -> List[str]:
        """Get all available subjects"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT name FROM subjects") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def get_quiz(self, subject: str) -> Optional[List[Question]]:
        """Get quiz questions for a subject"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT questions FROM quizzes WHERE subject = ?",
                (subject,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    data = json.loads(row[0])
                    return [Question(**q) for q in data]
                return None

    async def get_progress(self, user_id: int, subject: str) -> QuizProgress:
        """Get user progress for a subject"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT current_index, score, last_message_id FROM progress WHERE user_id = ? AND subject = ?",
                (user_id, subject)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return QuizProgress(
                        user_id=user_id,
                        subject=subject,
                        current_index=row[0],
                        score=row[1],
                        last_message_id=row[2]
                    )
                return QuizProgress(user_id=user_id, subject=subject, current_index=0, score=0)

    async def save_progress(self, progress: QuizProgress) -> None:
        """Save user progress"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO progress 
                (user_id, subject, current_index, score, last_message_id, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (progress.user_id, progress.subject, progress.current_index, 
                  progress.score, progress.last_message_id))
            await db.commit()

    async def get_user_total_score(self, user_id: int) -> int:
        """Get user's total score across all subjects"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT SUM(score) FROM progress WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row[0] else 0

    async def get_top_scorers(self, limit: int = None) -> List[UserScore]:
        """Get top scorers with ranking"""
        async with aiosqlite.connect(self.db_path) as db:
            query = """
                SELECT u.name, u.username, SUM(p.score) as total_score
                FROM progress p
                JOIN users u ON u.user_id = p.user_id
                GROUP BY u.user_id
                ORDER BY total_score DESC
            """
            if limit:
                query += f" LIMIT {limit}"
                
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [
                    UserScore(name=row[0], username=row[1], total_score=row[2], rank=idx+1)
                    for idx, row in enumerate(rows)
                ]

    async def get_all_scores(self) -> List[UserScore]:
        """Get all user scores (admin only)"""
        return await self.get_top_scorers(limit=None)

# ========================
# 🎮 QUIZ SERVICE
# ========================
class QuizService:
    @staticmethod
    def create_progress_bar(current: int, total: int) -> str:
        """Create a beautiful progress bar"""
        percentage = (current / total) * 100
        filled = int((current / total) * 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)
        return f"{bar} {percentage:.0f}%"

    @staticmethod
    def create_question_text(question: Question, current: int, total: int) -> str:
        """Format question text with progress"""
        progress_bar = QuizService.create_progress_bar(current, total)
        return f"📊 **Progress:** {progress_bar}\n\n❓ **Question {current}/{total}:**\n{question.question}"

    @staticmethod
    def validate_quiz_data(quiz_data: dict) -> bool:
        """Validate quiz JSON structure"""
        try:
            if not isinstance(quiz_data, list):
                return False
            for question in quiz_data:
                if not all(key in question for key in ['question', 'options', 'correct']):
                    return False
                if not isinstance(question['options'], list) or len(question['options']) < 2:
                    return False
                if not 0 <= question['correct'] < len(question['options']):
                    return False
            return True
        except:
            return False

# ========================
# 🤖 MODERN BOT
# ========================
class ModernQuizBot:
    def __init__(self, token: str, admin_id: int):
        self.bot = AsyncTeleBot(token)
        self.db = DatabaseManager(Config.DB_FILE)
        self.quiz_service = QuizService()
        self.admin_id = admin_id
        self._register_handlers()

    async def initialize(self):
        """Initialize database"""
        await self.db.initialize()

    def _register_handlers(self):
        """Register all bot handlers"""
        self.bot.message_handler(commands=['start'])(self._start_handler)
        self.bot.message_handler(commands=['quiz'])(self._quiz_handler)
        self.bot.message_handler(commands=['myscore'])(self._myscore_handler)
        self.bot.message_handler(commands=['topscorer'])(self._topscorer_handler)
        self.bot.message_handler(commands=['admin'])(self._admin_handler)
        self.bot.message_handler(content_types=['document'])(self._document_handler)
        self.bot.callback_query_handler(func=lambda call: True)(self._callback_handler)

    async def _start_handler(self, message: Message):
        """Handle /start command with main menu"""
        user = User(
            user_id=message.from_user.id,
            name=message.from_user.first_name,
            username=message.from_user.username or "NoUsername"
        )
        await self.db.save_user(user)
        
        # Create main menu keyboard
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            KeyboardButton("🎯 Take Quiz"),
            KeyboardButton("📊 My Score"),
            KeyboardButton("🏆 Top Scorers"),
            KeyboardButton("ℹ️ Help")
        )
        if message.from_user.id == self.admin_id:
            markup.add(KeyboardButton("👑 Admin Panel"))
        
        welcome_text = """
✨ **Welcome to HU Quizzes Bot!** ✨

🧠 *Test your knowledge across various subjects!*
📈 *Track your progress and compete with others!*
🏆 *Climb the leaderboard!*

*Use the buttons below to navigate:*
        """
        await self.bot.send_message(
            message.chat.id, 
            welcome_text, 
            reply_markup=markup,
            parse_mode='Markdown'
        )

    async def _quiz_handler(self, message: Message):
        """Handle quiz selection"""
        await self._show_subjects(message.chat.id)

    async def _show_subjects(self, chat_id: int):
        """Show available subjects"""
        subjects = await self.db.get_subjects()
        if not subjects:
            await self.bot.send_message(chat_id, "📭 *No quizzes available yet!*", parse_mode='Markdown')
            return

        markup = InlineKeyboardMarkup(row_width=2)
        for subject in subjects:
            markup.add(InlineKeyboardButton(
                f"📚 {subject}", 
                callback_data=f"subject|{subject}"
            ))
        
        await self.bot.send_message(
            chat_id, 
            "🎯 *Choose a subject to start quiz:*", 
            reply_markup=markup,
            parse_mode='Markdown'
        )

    async def _myscore_handler(self, message: Message):
        """Handle my score command"""
        total_score = await self.db.get_user_total_score(message.from_user.id)
        top_scorers = await self.db.get_top_scorers(limit=3)
        
        user_rank = "Not ranked"
        for scorer in top_scorers:
            if scorer.username == message.from_user.username or scorer.name == message.from_user.first_name:
                user_rank = scorer.rank
                break
        
        score_text = f"""
📊 **Your Statistics:**

🏅 **Total Score:** {total_score} points
📈 **Global Rank:** #{user_rank}

🎯 *Keep going to climb the leaderboard!*
        """
        await self.bot.send_message(message.chat.id, score_text, parse_mode='Markdown')

    async def _topscorer_handler(self, message: Message):
        """Show top 3 scorers to users"""
        top_scorers = await self.db.get_top_scorers(limit=3)
        
        if not top_scorers:
            await self.bot.send_message(message.chat.id, "📭 *No scores yet! Be the first to take a quiz!*", parse_mode='Markdown')
            return

        leaderboard_text = "🏆 **Top Scorers Leaderboard** 🏆\n\n"
        
        medals = ["🥇", "🥈", "🥉"]
        for i, scorer in enumerate(top_scorers):
            if i < 3:
                medal = medals[i]
                leaderboard_text += f"{medal} **{scorer.name}** (@{scorer.username})\n   📊 **Score:** {scorer.total_score} points\n\n"
        
        leaderboard_text += "💪 *Take quizzes to climb the leaderboard!*"
        await self.bot.send_message(message.chat.id, leaderboard_text, parse_mode='Markdown')

    async def _admin_handler(self, message: Message):
        """Handle admin panel"""
        if message.from_user.id != self.admin_id:
            await self.bot.send_message(message.chat.id, "🚫 *Access Denied!*", parse_mode='Markdown')
            return

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📥 Add Quiz", callback_data="admin_add_quiz"),
            InlineKeyboardButton("📊 View All Scores", callback_data="admin_all_scores"),
            InlineKeyboardButton("👥 User Stats", callback_data="admin_user_stats"),
            InlineKeyboardButton("🆕 Add Subject", callback_data="admin_add_subject")
        )
        
        await self.bot.send_message(
            message.chat.id, 
            "👑 **Admin Panel**\n\n*Choose an action:*", 
            reply_markup=markup,
            parse_mode='Markdown'
        )

    async def _document_handler(self, message: Message):
        """Handle document upload for quiz JSON"""
        if message.from_user.id != self.admin_id:
            return

        try:
            file_info = await self.bot.get_file(message.document.file_id)
            downloaded_file = await self.bot.download_file(file_info.file_path)
            quiz_data = json.loads(downloaded_file.decode('utf-8'))
            
            if not self.quiz_service.validate_quiz_data(quiz_data):
                await self.bot.send_message(message.chat.id, "❌ *Invalid quiz format!*", parse_mode='Markdown')
                return
            
            # Extract subject from filename or ask
            subject = message.document.file_name.replace('.json', '')
            questions = [Question(**q) for q in quiz_data]
            
            await self.db.save_subject(subject)
            await self.db.save_quiz(subject, questions)
            
            success_text = f"""
✅ **Quiz Added Successfully!**

📚 **Subject:** {subject}
❓ **Questions:** {len(questions)}
📅 **Added on:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

*Users can now take this quiz!*
            """
            await self.bot.send_message(message.chat.id, success_text, parse_mode='Markdown')
            
        except Exception as e:
            await self.bot.send_message(
                message.chat.id, 
                f"❌ *Error processing quiz file:* `{str(e)}`", 
                parse_mode='Markdown'
            )

    async def _send_question(self, user_id: int, subject: str):
        """Send next question to user"""
        quiz = await self.db.get_quiz(subject)
        if not quiz:
            await self.bot.send_message(user_id, "❌ *Quiz not found!*", parse_mode='Markdown')
            return

        progress = await self.db.get_progress(user_id, subject)
        
        # Check if quiz completed
        if progress.current_index >= len(quiz):
            completion_text = f"""
🎉 **Quiz Completed!** 🎉

📚 **Subject:** {subject}
✅ **Score:** {progress.score}/{len(quiz)}
📊 **Percentage:** {(progress.score/len(quiz))*100:.1f}%

🏆 *Great job! Try another subject!*
            """
            await self.bot.send_message(user_id, completion_text, parse_mode='Markdown')
            return

        question = quiz[progress.current_index]
        question_text = self.quiz_service.create_question_text(
            question, progress.current_index + 1, len(quiz)
        )

        # Create answer buttons
        markup = InlineKeyboardMarkup(row_width=2)
        for i, option in enumerate(question.options):
            emoji = ["🅰️", "🅱️", "🇨", "🇩"][i] if i < 4 else f"{i+1}️⃣"
            markup.add(InlineKeyboardButton(
                f"{emoji} {option}", 
                callback_data=f"answer|{subject}|{i}"
            ))

        # Delete previous question if exists
        if progress.last_message_id:
            try:
                await self.bot.delete_message(user_id, progress.last_message_id)
            except:
                pass  # Message might be already deleted

        # Send new question
        msg = await self.bot.send_message(
            user_id, 
            question_text, 
            reply_markup=markup,
            parse_mode='Markdown'
        )
        
        # Update progress with new message ID
        progress.last_message_id = msg.message_id
        await self.db.save_progress(progress)

    async def _handle_answer(self, call: CallbackQuery):
        """Handle user's answer"""
        try:
            _, subject, answer_idx = call.data.split("|")
            user_id = call.from_user.id
            answer_idx = int(answer_idx)

            quiz = await self.db.get_quiz(subject)
            if not quiz:
                await self.bot.answer_callback_query(call.id, "❌ Quiz not found!")
                return

            progress = await self.db.get_progress(user_id, subject)
            current_question = quiz[progress.current_index]

            # Check answer
            if answer_idx == current_question.correct:
                progress.score += 1
                await self.bot.answer_callback_query(call.id, "✅ Correct! 🎉")
            else:
                correct_answer = current_question.options[current_question.correct]
                await self.bot.answer_callback_query(
                    call.id, 
                    f"❌ Wrong! Correct: {correct_answer}"
                )

            # Move to next question
            progress.current_index += 1
            await self.db.save_progress(progress)
            
            # Send next question
            await self._send_question(user_id, subject)
            
        except Exception as e:
            await self.bot.answer_callback_query(call.id, "❌ Error processing answer!")

    async def _callback_handler(self, call: CallbackQuery):
        """Handle all callback queries"""
        try:
            if call.data.startswith("subject|"):
                subject = call.data.split("|")[1]
                await self._send_question(call.from_user.id, subject)
                
            elif call.data.startswith("answer|"):
                await self._handle_answer(call)
                
            elif call.data == "admin_add_quiz":
                await self.bot.send_message(
                    call.message.chat.id,
                    "📥 *Send me a JSON file with quiz questions.*\n\n"
                    "*Format:*\n"
                    "```json\n"
                    "[\n"
                    "  {\n"
                    '    "question": "Your question?",\n'
                    '    "options": ["Option A", "Option B", "Option C", "Option D"],\n'
                    '    "correct": 0\n'
                    "  }\n"
                    "]\n"
                    "```\n"
                    "*Note:* Use 0-based index for correct answer.",
                    parse_mode='Markdown'
                )
                
            elif call.data == "admin_all_scores":
                all_scores = await self.db.get_all_scores()
                if not all_scores:
                    await self.bot.send_message(call.message.chat.id, "📭 *No scores available!*", parse_mode='Markdown')
                    return
                
                scores_text = "📊 **All User Scores** 📊\n\n"
                for scorer in all_scores:
                    scores_text += f"#{scorer.rank} **{scorer.name}** (@{scorer.username})\n   📈 **Score:** {scorer.total_score}\n\n"
                
                await self.bot.send_message(call.message.chat.id, scores_text, parse_mode='Markdown')
                
            elif call.data == "admin_add_subject":
                await self.bot.send_message(
                    call.message.chat.id,
                    "📝 *Send the name of the new subject:*",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logging.error(f"Callback error: {e}")
            await self.bot.answer_callback_query(call.id, "❌ An error occurred!")

    async def run(self):
        """Start the bot"""
        await self.initialize()
        logging.info("🤖 HU Quiz Bot is running...")
        await self.bot.polling(non_stop=True)

# ========================
# 🚀 APPLICATION ENTRY POINT
# ========================
async def main():
    """Main application entry point"""
    # Configure logging
    logging.basicConfig(
        level=Config.LOG_LEVEL,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('quiz_bot.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    # Initialize and run bot
    bot = ModernQuizBot(Config.API_TOKEN, Config.ADMIN_ID)
    
    try:
        await bot.run()
    except Exception as e:
        logging.error(f"❌ Bot crashed: {e}")
    finally:
        logging.info("🛑 Bot stopped")

if __name__ == "__main__":
    asyncio.run(main())