import os
import json
import random
import asyncio
import unicodedata

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database import (
    init_database,
    get_score,
    add_score,
    get_leaderboard
)

# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("❌ Không tìm thấy DISCORD_TOKEN trong file .env")

VOCAB_FILE = "vocab.json"
QUIZ_TIME_LIMIT = 15  # Thời gian đoán cho mỗi từ (giây)
CORRECT_POINTS = 10   # Điểm thưởng cho mỗi từ đúng
NEXT_ROUND_DELAY = 5  # Thời gian chờ giữa các từ (giây)


# ============================================================
# LOAD VOCAB
# ============================================================

def load_vocab():
    try:
        with open(VOCAB_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("vocab.json phải là một danh sách.")

        words = [
            str(word).strip()
            for word in data
            if str(word).strip()
        ]
        return words

    except FileNotFoundError:
        print(f"❌ Không tìm thấy file {VOCAB_FILE}")
        return []
    except json.JSONDecodeError:
        print(f"❌ File {VOCAB_FILE} không đúng định dạng JSON.")
        return []


vocab = load_vocab()


# ============================================================
# DISCORD INTENTS & BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

# Thiết lập prefix là 'w' hoặc 'W' (ví dụ: wstart, wdiem, wtop, wstop)
bot = commands.Bot(
    command_prefix=["w", "W"],
    intents=intents,
    case_insensitive=True,
    help_command=None
)


# ============================================================
# GAME STATE
# ============================================================

# Quản lý trạng thái game đang chạy ở từng channel (channel_id -> True/False)
active_games = {}
used_words = {}


# ============================================================
# TEXT NORMALIZATION & SHUFFLE
# ============================================================

def normalize_text(text):
    text = text.strip().lower()
    text = unicodedata.normalize("NFC", text)
    text = " ".join(text.split())
    return text


def shuffle_word(word):
    word = unicodedata.normalize("NFC", word)
    letters = [char for char in word if not char.isspace()]
    
    # Đảm bảo từ xáo trộn không bị trùng hoàn toàn với từ gốc
    if len(letters) > 2:
        original = letters.copy()
        while letters == original:
            random.shuffle(letters)
    else:
        random.shuffle(letters)

    return letters


def format_shuffled_letters(letters):
    return " / ".join(letters)


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():
    init_database()
    print(f"🤖 Bot đã đăng nhập: {bot.user}")
    print(f"📚 Đã tải {len(vocab)} từ vựng.")
    print("🚀 Các lệnh khả dụng: wstart, wdiem, wtop, wstop")


# ============================================================
# COMMANDS
# ============================================================

@bot.command(name="start")
async def start_game(ctx):
    channel_id = ctx.channel.id

    if active_games.get(channel_id, False):
        await ctx.send("⚠️ Game đang chạy trong kênh này rồi! Gõ `wstop` để dừng.")
        return

    if not vocab:
        await ctx.send("❌ Chưa có từ vựng trong file vocab.json.")
        return

    active_games[channel_id] = True
    used_words[channel_id] = set()
    total_words = len({normalize_text(word) for word in vocab})
    await ctx.send("🎮 **BẮT ĐẦU GAME ĐOÁN TỪ LIÊN TỤC!**\n👉 Gõ `wstop` bất kỳ lúc nào để dừng game.")

    # Vòng lặp game tự động cho kênh
    game_completed = False
    while active_games.get(channel_id, False):
        remaining_words = [
            word for word in vocab
            if normalize_text(word) not in used_words[channel_id]
        ]

        if not remaining_words:
            game_completed = True
            break

        answer = random.choice(remaining_words)
        answer = unicodedata.normalize("NFC", answer.strip())
        used_words[channel_id].add(normalize_text(answer))

        shuffled_letters = shuffle_word(answer)
        shuffled_display = format_shuffled_letters(shuffled_letters)

        embed = discord.Embed(
            title="🔀 XÁO TRỘN CHỮ",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="🧩 Các chữ cái",
            value=f"```text\n{shuffled_display}\n```",
            inline=False
        )
        embed.add_field(
            name="⏱️ Thời gian",
            value=f"**{QUIZ_TIME_LIMIT} giây**",
            inline=True
        )
        embed.add_field(
            name="⭐ Phần thưởng",
            value=f"**+{CORRECT_POINTS} điểm**",
            inline=True
        )
        embed.set_footer(text="Gõ đáp án vào chat! (Sai được đoán lại cho đến khi hết giờ)")

        await ctx.send(embed=embed)

        end_time = asyncio.get_event_loop().time() + QUIZ_TIME_LIMIT
        is_correct = False

        # Vòng lặp nhận câu trả lời liên tục trong 15s
        while asyncio.get_event_loop().time() < end_time:
            if not active_games.get(channel_id, False):
                break

            remaining_time = end_time - asyncio.get_event_loop().time()
            if remaining_time <= 0:
                break

            try:
                def check(msg):
                    return (
                        msg.channel.id == channel_id
                        and not msg.author.bot
                    )

                msg = await bot.wait_for("message", timeout=remaining_time, check=check)

                # Nếu người chơi gõ wstop
                if msg.content.strip().lower() in ["wstop", "Wstop"]:
                    break

                user_ans = normalize_text(msg.content)
                correct_ans = normalize_text(answer)

                if user_ans == correct_ans:
                    add_score(msg.author.id, msg.author.display_name, CORRECT_POINTS)
                    new_score = get_score(msg.author.id)

                    embed_win = discord.Embed(
                        title="🎉 CHÍNH XÁC!",
                        color=discord.Color.green()
                    )
                    embed_win.description = (
                        f"**{msg.author.mention}** đã trả lời đúng: **{answer}**\n\n"
                        f"⭐ **+{CORRECT_POINTS} điểm** | 🏆 Tổng điểm: **{new_score}**"
                    )
                    await ctx.send(embed=embed_win)
                    is_correct = True
                    break
                else:
                    # Thả icon ❌ vào tin nhắn đoán sai
                    try:
                        await msg.add_reaction("❌")
                    except Exception:
                        pass

            except asyncio.TimeoutError:
                break

        # Nếu nhận lệnh dừng trong lúc chờ
        if not active_games.get(channel_id, False):
            break

        # Nếu hết 15s mà không ai đoán đúng
        if not is_correct:
            embed_timeout = discord.Embed(
                title="⏰ HẾT GIỜ!",
                color=discord.Color.red()
            )
            embed_timeout.description = f"Không ai đoán đúng! Đáp án là:\n\n## {answer}"
            await ctx.send(embed=embed_timeout)

        if len(used_words[channel_id]) >= total_words:
            game_completed = True
            await send_leaderboard(ctx)
            break

        # Chờ 5 giây trước khi sang câu mới
        await ctx.send(f"⏳ Từ mới sẽ bắt đầu sau **{NEXT_ROUND_DELAY} giây**...")

        for _ in range(NEXT_ROUND_DELAY):
            if not active_games.get(channel_id, False):
                break
            await asyncio.sleep(1)

    active_games[channel_id] = False
    used_words.pop(channel_id, None)

    if game_completed:
        await ctx.send("🏁 Đã sử dụng hết toàn bộ từ vựng! Game kết thúc.")
    else:
        await ctx.send("🛑 Game đã dừng hoàn toàn.")


@bot.command(name="stop")
async def stop_game(ctx):
    channel_id = ctx.channel.id
    if active_games.get(channel_id, False):
        active_games[channel_id] = False
        await ctx.send("🛑 Đã nhận lệnh dừng game!")
    else:
        await ctx.send("⚠️ Hiện không có game nào đang chạy trong kênh này.")


@bot.command(name="diem")
async def score_command(ctx):
    score = get_score(ctx.author.id)
    embed = discord.Embed(
        title="🏆 ĐIỂM CỦA BẠN",
        color=discord.Color.green()
    )
    embed.description = f"{ctx.author.mention}\n\n⭐ **{score} điểm**"
    await ctx.send(embed=embed)


@bot.command(name="top")
async def leaderboard_command(ctx):
    await send_leaderboard(ctx)


async def send_leaderboard(ctx):
    leaderboard = get_leaderboard(10)

    if not leaderboard:
        await ctx.send("📊 Chưa có ai có điểm.")
        return

    embed = discord.Embed(
        title="🏆 BẢNG XẾP HẠNG",
        color=discord.Color.gold()
    )

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []

    for index, (username, score) in enumerate(leaderboard, start=1):
        prefix = medals.get(index, f"**{index}.**")
        lines.append(f"{prefix} **{username}** — ⭐ {score}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Top 10 người chơi")
    await ctx.send(embed=embed)


# ============================================================
# START BOT
# ============================================================

bot.run(TOKEN)