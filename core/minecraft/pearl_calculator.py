"""
core/minecraft/pearl_calculator.py

職責：
- 計算 Minecraft 珍珠炮（Pearl Cannon）最佳 TNT 配置
- 輸入：84gt 珍珠投射點座標、目標座標、地面高度
- 輸出：前 10 個誤差最小的發射方案（含 tick、代碼、落點、誤差）
- 純函式設計：無任何模組層級可變狀態，並發安全

原始設計說明（珍珠炮物理）：
- 珍珠在 84gt 被 TNT 推出後具有初始速度 projectedMotion
- TNT 爆炸提供額外的 XZ/Y 動量（由 m、n 組合決定）
- 每個 tick 依 Minecraft 物理：v *= f（空氣阻力），vy = (vy - g) * f
- 模擬 fly_tick_num 個 tick 後，落點若低於地面高度則失效
- 遍歷 fly_tick_num（1 開始）直到找到有效方案，
  m/n 超過 ±160 時遞增繼續搜尋（超出 TNT 可達範圍）

Modification():

- 移植自 Bot-Firefly/core/minecraft/mc_pearl_calculator.py
- 消除全域可變狀態（mc_pearl_config）：所有參數改為函式參數
- 提取 _simulate() 內部函式增加可讀性
- 新增 PearlResult dataclass 供 embed 格式化使用
- 無解時回傳空清單，不拋出例外

"""

from __future__ import annotations

import struct
from dataclasses import dataclass


# ── 物理常數（Minecraft 1.8+ 不變） ──────────────────────

_GRAVITY: float        = 0.03
_AIR_RESIST: float     = 0.98999994993209839   # float32(0.99) 的 float64 表示
_ONE_TNT_XZ: float     = 0.6026793588895138
_ONE_TNT_Y: float      = 0.004435058914919521
_PEARL_INIT_MOTION_Y:  float = -0.340740225070415   # 84gt 珍珠的初始 Y 動量

_MAX_MN: int = 160   # m / n 超過此值代表超出 TNT 射程，跳過

_DIRECTION_BITS: dict[str, str] = {
    "N": "00", "W": "01", "E": "10", "S": "11",
}


# ── 結果資料模型 ──────────────────────

@dataclass(frozen=True)
class PearlResult:
    """
    單一珍珠炮方案。

    Attributes:
        rank:       排名（1 = 誤差最小）
        fly_ticks:  珍珠飛行 tick 數（不含發射前 84gt）
        total_tick: fly_ticks + 84
        code:       TNT 配置代碼字串（二進位顯示）
        land_x:     落點 X 座標
        land_y:     落點 Y 座標
        land_z:     落點 Z 座標
        error:      落點與目標的水平直線距離（越小越準）
    """
    rank:       int
    fly_ticks:  int
    total_tick: int
    code:       str
    land_x:     float
    land_y:     float
    land_z:     float
    error:      float


# ── 工具函式 ──────────────────────

def _float32(val: float) -> float:
    """模擬 Java float 精度（Minecraft 內部使用 float32）。"""
    packed = struct.pack("!f", val)
    return struct.unpack("!f", packed)[0]


def _to_bits(num: int) -> str:
    """
    將整數轉換為 8-bit 表示（TNT 代碼格式）。
    位元對應 TNT 數量：80、40、20、10、4、3、2、1。
    """
    weights = [80, 40, 20, 10, 4, 3, 2, 1]
    bits    = []
    n       = abs(num)
    for w in weights:
        if n >= w:
            bits.append("1")
            n -= w
        else:
            bits.append("0")
    b = "".join(bits)
    return f"{b[:4]} {b[4:]}"


# ── 核心計算 ──────────────────────

def _simulate(
    projected_pos:  list[float],
    init_motion:    tuple[float, float, float],
    fly_ticks:      int,
    ground_height:  float,
    dest_x:         float,
    dest_z:         float,
) -> PearlResult | None:
    """
    模擬珍珠飛行，回傳結果；落地點高於地面高度（無效）則回傳 None。
    """
    px, py, pz = projected_pos
    mx, my, mz = init_motion

    for _ in range(fly_ticks):
        mx *= _AIR_RESIST
        my  = (my - _GRAVITY) * _AIR_RESIST
        mz *= _AIR_RESIST
        px += mx
        py += my
        pz += mz

    if py > ground_height:
        return None   # 落點仍在地面以上，此方案無效

    error = ((px - dest_x) ** 2 + (pz - dest_z) ** 2) ** 0.5
    # rank 暫為 0，由呼叫方排序後賦值
    return PearlResult(
        rank       = 0,
        fly_ticks  = fly_ticks,
        total_tick = fly_ticks + 84,
        code       = "",     # 由呼叫方填入
        land_x     = px,
        land_y     = py,
        land_z     = pz,
        error      = error,
    )


