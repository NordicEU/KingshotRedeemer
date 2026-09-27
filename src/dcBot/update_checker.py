import aiohttp
import discord

from discord.ext import tasks
from typing import Dict, Any, Callable, List

from browser_automation.redeem import redeem_giftcode_for_all_players


GIFT_CODE_API = "https://kingshot.net/api/gift-codes"

# Errors where we should NOT permanently mark the code as processed,
# because trying again tomorrow could succeed.
RETRYABLE_STATUSES = {
    "REQUEST_ERROR",
    "HTTP_ERROR",
    "TOO FREQUENT",
    "TIMEOUT RETRY",
}


class UpdateChecker:
    """
    Checks Kingshot.net once every 24 hours for new gift codes.

    The class keeps the old UpdateChecker name so ksRedeemBot.py
    does not need to be changed.
    """

    def __init__(
        self,
        bot: discord.Client,
        bot_data: Dict[str, Any],
        save_data_func: Callable[[Dict[str, Any]], None],
    ):
        self.bot = bot
        self.bot_data = bot_data
        self.save_data = save_data_func

        self.check_updates.start()

    def unload(self):
        self.check_updates.cancel()

    async def fetch_active_codes(self) -> List[str]:
        """
        Fetch active gift codes from kingshot.net.
        """

        timeout = aiohttp.ClientTimeout(total=20)

        headers = {
            "User-Agent": "KingshotRedeemer/2.0",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
            ) as session:

                async with session.get(GIFT_CODE_API) as response:

                    if response.status != 200:
                        print(
                            f"❌ Gift-code API returned HTTP "
                            f"{response.status}"
                        )
                        return []

                    data = await response.json(content_type=None)

        except Exception as e:
            print(f"❌ Failed to check Kingshot gift codes: {e}")
            return []

        gift_codes = (
            data
            .get("data", {})
            .get("giftCodes", [])
        )

        codes = []

        for item in gift_codes:
            code = item.get("code")

            if code:
                code = str(code).strip()

                if code and code not in codes:
                    codes.append(code)

        return codes

    def has_retryable_failure(
        self,
        results: List[Dict[str, Any]],
    ) -> bool:
        """
        Return True if at least one redemption failed for a reason
        where trying again later may work.
        """

        for item in results:

            if item.get("success"):
                continue

            result = item.get("result", {})

            status = (
                result.get("status")
                or item.get("errorCode")
                or ""
            )

            if status in RETRYABLE_STATUSES:
                return True

            message = str(
                result.get("message", "")
            ).lower()

            if any(
                text in message
                for text in (
                    "rate limit",
                    "server busy",
                    "timeout",
                    "timed out",
                    "connection",
                    "http error",
                    "request error",
                )
            ):
                return True

        return False

    def build_result_message(
        self,
        code: str,
        results: List[Dict[str, Any]],
    ) -> str:

        success_count = sum(
            1
            for item in results
            if item.get("success")
        )

        message = (
            f"🎁 **New Kingshot Gift Code Found!**\n"
            f"Code: `{code}`\n\n"
            f"🚀 `{success_count}/{len(results)}` "
            f"players processed successfully."
        )

        failures = []

        for item in results:

            if item.get("success"):
                continue

            player_id = item.get(
                "player_id",
                "Unknown",
            )

            kingdom_id = item.get(
                "kingdom_id",
                "Unknown",
            )

            result = item.get("result", {})

            error_message = (
                result.get("message")
                or item.get("message")
                or item.get("errorCode")
                or "Unknown error"
            )

            failures.append(
                f"❌ K{kingdom_id} `{player_id}`: "
                f"{error_message}"
            )

        if failures:

            message += (
                "\n\n**Failures:**\n"
                + "\n".join(failures)
            )

        if len(message) > 1900:
            message = (
                message[:1900]
                + "\n…(truncated)"
            )

        return message

    @tasks.loop(hours=24)
    async def check_updates(self):

        print(
            "🎁 Checking Kingshot.net "
            "for new gift codes..."
        )

        active_codes = await self.fetch_active_codes()

        if not active_codes:
            print(
                "ℹ️ No gift codes returned "
                "from Kingshot.net."
            )
            return

        print(
            f"ℹ️ Active codes: "
            f"{', '.join(active_codes)}"
        )

        # Keep the list inside botData.json so it survives
        # Docker/container restarts.
        if "botConfig" not in self.bot_data:
            self.bot_data["botConfig"] = {}

        config = self.bot_data["botConfig"]

        seen_codes = set(
            config.get(
                "seen_gift_codes",
                [],
            )
        )

        new_codes = [
            code
            for code in active_codes
            if code not in seen_codes
        ]

        if not new_codes:
            print("✅ No new gift codes found.")
            return

        print(
            f"🎁 New gift codes found: "
            f"{', '.join(new_codes)}"
        )

        players = self.bot_data.get(
            "players",
            [],
        )

        if not players:
            print(
                "⚠️ New codes found, but there "
                "are no registered players."
            )

            # Do NOT mark them as seen.
            # If a player is added before tomorrow,
            # the bot can still redeem them.
            return

        channel_id = config.get(
            "allowed_channel"
        )

        channel = None

        if channel_id:
            channel = self.bot.get_channel(
                channel_id
            )

        for code in new_codes:

            print(
                f"🚀 Automatically redeeming "
                f"gift code: {code}"
            )

            try:
                results = (
                    await redeem_giftcode_for_all_players(
                        players,
                        code,
                    )
                )

            except Exception as e:
                print(
                    f"❌ Automatic redemption "
                    f"failed for {code}: {e}"
                )

                # Don't mark it seen.
                # It'll retry tomorrow.
                continue

            if channel:
                try:
                    await channel.send(
                        self.build_result_message(
                            code,
                            results,
                        )
                    )

                except discord.Forbidden:
                    print(
                        "❌ Bot cannot send "
                        "auto-redeem result "
                        "to configured channel."
                    )

                except Exception as e:
                    print(
                        f"❌ Failed to send "
                        f"Discord result: {e}"
                    )

            if self.has_retryable_failure(
                results
            ):
                print(
                    f"⚠️ {code} had temporary "
                    f"failures. It will be "
                    f"retried next check."
                )

                continue

            # Everything was either successful or a permanent
            # response such as already claimed / wrong kingdom.
            seen_codes.add(code)

            config["seen_gift_codes"] = sorted(
                seen_codes
            )

            self.save_data(
                self.bot_data
            )

            print(
                f"✅ Finished automatic "
                f"redemption for {code}"
            )

    @check_updates.before_loop
    async def before_check_updates(self):
        await self.bot.wait_until_ready()
