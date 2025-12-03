import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    bot_token: str
    sheet_id: str
    sheet_gid: str
    service_account_info: dict
    timezone: str = "Europe/Kyiv"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        bot_token = os.getenv("BOT_TOKEN")
        sheet_id = os.getenv("GSHEET_ID")
        sheet_gid = os.getenv("GSHEET_GID")
        raw_service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN env variable is missing")
        if not sheet_id:
            raise RuntimeError("GSHEET_ID env variable is missing")
        if not sheet_gid:
            raise RuntimeError("GSHEET_GID env variable is missing")
        if not raw_service_account:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env variable is missing")
        service_account = json.loads(raw_service_account)
        private_key = service_account.get("private_key")
        if isinstance(private_key, str):
            service_account["private_key"] = private_key.replace("\\n", "\n")
        return cls(
            bot_token=bot_token,
            sheet_id=sheet_id,
            sheet_gid=sheet_gid,
            service_account_info=service_account,
        )
