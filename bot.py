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
    reset_scores,
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
QUIZ_TIME_LIMIT = 20  # Thời gian đoán cho mỗi từ (giây)
JOIN_TIME_LIMIT = 20  # Thời gian chờ người chơi tham gia (giây)
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


def build_quiz_embed(shuffled_display, remaining_seconds):
    remaining_seconds = max(0, int(remaining_seconds))
    timer_color = discord.Color.red() if remaining_seconds <= 5 else discord.Color.blurple()

    embed = discord.Embed(
        title="🔤 ĐOÁN TỪ XÁO TRỘN",
        description="Sắp xếp các chữ cái để tìm ra từ bí mật!",
        color=timer_color
    )
    embed.add_field(
        name="🧩 Các chữ cái",
        value=f"```text\n{shuffled_display}\n```",
        inline=False
    )
    embed.add_field(
        name="⏳ THỜI GIAN CÒN LẠI",
        value=f"# **{remaining_seconds} GIÂY**",
        inline=True
    )
    embed.add_field(
        name="⭐ PHẦN THƯỞNG",
        value=f"**+{CORRECT_POINTS} điểm**",
        inline=True
    )
    embed.set_footer(text="Gõ đáp án vào chat • Sai được đoán lại cho đến khi hết giờ")
    return embed


async def update_quiz_timer(message, shuffled_display, end_time):
    try:
        while True:
            remaining_seconds = max(0, int(end_time - asyncio.get_event_loop().time() + 0.999))
            await message.edit(
                embed=build_quiz_embed(
                    shuffled_display,
                    remaining_seconds
                )
            )

            if remaining_seconds <= 0:
                break

            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


