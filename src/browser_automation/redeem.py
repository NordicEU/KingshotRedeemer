import asyncio
import hashlib
import time
from typing import Any, Dict, List

import aiohttp


BASE_URL = "https://kingshot-giftcode.centurygame.com"
REDEEM_URL = f"{BASE_URL}/api/gift_code"

KS_ENCRYPT_KEY = "mN4!pQs6JrYwV9"


RESULT_MESSAGES = {
    "SUCCESS": "Successfully redeemed",
    "RECEIVED": "Already redeemed",
    "SAME TYPE EXCHANGE": "Successfully redeemed",
    "TIME ERROR": "Gift code has expired",
    "CDK NOT FOUND": "Gift code not found or incorrect",
    "USED": "Gift code claim limit reached",
    "TIMEOUT RETRY": "Server requested a retry",
    "TOO FREQUENT": "Rate limited for this player",
    "USER INFO ERROR": "Wrong Kingdom ID for this player",
    "ROLE NOT EXIST": "Player does not exist",
}


def sign_payload(data: Dict[str, str]) -> Dict[str, str]:
    encoded = "&".join(
        f"{key}={data[key]}"
        for key in sorted(data.keys())
    )

    sign = hashlib.md5(
        f"{encoded}{KS_ENCRYPT_KEY}".encode()
    ).hexdigest()

    return {
        "sign": sign,
        **data,
    }


def classify_response(data: Dict[str, Any]) -> str:
    msg = str(data.get("msg", "Unknown error")).strip(".")
    err_code = data.get("err_code")

    if msg == "SUCCESS":
        return "SUCCESS"

    if msg == "RECEIVED" and err_code == 40008:
        return "RECEIVED"

    if msg == "SAME TYPE EXCHANGE" and err_code == 40011:
        return "SAME TYPE EXCHANGE"

    if msg == "TIME ERROR" and err_code == 40007:
        return "TIME ERROR"

    if msg == "CDK NOT FOUND" and err_code == 40014:
        return "CDK NOT FOUND"

    if msg == "USED" and err_code == 40005:
        return "USED"

    if msg == "TIMEOUT RETRY" and err_code == 40004:
        return "TIMEOUT RETRY"

    if msg == "TOO FREQUENT" and err_code == 40019:
        return "TOO FREQUENT"

    if msg == "USER INFO ERROR" and err_code == 40020:
        return "USER INFO ERROR"

    if err_code == 40001 and "not exist" in msg.lower():
        return "ROLE NOT EXIST"

    return msg


async def perform_giftcode_redeem(
    player_id: str,
    kingdom_id: str,
    gift_code: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:

    payload = sign_payload({
        "fid": str(player_id),
        "cdk": gift_code,
        "kid": str(kingdom_id),
        "time": str(int(time.time())),
    })

    try:
        async with session.post(
            REDEEM_URL,
            data=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:

            if response.status == 429:
                return {
                    "success": False,
                    "status": "TOO FREQUENT",
                    "message": "Rate limited for this player.",
                }

            if response.status != 200:
                return {
                    "success": False,
                    "status": "HTTP_ERROR",
                    "message": f"Kingshot returned HTTP {response.status}",
                }

            data = await response.json(content_type=None)
            status = classify_response(data)

            return {
                "success": status in (
                    "SUCCESS",
                    "SAME TYPE EXCHANGE",
                    "RECEIVED",
                ),
                "status": status,
                "message": RESULT_MESSAGES.get(status, status),
            }

    except Exception as e:
        return {
            "success": False,
            "status": "REQUEST_ERROR",
            "message": str(e),
        }


async def redeem_giftcode_for_all_players(
    players: List[Dict[str, str]],
    gift_code: str,
) -> List[Dict[str, Any]]:

    results: List[Dict[str, Any]] = []

    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/",
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/152.0.0.0 Safari/537.36"
        ),
    }

    async with aiohttp.ClientSession(headers=headers) as session:

        for player in players:
            player_id = str(player.get("player_id", ""))
            kingdom_id = str(player.get("kingdom_id", ""))
            stored_nick = player.get("player_nick")

            if not player_id:
                continue

            if not kingdom_id:
                results.append({
                    "player_id": player_id,
                    "kingdom_id": kingdom_id,
                    "stored_player_nick": stored_nick,
                    "success": False,
                    "result": {
                        "message": "Missing Kingdom ID.",
                    },
                })
                continue

            result = await perform_giftcode_redeem(
                player_id,
                kingdom_id,
                gift_code,
                session,
            )

            status = result.get("status")

            results.append({
                "player_id": player_id,
                "kingdom_id": kingdom_id,
                "stored_player_nick": stored_nick,
                "success": result.get("success", False),
                "result": result,
            })

            # These failures apply to the gift code itself,
            # so there is no reason to try every player.
            if status in (
                "TIME ERROR",
                "CDK NOT FOUND",
                "USED",
            ):
                break

            await asyncio.sleep(1)

    return results