def calculate(
    projected_pos:  list[float],
    dest_x:         float,
    dest_z:         float,
    ground_height:  float = 128.0,
    top_n:          int   = 10,
) -> list[PearlResult]:
    """
    計算珍珠炮方案，回傳誤差最小的前 top_n 個結果。

    Args:
        projected_pos:  [px, py, pz]，84gt 時的珍珠位置
        dest_x:         目標 X 座標
        dest_z:         目標 Z 座標
        ground_height:  地面高度（預設 128）
        top_n:          回傳方案數上限（預設 10）

    Returns:
        排序後的 PearlResult 清單（空清單表示無有效方案）
    """
    dx     = dest_x - projected_pos[0]
    dz     = dest_z - projected_pos[2]

    # 決定主方向
    if abs(dx) >= abs(dz):
        direction = "E" if dx > 0 else "W"
    else:
        direction = "S" if dz > 0 else "N"

    dir_bits = _DIRECTION_BITS[direction]
    f        = _float32(0.99)   # 確保與 Minecraft Java 精度一致

    candidates: list[tuple[float, int, int, int, str]] = []
    fly_ticks  = 1

    while True:
        # 計算運動係數 kp
        kp = 2 * _ONE_TNT_XZ * ((f - f ** (fly_ticks + 1)) / (1 - f))

        # 依方向計算 m、n
        if direction in ("N", "S"):
            m = round((dx + dz) / kp)
            n = round((dz - dx) / kp)
            if direction == "N":
                m, n = n, m
            motion_x = (abs(m) - abs(n)) * _ONE_TNT_XZ
            motion_z = (m + n)           * _ONE_TNT_XZ
        else:
            m = round((dx + dz) / kp)
            n = round((dx - dz) / kp)
            if direction == "W":
                m, n = n, m
            motion_x = (m + n)           * _ONE_TNT_XZ
            motion_z = (abs(m) - abs(n)) * _ONE_TNT_XZ

        motion_y = abs(m + n) * _ONE_TNT_Y + _PEARL_INIT_MOTION_Y

        # m / n 超出 TNT 可達範圍 → 搜尋下一個 tick
        if abs(m) > _MAX_MN or abs(n) > _MAX_MN:
            fly_ticks += 1
            if fly_ticks > 200:   # 硬性上限，防止無限迴圈
                break
            continue

        result = _simulate(
            projected_pos  = list(projected_pos),
            init_motion    = (motion_x, motion_y, motion_z),
            fly_ticks      = fly_ticks,
            ground_height  = ground_height,
            dest_x         = dest_x,
            dest_z         = dest_z,
        )

        if result is not None:
            # 組裝代碼字串：n bits（反向）+ 方向 + m bits
            n_bits = _to_bits(abs(n))[::-1]
            m_bits = _to_bits(abs(m))
            code   = f"{n_bits} {dir_bits} {m_bits}"
            candidates.append((result.error, fly_ticks, m, n, code))

        fly_ticks += 1
        if fly_ticks > 200:
            break

    # 按誤差排序，取前 top_n
    candidates.sort(key=lambda x: x[0])

    results: list[PearlResult] = []
    for rank, (error, ft, m, n, code) in enumerate(candidates[:top_n], start=1):
        kp   = 2 * _ONE_TNT_XZ * ((f - f ** (ft + 1)) / (1 - f))
        dx_r = dest_x - projected_pos[0]
        dz_r = dest_z - projected_pos[2]

        if direction in ("N", "S"):
            mx = (abs(m) - abs(n)) * _ONE_TNT_XZ
            mz = (m + n)           * _ONE_TNT_XZ
        else:
            mx = (m + n)           * _ONE_TNT_XZ
            mz = (abs(m) - abs(n)) * _ONE_TNT_XZ
        my = abs(m + n) * _ONE_TNT_Y + _PEARL_INIT_MOTION_Y

        # 重新模擬取落點
        px, py, pz = projected_pos
        for _ in range(ft):
            mx *= f; my = (my - _GRAVITY) * f; mz *= f
            px += mx; py += my; pz += mz

        results.append(PearlResult(
            rank       = rank,
            fly_ticks  = ft,
            total_tick = ft + 84,
            code       = code,
            land_x     = px,
            land_y     = py,
            land_z     = pz,
            error      = error,
        ))

    return results