class GameSetupView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=JOIN_TIME_LIMIT)
        self.ctx = ctx
        self.mode = None
        self.players = {ctx.author.id: ctx.author.display_name}

    def setup_embed(self):
        if self.mode == "friends":
            player_lines = "\n".join(
                f"• {name}" for name in self.players.values()
            )
            description = (
                "👥 **Chế độ chơi với bạn bè**\n"
                f"Bấm **Tham gia** trong **{JOIN_TIME_LIMIT} giây**.\n\n"
                f"**Người chơi ({len(self.players)}):**\n{player_lines}"
            )
            color = discord.Color.green()
        else:
            description = (
                "🎮 **Bạn muốn chơi như thế nào?**\n\n"
                "👤 Một mình: chỉ bạn được trả lời.\n"
                "👥 Với bạn bè: mở phòng chờ để mọi người tham gia."
            )
            color = discord.Color.blurple()

        return discord.Embed(
            title="🎯 THIẾT LẬP VÁN CHƠI",
            description=description,
            color=color
        )

    def disable_all(self):
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction):
        if interaction.channel_id != self.ctx.channel.id:
            await interaction.response.send_message(
                "⚠️ Ván chơi này ở kênh khác.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Một mình", emoji="👤", style=discord.ButtonStyle.primary)
    async def solo_button(self, interaction, button):
        if self.mode is not None and self.mode != "solo":
            await interaction.response.send_message(
                "⚠️ Chế độ chơi với bạn bè đã được chọn.",
                ephemeral=True
            )
            return
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "⚠️ Chỉ người tạo ván mới được chọn chế độ chơi.",
                ephemeral=True
            )
            return

        self.mode = "solo"
        self.disable_all()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="👤 CHẾ ĐỘ MỘT MÌNH",
                description="Ván chơi sẽ bắt đầu ngay!",
                color=discord.Color.green()
            ),
            view=self
        )
        self.stop()

    @discord.ui.button(label="Chơi với bạn bè", emoji="👥", style=discord.ButtonStyle.success)
    async def friends_button(self, interaction, button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "⚠️ Chỉ người tạo ván mới được chọn chế độ chơi.",
                ephemeral=True
            )
            return

        self.mode = "friends"
        self.solo_button.disabled = True
        self.friends_button.disabled = True
        self.join_button.disabled = False
        await interaction.response.edit_message(
            embed=self.setup_embed(),
            view=self
        )

    @discord.ui.button(label="Tham gia", emoji="✅", style=discord.ButtonStyle.secondary, disabled=True)
    async def join_button(self, interaction, button):
        if self.mode != "friends":
            await interaction.response.send_message(
                "⚠️ Hãy chọn chế độ chơi với bạn bè trước.",
                ephemeral=True
            )
            return

        if interaction.user.id not in self.players:
            self.players[interaction.user.id] = interaction.user.display_name

        await interaction.response.edit_message(
            embed=self.setup_embed(),
            view=self
        )

    async def on_timeout(self):
        self.disable_all()
        try:
            await self.message.edit(
                embed=discord.Embed(
                    title="⏱️ HẾT THỜI GIAN THAM GIA",
                    description="Phòng chờ đã đóng.",
                    color=discord.Color.red()
                ),
                view=self
            )
        except (discord.NotFound, discord.HTTPException):
            pass


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

    setup_view = GameSetupView(ctx)
    setup_message = await ctx.send(
        embed=setup_view.setup_embed(),
        view=setup_view
    )
    setup_view.message = setup_message
    await setup_view.wait()

    if setup_view.mode is None:
        await ctx.send("⚠️ Chưa chọn chế độ chơi. Ván chơi đã hủy.")
        return

    reset_scores()
    active_games[channel_id] = True
    used_words[channel_id] = set()
    total_words = len({normalize_text(word) for word in vocab})
    player_ids = set(setup_view.players)
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

        end_time = asyncio.get_event_loop().time() + QUIZ_TIME_LIMIT
        round_number = len(used_words[channel_id])
        question_message = await ctx.send(
            embed=build_quiz_embed(
                shuffled_display,
                QUIZ_TIME_LIMIT
            )
        )
        timer_task = asyncio.create_task(
            update_quiz_timer(
                question_message,
                shuffled_display,
                end_time
            )
        )
        is_correct = False

        # Vòng lặp nhận câu trả lời liên tục trong 20s
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
                        and msg.author.id in player_ids
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
                        title=f"🎉 ĐÁP ÁN ĐÚNG: {answer}",
                        color=discord.Color.green()
                    )
                    embed_win.description = (
                        f"**{msg.author.mention}** đã trả lời chính xác từ này."
                    )
                    embed_win.add_field(
                        name="⭐ ĐIỂM NHẬN ĐƯỢC",
                        value=f"**+{CORRECT_POINTS} điểm**",
                        inline=True
                    )
                    embed_win.add_field(
                        name="🏆 TỔNG ĐIỂM",
                        value=f"**{new_score} điểm**",
                        inline=True
                    )
                    embed_win.set_footer(text="Câu trả lời chính xác")
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

        timer_task.cancel()
        await timer_task

        # Nếu nhận lệnh dừng trong lúc chờ
        if not active_games.get(channel_id, False):
            break

        # Nếu hết 15s mà không ai đoán đúng
        if not is_correct:
            embed_timeout = discord.Embed(
                title=f"⏰ ĐÁP ÁN LÀ: {answer}",
                color=discord.Color.red()
            )
            embed_timeout.description = "Hết giờ! Đây là đáp án của từ vừa rồi."
            embed_timeout.add_field(
                name="⏱️ THỜI GIAN",
                value=f"# **0 / {QUIZ_TIME_LIMIT} GIÂY**",
                inline=True
            )
            embed_timeout.set_footer(text="Thời gian đã kết thúc • Hãy chuẩn bị cho từ tiếp theo")
            await ctx.send(embed=embed_timeout)

        if len(used_words[channel_id]) >= total_words:
            game_completed = True
            await send_leaderboard(ctx)
            break

        # Cập nhật trực tiếp thời gian chờ trước câu tiếp theo
        countdown_message = await ctx.send(
            f"⏳ Từ mới sẽ bắt đầu sau **{NEXT_ROUND_DELAY} giây**..."
        )

        for remaining_seconds in range(NEXT_ROUND_DELAY, 0, -1):
            if not active_games.get(channel_id, False):
                break

            await countdown_message.edit(
                content=f"⏳ Từ mới sẽ bắt đầu sau **{remaining_seconds} giây**..."
            )
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