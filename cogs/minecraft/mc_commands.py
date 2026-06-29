"""
cogs/minecraft/mc_commands.py

職責：
- Minecraft 工具指令群組（/mc pearl）
- /mc pearl：珍珠炮計算機
  輸入 84gt 珍珠座標 + 目標 XZ + 地面高度
  輸出：前 10 個方案的 embed（含代碼、tick、落點、誤差）

Modification():

- 移植自 Bot-Firefly/cogs/minecraft/mc_commands.py
- 消除全域狀態依賴：改呼叫純函式 pearl_calculator.calculate()
- 改用 Embed + CodeBlock 呈現結果，分頁顯示
- 新增完整錯誤處理與輸入驗證
- 支援 DM、私人頻道（allowed_contexts 保留）

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.minecraft.pearl_calculator import calculate, PearlResult
from core.system.settings import get

log = logging.getLogger("bot.minecraft")


# ── embed 工廠 ──────────────────────

def _pearl_embed(
    results:       list[PearlResult],
    projected_pos: list[float],
    dest_x:        float,
    dest_z:        float,
    ground_height: float,
) -> discord.Embed:
    """將計算結果格式化為 Discord Embed。"""
    footer = get("embed_footer.default", "Firefly Bot")

    if not results:
        embed = discord.Embed(
            title       = "珍珠炮計算機",
            description = (
                "找不到有效方案。\n"
                "請確認座標是否正確，或嘗試調整地面高度。"
            ),
            color       = discord.Color.red(),
        )
        embed.set_footer(text=footer)
        return embed

    lines = []
    for r in results:
        lines.append(
            f"[{r.rank:>2}] tick: {r.fly_ticks} (+84={r.total_tick})\n"
            f"     code: {r.code}\n"
            f"     落點: ({r.land_x:.2f}, {r.land_y:.2f}, {r.land_z:.2f})\n"
            f"     誤差: {r.error:.4f} 格"
        )

    embed = discord.Embed(
        title = "珍珠炮計算結果",
        color = discord.Color.green(),
    )
    embed.add_field(
        name  = "輸入參數",
        value = (
            f"投射點：`({projected_pos[0]}, {projected_pos[1]}, {projected_pos[2]})`\n"
            f"目標：`(X={dest_x}, Z={dest_z})`\n"
            f"地面高度：`{ground_height}`"
        ),
        inline=False,
    )
    embed.add_field(
        name  = f"前 {len(results)} 個方案（依誤差排序）",
        value = f"```\n{chr(10).join(lines)}\n```",
        inline=False,
    )
    embed.add_field(
        name  = "如何讀取 code",
        value = (
            "`code: 0 1 0 0  10  0 0 1 0 0 0 1 1`\n"
            "前半（8 bit 反向）= n 值　│　方向位元（N=00 W=01 E=10 S=11）　│　後半（8 bit）= m 值\n"
            "每個位元對應 TNT 數：1 2 3 4 10 20 40 80"
        ),
        inline=False,
    )
    embed.set_footer(text=f"計算誤差單位為水平距離（格）　|　{footer}")
    return embed


# ── Cog ──────────────────────

class Minecraft(commands.Cog):
    """Minecraft 工具指令群組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    mc_group = app_commands.Group(
        name        = "mc",
        description = "Minecraft 工具",
    )

    # ── /mc pearl ──────────────────────

    @mc_group.command(name="pearl", description="珍珠炮計算機：計算最佳 TNT 配置")
    @app_commands.describe(
        px            = "84gt 珍珠投射點 X 座標",
        py            = "84gt 珍珠投射點 Y 座標",
        pz            = "84gt 珍珠投射點 Z 座標",
        dest_x        = "目標 X 座標",
        dest_z        = "目標 Z 座標",
        ground_height = "地面高度（預設 128）",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_pearl(
        self,
        interaction:   discord.Interaction,
        px:            float,
        py:            float,
        pz:            float,
        dest_x:        float,
        dest_z:        float,
        ground_height: float = 128.0,
    ) -> None:
        """
        計算珍珠炮配置。

        輸入 84gt 時的珍珠座標，以及目標 XZ 座標，
        回傳誤差最小的前 10 個 TNT 配置方案。
        """
        await interaction.response.defer()

        # ── 輸入驗證 ──────────────────────
        if ground_height < -64 or ground_height > 320:
            await interaction.followup.send(
                "地面高度必須在 -64 到 320 之間（Minecraft 世界高度範圍）",
                ephemeral=True,
            )
            return

        if py <= ground_height:
            await interaction.followup.send(
                f"珍珠 Y 座標（{py}）不能低於或等於地面高度（{ground_height}）",
                ephemeral=True,
            )
            return

        # ── 執行計算 ──────────────────────
        try:
            results = calculate(
                projected_pos = [px, py, pz],
                dest_x        = dest_x,
                dest_z        = dest_z,
                ground_height = ground_height,
                top_n         = 10,
            )
        except Exception as exc:
            log.exception("[mc.pearl] 計算錯誤 px=%s py=%s pz=%s dx=%s dz=%s", px, py, pz, dest_x, dest_z)
            await interaction.followup.send(
                f"計算過程發生錯誤：{exc}",
                ephemeral=True,
            )
            return

        embed = _pearl_embed(
            results        = results,
            projected_pos  = [px, py, pz],
            dest_x         = dest_x,
            dest_z         = dest_z,
            ground_height  = ground_height,
        )
        await interaction.followup.send(embed=embed)

        log.info(
            "[mc.pearl] 計算完成 proj=(%s,%s,%s) dest=(%s,%s) gh=%s → %d 個方案",
            px, py, pz, dest_x, dest_z, ground_height, len(results),
        )


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Minecraft(bot))
