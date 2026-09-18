"""
cogs/talk/say.py

職責：
- /say：以 Bot 身份在目前頻道發送訊息（支援附件、回覆、圖片 URL）
- 使用者需有 Manage Messages 權限
- 代發內文會標示發起者：**暱稱**說：內容

Modification():

- 移植自 Bot-Firefly/cogs/talk/say.py
- 加入 from __future__ import annotations
- 類別命名改為 Say（PEP 8）
- 錯誤處理更細緻

- 修正 /say 在使用者缺少 Manage Messages 時觸發 MissingPermissions 例外，
  但因 bot.py 原本沒有 CommandTree error handler，使用者看到「互動未能回應」
  而非任何說明。已兩面修正：
  1. bot.py 新增 CustomCommandTree.on_error 作為最後防線
  2. 保留 @app_commands.default_permissions 作為 Discord 側的預設可用權限，
     並恢復 @app_commands.checks.has_permissions 作為執行期強制驗證。
     default_permissions 可被伺服器指令覆寫調整，不應單獨當作安全邊界。
  3. 限制於伺服器頻道，避免 DM 沒有 Manage Messages 語意時的權限繞過。

"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


async def _fetch_reference(
    channel: discord.TextChannel | discord.Thread,
    message_id: str | None,
) -> discord.Message | None:
    if not message_id:
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except (discord.NotFound, ValueError):
        return None


class Say(commands.Cog):
    """Bot 代發訊息指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="say", description="開啟 Bot 訊息發送面板")
    @app_commands.describe(
        content    = "直接發送純文字（留空則開啟整合面板）",
        image1     = "附件圖片 1（選填）",
        image2     = "附件圖片 2（選填）",
        image3     = "附件圖片 3（選填）",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(send_messages=True)
    async def cmd_say(
        self,
        interaction: discord.Interaction,
        content:     app_commands.Range[str, 1, 1950] | None = None,
        image1:      discord.Attachment | None   = None,
        image2:      discord.Attachment | None   = None,
        image3:      discord.Attachment | None   = None,
    ) -> None:
        attachments = tuple(img for img in (image1, image2, image3) if img is not None)
        if content is None:
            embed = discord.Embed(
                title="訊息發送面板",
                description=(
                    "請選擇發送方式：Bot 訊息、Webhook 自訂身分，或 Embed。\n"
                    "如有在 `/say` 附上檔案，會隨 Bot 訊息或 Webhook 一併發送。"
                ),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            await interaction.response.send_message(
                embed=embed,
                view=MessageSenderView(self, interaction.user.id, attachments),
                ephemeral=True,
            )
            return

        await self.send_plain(interaction, content, attachments=attachments)

    async def send_plain(
        self,
        interaction: discord.Interaction,
        content: str,
        image_url: str | None = None,
        message_id: str | None = None,
        attachments: tuple[discord.Attachment, ...] = (),
    ) -> None:
        channel   = interaction.channel
        if channel is None:
            await interaction.response.send_message("找不到可發送訊息的頻道。", ephemeral=True)
            return

        bot_member = interaction.guild.me
        bot_permissions = channel.permissions_for(bot_member)
        if attachments and not bot_permissions.attach_files:
            await interaction.response.send_message("Bot 沒有上傳附件的權限。", ephemeral=True)
            return
        if image_url and not bot_permissions.embed_links:
            await interaction.response.send_message("Bot 沒有嵌入連結的權限。", ephemeral=True)
            return

        reference = await _fetch_reference(channel, message_id)

        files = [
            await img.to_file()
            for img in attachments
        ]

        try:
            # Bot 代發時明確標記發起者，並且不讓使用者透過
            # Bot 繞過自己的 Mention Everyone 權限。
            permissions = channel.permissions_for(interaction.user)
            allowed_mentions = discord.AllowedMentions(
                everyone=permissions.mention_everyone,
                roles=permissions.mention_everyone,
                users=True,
                replied_user=True,
            )
            display_name = str(
                getattr(interaction.user, "display_name", None)
                or getattr(interaction.user, "name", None)
                or interaction.user.id
            )
            safe_display_name = discord.utils.escape_markdown(display_name)
            labelled_content = f"**{safe_display_name}**說：{content}"
            await channel.send(
                labelled_content,
                files=files,
                reference=reference,
                allowed_mentions=allowed_mentions,
            )

            if image_url:
                embed = discord.Embed()
                embed.set_image(url=image_url)
                await channel.send(embed=embed, reference=reference)

            await interaction.response.send_message("已發送。", ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message("Bot 沒有發送訊息的權限。", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("找不到指定的訊息 ID。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"錯誤：```{e}```", ephemeral=True)


class MessageSenderSelect(discord.ui.Select):
    def __init__(self, cog: Say, attachments: tuple[discord.Attachment, ...]) -> None:
        self.cog, self.attachments = cog, attachments
        super().__init__(placeholder="選擇發送方式", options=[
            discord.SelectOption(label="Bot 訊息", value="plain", description="以 Bot 身份發送文字、圖片或附件"),
            discord.SelectOption(label="Webhook 訊息", value="webhook", description="使用自訂名稱與頭像發送"),
            discord.SelectOption(label="Embed 訊息", value="embed", description="發送自訂嵌入式訊息"),
        ])

    async def callback(self, interaction: discord.Interaction) -> None:
        mode = self.values[0]
        if mode == "webhook":
            permissions = interaction.channel.permissions_for(interaction.user)
            bot_permissions = interaction.channel.permissions_for(interaction.guild.me)
            if not permissions.manage_webhooks:
                await interaction.response.send_message("你需要「管理 Webhook」權限。", ephemeral=True)
                return
            if not bot_permissions.manage_webhooks:
                await interaction.response.send_message("Bot 缺少「管理 Webhook」權限。", ephemeral=True)
                return
        if mode == "embed":
            bot_permissions = interaction.channel.permissions_for(interaction.guild.me)
            if not bot_permissions.embed_links:
                await interaction.response.send_message("Bot 缺少「嵌入連結」權限。", ephemeral=True)
                return
            await interaction.response.send_message(
                "使用下方按鈕分段編輯 Embed，完成後按「發送 Embed」。",
                view=EmbedComposerView(self.cog, interaction.user.id), ephemeral=True,
            )
            return
        await interaction.response.send_modal(MessageSenderModal(self.cog, mode, self.attachments))


class MessageSenderView(discord.ui.View):
    def __init__(self, cog: Say, user_id: int, attachments: tuple[discord.Attachment, ...]) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(MessageSenderSelect(cog, attachments))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的訊息發送面板。", ephemeral=True)
        return False


class MessageSenderModal(discord.ui.Modal):
    def __init__(self, cog: Say, mode: str, attachments: tuple[discord.Attachment, ...]) -> None:
        titles = {"plain": "發送 Bot 訊息", "webhook": "發送 Webhook 訊息", "embed": "發送 Embed 訊息"}
        super().__init__(title=titles[mode])
        self.cog, self.mode, self.attachments = cog, mode, attachments
        self.inputs: dict[str, discord.ui.TextInput] = {}

        def add(key: str, label: str, **kwargs: object) -> None:
            item = discord.ui.TextInput(label=label, **kwargs)
            self.inputs[key] = item
            self.add_item(item)

        if mode == "plain":
            add("content", "訊息內容", max_length=1950, style=discord.TextStyle.paragraph)
            add("image_url", "圖片 URL（選填）", required=False)
            add("message_id", "回覆訊息 ID（選填）", required=False, max_length=20)
        elif mode == "webhook":
            add("content", "訊息內容", max_length=1900, style=discord.TextStyle.paragraph)
            add("username", "顯示名稱（選填）", required=False, max_length=80)
            add("avatar_url", "頭像 URL（選填）", required=False)
            add("image_url", "圖片 URL（選填）", required=False)
            add("message_id", "引用訊息 ID（選填）", required=False, max_length=20)
        else:
            add("title", "標題（選填）", required=False, max_length=256)
            add("description", "內文", max_length=4000, style=discord.TextStyle.paragraph)
            add("color", "顏色（如 #FF5733 或 red）", required=False, max_length=30)
            add("image_url", "主要圖片 URL（選填）", required=False)
            add("footer", "頁腳文字（選填）", required=False, max_length=2048)

    def value(self, key: str) -> str | None:
        if key not in self.inputs:
            return None
        return str(self.inputs[key].value).strip() or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.mode == "plain":
            await self.cog.send_plain(
                interaction, self.value("content") or "", self.value("image_url"),
                self.value("message_id"), self.attachments,
            )
            return
        if self.mode == "webhook":
            webhook_cog = self.cog.bot.get_cog("WebhookSender")
            if webhook_cog is None:
                await interaction.response.send_message("Webhook 功能尚未載入。", ephemeral=True)
                return
            await webhook_cog.cmd_webhook.callback(
                webhook_cog, interaction, self.value("content") or "",
                self.value("username"), self.value("avatar_url"), self.value("image_url"),
                self.value("message_id"), *self.attachments, *([None] * (3 - len(self.attachments))),
            )
            return

        embed_cog = self.cog.bot.get_cog("EmbedBuilder")
        if embed_cog is None:
            await interaction.response.send_message("Embed 功能尚未載入。", ephemeral=True)
            return
        await embed_cog.cmd_embed.callback(
            embed_cog, interaction, title=self.value("title"),
            description=self.value("description"), color=self.value("color"),
            footer=self.value("footer"), image_url=self.value("image_url"),
        )


class EmbedComposerView(discord.ui.View):
    def __init__(self, cog: Say, user_id: int) -> None:
        super().__init__(timeout=300)
        self.cog, self.user_id = cog, user_id
        self.data: dict[str, str | None] = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的 Embed 編輯器。", ephemeral=True)
        return False

    @discord.ui.button(label="基本內容", style=discord.ButtonStyle.secondary)
    async def basic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmbedSectionModal(self, "basic"))

    @discord.ui.button(label="作者與頁腳", style=discord.ButtonStyle.secondary)
    async def attribution(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmbedSectionModal(self, "attribution"))

    @discord.ui.button(label="圖片與回覆", style=discord.ButtonStyle.secondary)
    async def media(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmbedSectionModal(self, "media"))

    @discord.ui.button(label="發送 Embed", style=discord.ButtonStyle.primary)
    async def send(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.data.get("title") and not self.data.get("description"):
            await interaction.response.send_message("請先填寫 Embed 標題或內文。", ephemeral=True)
            return
        embed_cog = self.cog.bot.get_cog("EmbedBuilder")
        if embed_cog is None:
            await interaction.response.send_message("Embed 功能尚未載入。", ephemeral=True)
            return
        await embed_cog.cmd_embed.callback(embed_cog, interaction, **self.data)
        self.stop()


class EmbedSectionModal(discord.ui.Modal):
    def __init__(self, composer: EmbedComposerView, section: str) -> None:
        titles = {"basic": "Embed 基本內容", "attribution": "Embed 作者與頁腳", "media": "Embed 圖片與回覆"}
        super().__init__(title=titles[section])
        self.composer, self.section = composer, section
        self.inputs: dict[str, discord.ui.TextInput] = {}

        def add(key: str, label: str, **kwargs: object) -> None:
            current = composer.data.get(key)
            if current:
                kwargs["default"] = current
            item = discord.ui.TextInput(label=label, required=False, **kwargs)
            self.inputs[key] = item
            self.add_item(item)

        if section == "basic":
            add("title", "標題", max_length=256)
            add("description", "內文", max_length=4000, style=discord.TextStyle.paragraph)
            add("color", "顏色（如 #FF5733 或 red）", max_length=30)
        elif section == "attribution":
            add("author", "作者名稱", max_length=256)
            add("author_icon", "作者圖示 URL")
            add("footer", "頁腳文字", max_length=2048)
            add("footer_icon", "頁腳圖示 URL")
        else:
            add("thumbnail", "縮圖 URL")
            add("image_url", "主要圖片 URL")
            add("message_id", "回覆訊息 ID", max_length=20)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        for key, item in self.inputs.items():
            self.composer.data[key] = str(item.value).strip() or None
        await interaction.response.send_message("已儲存這一段 Embed 設定。", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Say(bot))
