"""key_pricing.py — Key pricing configuration & calculator.
Default price for any duration is 4 points.
Admin can manually customize prices for any duration (e.g. 1h: 4, 30d: 50, etc.).
Cost scales proportionally with device/user count (device * price).
"""
import database as db
import html
from telegram import Update
from telegram.ext import ContextTypes
from helpers import is_admin, HTML_EMOJI

# Default price for 1 device of any duration unless manually overridden
DEFAULT_PRICE = 4


async def get_all_prices() -> dict[str, int]:
    """Fetch current manually customized pricing map from database."""
    raw = await db.get_setting("key_pricing_map")
    if not raw:
        return {}
    pricing = {}
    for part in raw.split(";"):
        if ":" in part:
            dur, cost = part.split(":", 1)
            dur = dur.strip().lower()
            if cost.strip().isdigit():
                pricing[dur] = int(cost.strip())
    return pricing


async def set_price_for_duration(duration_label: str, price: int):
    """Admin sets custom price for a duration (e.g. '1h' -> 4, '30d' -> 50)."""
    pricing = await get_all_prices()
    pricing[duration_label.strip().lower()] = price
    serialized = ";".join(f"{k}:{v}" for k, v in pricing.items())
    await db.set_setting("key_pricing_map", serialized)


async def remove_custom_price(duration_label: str):
    """Remove custom price so it falls back to default (4 points)."""
    pricing = await get_all_prices()
    dur = duration_label.strip().lower()
    if dur in pricing:
        del pricing[dur]
        serialized = ";".join(f"{k}:{v}" for k, v in pricing.items())
        await db.set_setting("key_pricing_map", serialized)


async def calculate_key_cost(duration_label: str, devices: int = 1) -> tuple[int, int]:
    """Calculate total points cost for generating a key.
    
    Returns (unit_price, total_cost)
    Example:
      - Unit price: 4
      - Devices: 10
      - Total cost: 4 * 10 = 40
    """
    pricing = await get_all_prices()
    lbl = duration_label.strip().lower()
    unit_price = pricing.get(lbl, DEFAULT_PRICE)
    dev_count = max(1, devices)
    total_cost = unit_price * dev_count
    return unit_price, total_cost


# ── Commands for Admin to View & Manage Pricing ────────────

async def setkeyprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /setkeyprice <duration> <points>
    Example: /setkeyprice 1h 4
             /setkeyprice 30d 50
    """
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/setkeyprice &lt;duration&gt; &lt;points&gt;</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            f"• <code>/setkeyprice 1h 4</code> (1h key will cost 4 points per device)\n"
            f"• <code>/setkeyprice 30d 60</code> (30d key will cost 60 points per device)\n"
            f"• <code>/keyprices</code> — View all configured prices.",
            parse_mode="HTML"
        )

    duration_label = args[0].strip().lower()
    if not args[1].isdigit() or int(args[1]) < 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Points must be a non-negative number.", parse_mode="HTML")

    price = int(args[1])
    await set_price_for_duration(duration_label, price)

    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Key Price Updated!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Duration:</b> <code>{html.escape(duration_label)}</code>\n"
        f"{HTML_EMOJI.CREDIT} <b>Price per Device:</b> <code>{price}</code> points\n"
        f"{HTML_EMOJI.TIP} <i>1 device = {price} pts, 10 devices = {price * 10} pts.</i>",
        parse_mode="HTML"
    )


async def keyprices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /keyprices — view all custom prices and default price."""
    prices = await get_all_prices()
    lines = [
        f"{HTML_EMOJI.CREDIT} <b>KEY PRICING CONFIGURATION</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{HTML_EMOJI.DELVORN} <b>Default Base Price:</b> <code>{DEFAULT_PRICE}</code> points per device (for any unlisted duration)\n",
        f"{HTML_EMOJI.STATS} <b>Custom Set Prices:</b>"
    ]
    if not prices:
        lines.append("<i>(No custom prices set yet. All durations cost default 4 points per device.)</i>")
    else:
        for dur, pr in sorted(prices.items()):
            lines.append(f"• <code>{html.escape(dur)}</code> {HTML_EMOJI.ARROW_RIGHT} <code>{pr}</code> points / device")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"{HTML_EMOJI.SETTINGS} <b>Change price:</b> <code>/setkeyprice &lt;duration&gt; &lt;points&gt;</code>")
    lines.append(f"{HTML_EMOJI.STATS} <b>Calculation:</b> <code>Cost = price * devices</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
