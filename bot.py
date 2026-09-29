import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import random
import datetime
import os
from dateutil import parser

# --- 1. 初期設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        init_db()
        self.bot_loop.start()
        self.add_view(StatusView())
        self.add_view(ShopView())
        self.add_view(GiveawayView())
        await self.tree.sync()

    @tasks.loop(seconds=60)
    async def bot_loop(self):
        await check_vc_xp(self)
        await check_giveaway_end(self)

    @bot_loop.before_loop
    async def before_bot_loop(self):
        await self.wait_until_ready()

bot = MyBot()

DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # ユーザーデータ
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            tickets INTEGER DEFAULT 0
        )
    ''')
    # チャンネル設定
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            lvl_channel_id INTEGER,
            shop_channel_id INTEGER,
            welcome_channel_id INTEGER
        )
    ''')
    # ショップ商品
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            cost INTEGER,
            description TEXT
        )
    ''')
    # ギフト企画
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS giveaways (
            giveaway_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            channel_id INTEGER,
            message_id INTEGER,
            prize TEXT,
            end_time TEXT,
            min_level INTEGER,
            status TEXT DEFAULT 'active'
        )
    ''')
    # ギフト参加者
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS giveaway_participants (
            giveaway_id INTEGER,
            user_id INTEGER,
            weight INTEGER DEFAULT 1,
            PRIMARY KEY (giveaway_id, user_id)
        )
    ''')
    conn.commit()
    conn.close()

def get_required_xp(level):
    return level * 100

def add_xp(user_id, xp_to_add):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT xp, level, tickets FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        xp, level, tickets = 0, 1, 0
        cursor.execute("INSERT INTO users (user_id, xp, level, tickets) VALUES (?, ?, ?, ?)", (user_id, xp, level, tickets))
    else:
        xp, level, tickets = row
    new_xp = xp + xp_to_add
    req_xp = get_required_xp(level)
    leveled_up = False
    while new_xp >= req_xp:
        new_xp -= req_xp
        level += 1
        tickets += 1
        leveled_up = True
        req_xp = get_required_xp(level)
    cursor.execute("UPDATE users SET xp = ?, level = ?, tickets = ? WHERE user_id = ?", (new_xp, level, tickets, user_id))
    conn.commit()
    conn.close()
    return leveled_up, level

# チャットXP獲得
last_chat_xp = {}
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    now = datetime.datetime.now()
    user_id = message.author.id
    if user_id not in last_chat_xp or (now - last_chat_xp[user_id]).total_seconds() >= 60:
        last_chat_xp[user_id] = now
        leveled_up, new_level = add_xp(user_id, random.randint(1, 5))
        if leveled_up:
            await announce_level_up(message.guild, message.author, new_level)
    await bot.process_commands(message)

async def announce_level_up(guild, member, level):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT lvl_channel_id FROM settings WHERE guild_id = ?", (guild.id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        channel = guild.get_channel(row[0])
        if channel:
            embed = discord.Embed(title="😈 レベルアップ！", description=f"{member.mention} のレベルが **Lv.{level}** に上がりました！\n🎫 **小悪魔チケット**を1枚獲得しました！", color=0x9b59b6)
            await channel.send(embed=embed)

# VC XP獲得処理
async def check_vc_xp(self):
    for guild in self.guilds:
        for vc in guild.voice_channels:
            members = [m for m in vc.members if not m.bot]
            if len(members) <= 1:
                continue
            for m in members:
                if m.voice.self_mute or m.voice.self_deaf or m.voice.mute or m.voice.deaf:
                    continue
                leveled_up, new_level = add_xp(m.id, random.randint(1, 5))
                if leveled_up:
                    await announce_level_up(guild, m, new_level)

# ギフト企画終了の自動チェック
async def check_giveaway_end(self):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.datetime.now().isoformat()
    cursor.execute("SELECT giveaway_id, guild_id, channel_id, message_id, prize FROM giveaways WHERE status = 'active' AND end_time <= ?", (now_str,))
    ended = cursor.fetchall()
    conn.close()
    
    for g_id, guild_id, ch_id, msg_id, prize in ended:
        guild = self.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        try:
            message = await channel.fetch_message(msg_id)
        except:
            message = None
            
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, weight FROM giveaway_participants WHERE giveaway_id = ?", (g_id,))
        participants = cursor.fetchall()
        
        if not participants:
            cursor.execute("UPDATE giveaways SET status = 'ended' WHERE giveaway_id = ?", (g_id,))
            conn.commit()
            conn.close()
            if message:
                embed = message.embeds[0]
                embed.title = f"🎉 【終了】{prize}"
                embed.description = "参加者がいなかったため、当選者はありませんでした。"
                await message.edit(embed=embed, view=None)
            continue
            
        pool = []
        for u_id, w in participants:
            pool.extend([u_id] * w)
        winner_id = random.choice(pool)
        
        cursor.execute("UPDATE giveaways SET status = 'ended' WHERE giveaway_id = ?", (g_id,))
        conn.commit()
        conn.close()
        
        winner_user = guild.get_member(winner_id) or await self.fetch_user(winner_id)
        winner_mention = winner_user.mention if winner_user else f"<@{winner_id}>"
        
        if message:
            embed = message.embeds[0]
            embed.title = f"🎉 【終了】{prize}"
            embed.description = f"**当選者:** {winner_mention}\nたくさんのご参加ありがとうございました！"
            await message.edit(embed=embed, view=None)
            
        await channel.send(f"🎉 **ギフト企画抽選結果** 🎉\n【{prize}】の当選者は {winner_mention} です！おめでとうございます！")

# 共有UIコンポーネント
class StatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="ステータス確認", style=discord.ButtonStyle.primary, custom_id="check_status_btn")
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT xp, level, tickets FROM users WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        conn.close()
        xp, level, tickets = row if row else (0, 1, 0)
        req_xp = get_required_xp(level)
        embed = discord.Embed(title=f"😈 {interaction.user.display_name} のステータス", color=0x9b59b6)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="現在のレベル", value=f"Lv.{level}", inline=True)
        embed.add_field(name="小悪魔チケット", value=f"🎫 {tickets} 枚", inline=True)
        embed.add_field(name="経験値 (XP)", value=f"{xp} / {req_xp} XP", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="ショップを開く", style=discord.ButtonStyle.success, custom_id="open_shop_btn")
    async def open_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT item_id, name, cost, description FROM shop_items")
        items = cursor.fetchall()
        conn.close()
        if not items:
            await interaction.response.send_message("現在ショップに商品はありません。", ephemeral=True)
            return
        embed = discord.Embed(title="😈 小悪魔ショップ", description="チケットを消費してアイテムと交換できます。", color=0xe74c3c)
        view = discord.ui.View()
        for item_id, name, cost, desc in items:
            embed.add_field(name=f"{name} (🎫 {cost}枚)", value=desc, inline=False)
            btn = discord.ui.Button(label=f"{name} と交換", style=discord.ButtonStyle.secondary, custom_id=f"buy_{item_id}")
            async def make_callback(i_id=item_id, c=cost, n=name):
                async def callback(i: discord.Interaction):
                    conn2 = sqlite3.connect(DB_NAME)
                    cursor2 = conn2.cursor()
                    cursor2.execute("SELECT tickets FROM users WHERE user_id = ?", (i.user.id,))
                    r = cursor2.fetchone()
                    t = r[0] if r else 0
                    if t < c:
                        await i.response.send_message("🎫 小悪魔チケットが足りません！", ephemeral=True)
                        conn2.close()
                        return
                    cursor2.execute("UPDATE users SET tickets = tickets - ? WHERE user_id = ?", (c, i.user.id))
                    cursor2.execute("SELECT shop_channel_id FROM settings WHERE guild_id = ?", (i.guild_id,))
                    s_row = cursor2.fetchone()
                    conn2.commit()
                    conn2.close()
                    await i.response.send_message(f"🎁 **{n}** の交換を申請しました！", ephemeral=True)
                    if s_row and s_row[0]:
                        ch = i.guild.get_channel(s_row[0])
                        if ch:
                            await ch.send(f"🛒 **交換完了通知**\n{i.user.mention} が **{n}** を交換しました！ (消費: 🎫 {c}枚)")
                return callback
            btn.callback = await make_callback()
            view.add_item(btn)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    async def get_giveaway(self, message_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT giveaway_id, min_level, status, prize FROM giveaways WHERE message_id = ?", (message_id,))
        row = cursor.fetchone()
        conn.close()
        return row

    async def check_user_level(self, user_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT level, tickets FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row if row else (1, 0)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, custom_id="ga_join")
    async def join_ga(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await self.get_giveaway(interaction.message.id)
        if not g or g[2] != 'active':
            return await interaction.response.send_message("この企画は終了しているか、存在しません。", ephemeral=True)
        g_id, min_lvl, _, _ = g
        lvl, _ = await self.check_user_level(interaction.user.id)
        if lvl < min_lvl:
            return await interaction.response.send_message(f"⚠️ 参加に必要なレベル (Lv.{min_lvl}) に達していません。(現在: Lv.{lvl})", ephemeral=True)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES (?, ?)", (g_id, interaction.user.id))
            conn.commit()
            await interaction.response.send_message("🎉 ギフト企画に参加しました！", ephemeral=True)
        except sqlite3.IntegrityError:
            await interaction.response.send_message("既にこの企画に参加しています。", ephemeral=True)
        finally:
            conn.close()

    @discord.ui.button(label="参加取り消し", style=discord.ButtonStyle.danger, custom_id="ga_leave")
    async def leave_ga(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await self.get_giveaway(interaction.message.id)
        if not g or g[2] != 'active':
            return await interaction.response.send_message("この企画は終了しています。", ephemeral=True)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?", (g[0], interaction.user.id))
        conn.commit()
        conn.close()
        await interaction.response.send_message("📥 参加を取り消しました。", ephemeral=True)

    @discord.ui.button(label="参加者一覧", style=discord.ButtonStyle.secondary, custom_id="ga_list")
    async def list_ga(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await self.get_giveaway(interaction.message.id)
        if not g:
            return await interaction.response.send_message("企画が見つかりません。", ephemeral=True)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM giveaway_participants WHERE giveaway_id = ?", (g[0],))
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return await interaction.response.send_message("現在の参加者は0人です。", ephemeral=True)
        mentions = [f"<@{r[0]}>" for r in rows]
        await interaction.response.send_message(f"👥 **現在の参加者一覧:**\n" + ", ".join(mentions), ephemeral=True)

    @discord.ui.button(label="当選確率", style=discord.ButtonStyle.secondary, custom_id="ga_odds")
    async def odds_ga(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await self.get_giveaway(interaction.message.id)
        if not g:
            return await interaction.response.send_message("企画が見つかりません。", ephemeral=True)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, weight FROM giveaway_participants WHERE giveaway_id = ?", (g[0],))
        parts = cursor.fetchall()
        conn.close()
        total_weight = sum([p[1] for p in parts])
        user_weight = next((p[1] for p in parts if p[0] == interaction.user.id), 0)
        if user_weight == 0:
            return await interaction.response.send_message("企画に参加していないため、現在の当選確率は 0% です。", ephemeral=True)
        odds = (user_weight / total_weight) * 100
        await interaction.response.send_message(f"🔮 あなたの現在の当選確率は **{odds:.2f}%** です (あなたの抽選券: {user_weight}枚 / 総数: {total_weight}枚)。", ephemeral=True)

    @discord.ui.button(label="チケットで確率アップ", style=discord.ButtonStyle.primary, custom_id="ga_boost")
    async def boost_ga(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await self.get_giveaway(interaction.message.id)
        if not g or g[2] != 'active':
            return await interaction.response.send_message("この企画は終了しています。", ephemeral=True)
        g_id = g[0]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?", (g_id, interaction.user.id))
        if not cursor.fetchone():
            conn.close()
            return await interaction.response.send_message("⚠️ まず「参加」ボタンを押して企画に参加してください。", ephemeral=True)
        cursor.execute("SELECT tickets FROM users WHERE user_id = ?", (interaction.user.id,))
        r = cursor.fetchone()
        t = r[0] if r else 0
        if t < 1:
            conn.close()
            return await interaction.response.send_message("🎫 小悪魔チケットがありません！", ephemeral=True)
        cursor.execute("UPDATE users SET tickets = tickets - 1 WHERE user_id = ?", (interaction.user.id,))
        cursor.execute("UPDATE giveaway_participants SET weight = weight + 1 WHERE giveaway_id = ? AND user_id = ?", (g_id, interaction.user.id))
        conn.commit()
        conn.close()
        await interaction.response.send_message("🔥 チケットを1枚消費して当選確率をアップさせました！(抽選券が1枚追加されました)", ephemeral=True)

# 管理者コマンド
@bot.tree.command(name="bot通知設定", description="各種通知チャンネルを設定します（管理者用）")
@app_commands.describe(レベルアップ通知="レベルアップ通知チャンネル", 交換完了通知="ショップの交換完了通知チャンネル", 入退室通知="メンバーの入退室通知チャンネル")
@app_commands.checks.has_permissions(administrator=True)
async def set_notifications(interaction: discord.Interaction, レベルアップ通知: discord.TextChannel = None, 交換完了通知: discord.TextChannel = None, 入退室通知: discord.TextChannel = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    guild_id = interaction.guild_id
    cursor.execute("SELECT * FROM settings WHERE guild_id = ?", (guild_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO settings (guild_id) VALUES (?)", (guild_id,))
    if レベルアップ通知:
        cursor.execute("UPDATE settings SET lvl_channel_id = ? WHERE guild_id = ?", (レベルアップ通知.id, guild_id))
    if 交換完了通知:
        cursor.execute("UPDATE settings SET shop_channel_id = ? WHERE guild_id = ?", (交換完了通知.id, guild_id))
    if 入退室通知:
        cursor.execute("UPDATE settings SET welcome_channel_id = ? WHERE guild_id = ?", (入退室通知.id, guild_id))
    conn.commit()
    conn.close()
    await interaction.response.send_message("⚙️ 通知チャンネルの設定を更新しました！", ephemeral=True)

@bot.tree.command(name="botボタン設定", description="ショップやステータス確認ボタンを設置します（管理者用）")
@app_commands.checks.has_permissions(administrator=True)
async def set_buttons(interaction: discord.Interaction):
    view = discord.ui.View(timeout=None)
    view.add_item(StatusView().children[0])
    view.add_item(ShopView().children[0])
    await interaction.response.send_message("以下のボタンからショップの利用やステータス確認ができます。", view=view)

@bot.tree.command(name="ギフト企画作成", description="景品、締切日時、最低参加レベルを設定し企画作成します（管理者用）")
@app_commands.describe(景品="プレゼントするアイテム名", 締切日時="YYYY-MM-DD HH:MM 形式で入力", 最低参加レベル="参加に必要な最小レベル")
@app_commands.checks.has_permissions(administrator=True)
async def create_giveaway(interaction: discord.Interaction, 景品: str, 締切日時: str, 最低参加レベル: int = 1):
    try:
        dt = parser.parse(締切日時)
    except:
        return await interaction.response.send_message("⚠️ 日時のフォーマットが正しくありません。「2026-10-01 20:00」のように入力してください。", ephemeral=True)
    
    end_time_str = dt.isoformat()
    await interaction.response.defer()
    
    embed = discord.Embed(title=f"🎁 ギフト企画開催：{景品}", description=f"ボタンを押して企画に参加しよう！\n\n⌛ **締切日時:** {dt.strftime('%Y/%m/%d %H:%M')}\n🔰 **参加条件:** Lv.{最低参加レベル} 以上", color=0x3498db)
    view = GiveawayView()
    msg = await interaction.followup.send(embed=embed, view=view)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO giveaways (guild_id, channel_id, message_id, prize, end_time, min_level) VALUES (?, ?, ?, ?, ?, ?)",
                   (interaction.guild_id, interaction.channel_id, msg.id, 景品, end_time_str, 最低参加レベル))
    conn.commit()
    conn.close()

@bot.tree.command(name="商品追加", description="ショップに新しい商品を追加します（管理者用）")
@app_commands.describe(商品名="商品の名前", 必要チケット数="必要な小悪魔チケット数", 内容="商品の説明")
@app_commands.checks.has_permissions(administrator=True)
async def add_item(interaction: discord.Interaction, 商品名: str, 必要チケット数: int, 内容: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shop_items (name, cost, description) VALUES (?, ?, ?)", (商品名, 必要チケット数, 内容))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"📦 ショップに商品を追加しました：**{商品名}** ({必要チケット数}枚)", ephemeral=True)

@bot.tree.command(name="商品編集", description="ショップ内にある商品の編集、削除ができます（管理者用）")
@app_commands.describe(商品id="編集または削除する商品のID", 新商品名="変更後の名前(空欄で維持)", 新必要チケット数="変更後のチケット数(空欄で維持)", 削除する="「はい」を選ぶとこの商品を削除します")
@app_commands.checks.has_permissions(administrator=True)
async def edit_item(interaction: discord.Interaction, 商品id: int, 新商品名: str = None, 新必要チケット数: int = None, 削除する: str = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if 削除する == "はい":
        cursor.execute("DELETE FROM shop_items WHERE item_id = ?", (商品id,))
        await interaction.response.send_message(f"🗑️ 商品ID {商品id} を削除しました。", ephemeral=True)
    else:
        if 新商品名:
            cursor.execute("UPDATE shop_items SET name = ? WHERE item_id = ?", (新商品名, 商品id))
        if 新必要チケット数 is not None:
            cursor.execute("UPDATE shop_items SET cost = ? WHERE item_id = ?", (新必要チケット数, 商品id))
        await interaction.response.send_message(f"📝 商品ID {商品id} の内容を更新しました。", ephemeral=True)
    conn.commit()
    conn.close()

# 入退室通知
@bot.event
async def on_member_join(member):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT welcome_channel_id FROM settings WHERE guild_id = ?", (member.guild.id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        channel = member.guild.get_channel(row[0])
        if channel:
            now = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            embed = discord.Embed(title="📥 メンバーが入室しました", description=f"**{member.name}** が参加しました！", color=0x2ecc71)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="日時", value=now, inline=False)
            await channel.send(embed=embed)

@bot.event
async def on_member_remove(member):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT welcome_channel_id FROM settings WHERE guild_id = ?", (member.guild.id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        channel = member.guild.get_channel(row[0])
        if channel:
            now = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            embed = discord.Embed(title="📤 メンバーが退出しました", description=f"**{member.name}** が退室しました。", color=0xe74c3c)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="日時", value=now, inline=False)
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name}")

bot.run(os.getenv("DISCORD_TOKEN"))
