"""YouTube OAuth 2.0 authentication and 7-day testing mode token manager."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import google.auth.transport.requests
import google.oauth2.credentials
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from clipforge.core.config import CREDENTIALS_DIR, DEFAULT_YOUTUBE_CLIENT_SECRET_FILE
from clipforge.core.db import Database
from clipforge.core.models import Account, AccountStatus, Channel, Platform

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",
]


class YouTubeAuthError(Exception):
    """Base error for YouTube OAuth failures."""
    pass


class YouTubeTokenExpiredError(YouTubeAuthError):
    """Raised when token has expired (e.g. 7-day Testing mode expiration) and needs re-authentication."""
    pass


class YouTubeAuthManager:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def start_oauth_flow(
        client_secrets_path: Path | str = DEFAULT_YOUTUBE_CLIENT_SECRET_FILE,
        port: int = 8080,
    ) -> Tuple[Credentials, Dict[str, Any]]:
        """
        Run the interactive OAuth 2.0 flow via local loopback webserver.
        Returns: (Credentials, channel_profile_info)
        """
        secret_file = Path(client_secrets_path)
        if not secret_file.exists():
            raise FileNotFoundError(
                f"Google Client Secret file not found at: {secret_file}.\n"
                f"Please download your client_secret_*.json from Google Cloud Console and place it at {secret_file}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret_file),
            scopes=YOUTUBE_SCOPES,
        )

        try:
            creds = flow.run_local_server(
                port=port,
                prompt="consent",
                access_type="offline",
                open_browser=True,
            )
        except Exception as e:
            raise YouTubeAuthError(f"OAuth loopback authorization failed: {e}") from e

        # Build service to query channel profile info
        service = build("youtube", "v3", credentials=creds)
        channels_resp = service.channels().list(mine=True, part="snippet,contentDetails").execute()

        items = channels_resp.get("items", [])
        if not items:
            raise YouTubeAuthError("No YouTube channel found associated with this Google Account.")

        channel_info = items[0]
        snippet = channel_info.get("snippet", {})

        profile = {
            "channel_id": channel_info.get("id"),
            "title": snippet.get("title"),
            "custom_url": snippet.get("customUrl"),
            "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
            "description": snippet.get("description"),
        }

        return creds, profile

    def register_account(
        self,
        credentials: Credentials,
        profile: Dict[str, Any],
        is_testing_mode: bool = True,
        account_id: Optional[str] = None,
    ) -> Account:
        """
        Save the authenticated YouTube account into SQLite.
        In GCP Testing Mode, refresh tokens expire in 7 days.
        """
        now = datetime.now(timezone.utc)
        a_id = account_id or f"yt_{profile.get('channel_id')}"
        channel_title = profile.get("title") or "YouTube Channel"

        # Calculate token expiration threshold
        # In Testing mode, Google invalidates user tokens after 7 days
        token_expires_at = now + timedelta(days=7) if is_testing_mode else None

        creds_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        account = Account(
            id=a_id,
            platform=Platform.YOUTUBE,
            name=channel_title,
            status=AccountStatus.CONNECTED,
            credentials_json=json.dumps(creds_data),
            token_expires_at=token_expires_at,
            connected_at=now,
            updated_at=now,
        )
        self.db.save_account(account)

        # Also register own channel in channels table
        channel = Channel(
            id=f"chan_{profile.get('channel_id')}",
            account_id=a_id,
            platform=Platform.YOUTUBE,
            external_id=profile.get("channel_id") or a_id,
            name=channel_title,
            url=f"https://www.youtube.com/channel/{profile.get('channel_id')}",
            created_at=now,
        )
        self.db.save_channel(channel)

        return account

    def get_service(self, account_id: str) -> Resource:
        """
        Load stored credentials, verify token health and build YouTube API v3 client.
        Raises YouTubeTokenExpiredError if token is expired or refresh fails.
        """
        account = self.db.get_account(account_id)
        if not account:
            raise YouTubeAuthError(f"Account {account_id} not found in database.")

        if not account.credentials_json:
            raise YouTubeAuthError(f"Account {account_id} has no credentials stored.")

        # Check 7-day expiration status
        health = self.check_token_health(account)
        if health["is_expired"]:
            account.status = AccountStatus.EXPIRED
            self.db.save_account(account)
            raise YouTubeTokenExpiredError(
                f"YouTube account '{account.name}' token has expired ({health['reason']}). "
                "Please reconnect your account in Clip Forge."
            )

        creds_data = json.loads(account.credentials_json)
        credentials = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes"),
        )

        # Refresh token if needed
        if credentials.expired and credentials.refresh_token:
            try:
                request = google.auth.transport.requests.Request()
                credentials.refresh(request)
                
                # Update saved access token
                creds_data["token"] = credentials.token
                account.credentials_json = json.dumps(creds_data)
                account.status = AccountStatus.CONNECTED
                self.db.save_account(account)
            except Exception as e:
                account.status = AccountStatus.EXPIRED
                self.db.save_account(account)
                raise YouTubeTokenExpiredError(
                    f"Failed to refresh YouTube access token: {e}. Please re-authenticate."
                ) from e

        return build("youtube", "v3", credentials=credentials)

    @staticmethod
    def check_token_health(account: Account) -> Dict[str, Any]:
        """
        Evaluate token status and days remaining before expiration.
        """
        now = datetime.now(timezone.utc)
        if account.status == AccountStatus.EXPIRED:
            return {"is_expired": True, "days_remaining": 0, "reason": "Account marked as expired"}

        if account.token_expires_at:
            delta = account.token_expires_at - now
            days_left = round(delta.total_seconds() / 86400.0, 1)
            if days_left <= 0:
                return {
                    "is_expired": True,
                    "days_remaining": 0,
                    "reason": "7-day Testing mode token expired",
                }
            elif days_left <= 1.0:
                return {
                    "is_expired": False,
                    "warning": True,
                    "days_remaining": days_left,
                    "reason": f"Token will expire in {days_left} days. Reconnect soon.",
                }
            else:
                return {
                    "is_expired": False,
                    "warning": False,
                    "days_remaining": days_left,
                    "reason": "Token valid",
                }

        return {"is_expired": False, "days_remaining": None, "reason": "No expiration set"}
