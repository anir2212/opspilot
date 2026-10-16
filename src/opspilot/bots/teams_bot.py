"""Microsoft Teams SDK bot with local conversation and internal delivery routes."""

from __future__ import annotations

import hmac
import os

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from microsoft_teams.apps import App, FastAPIAdapter
from pydantic import BaseModel
from sqlalchemy import select

from opspilot.bots.cards import teams_card
from opspilot.bots.chat_actions import acknowledge, draft_from_alert
from opspilot.storage import ChatThread, make_engine, session_factory


class Delivery(BaseModel):
    fingerprint: str
    result: dict


def create_bot(*, api_url: str | None = None, dev_unauthenticated: bool = False):
    """Create the bot; caller must await `teams.initialize()` before serving HTTP."""
    web = FastAPI(title="OpsPilot Teams bot")
    teams = App(
        http_server_adapter=FastAPIAdapter(app=web),
        dangerously_allow_unauthenticated_requests=dev_unauthenticated,
    )
    Session = session_factory(make_engine())
    api = api_url or os.getenv("OPSPILOT_API_URL", "http://127.0.0.1:18100")

    @teams.on_message
    async def on_message(ctx):
        text = (ctx.activity.text or "").strip()
        conversation = ctx.activity.conversation.id
        if text.lower().startswith("incident "):
            fingerprint = text.split(maxsplit=1)[1].strip()
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.get(f"{api}/api/triage/{fingerprint}")
                if response.status_code == 404:
                    await ctx.send("That incident thread was not found.")
                    return
                response.raise_for_status()
            state = response.json()["state"]
            with Session() as session:
                row = session.get(ChatThread, ("teams", fingerprint))
                if row is None:
                    row = ChatThread(platform="teams", fingerprint=fingerprint,
                                     channel_id=conversation, message_id=fingerprint)
                    session.add(row)
                else:
                    row.channel_id = conversation
                session.commit()
            await ctx.send(teams_card(fingerprint, state))
            return
        with Session() as session:
            row = session.scalar(select(ChatThread).where(
                ChatThread.platform == "teams", ChatThread.channel_id == conversation
            ))
            fingerprint = row.fingerprint if row else None
        if fingerprint is None:
            await ctx.send("Send `incident <fingerprint>` to inspect an OpsPilot thread.")
            return
        if len(text) < 3:
            return
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{api}/api/triage/{fingerprint}/followup", json={"question": text}
            )
            response.raise_for_status()
        await ctx.send(response.json()["answer"])

    async def card_action(ctx, kind: str):
        fingerprint = str(ctx.activity.value.action.data.get("thread", ""))
        actor = str(ctx.activity.from_.id)
        try:
            with Session() as session:
                if kind == "ack":
                    ok = acknowledge(session, "teams", fingerprint, actor)
                    message = "Incident acknowledged." if ok else "Unknown incident thread."
                else:
                    draft = draft_from_alert(session, fingerprint, kind)
                    message = (
                        f"Pending {kind} {draft['target']} ({draft['action_id']}). "
                        "Separate operator approval is required in the web console."
                    )
        except ValueError as exc:
            message = str(exc)
        await ctx.send(message)
        return {"statusCode": 200, "type": "application/vnd.microsoft.activity.message",
                "value": "Action processed"}

    @teams.on_card_action_execute("ack")
    async def on_ack(ctx):
        return await card_action(ctx, "ack")

    @teams.on_card_action_execute("ticket")
    async def on_ticket(ctx):
        return await card_action(ctx, "ticket")

    @teams.on_card_action_execute("change")
    async def on_change(ctx):
        return await card_action(ctx, "change")

    @web.post("/internal/deliver")
    async def deliver(payload: Delivery, token: str | None = Header(
        default=None, alias="X-Delivery-Token"
    )):
        expected = os.getenv("OPSPILOT_TEAMS_INTERNAL_TOKEN", "")
        conversation = os.getenv("OPSPILOT_TEAMS_CONVERSATION_ID", "")
        if not expected or token is None or not hmac.compare_digest(expected, token):
            raise HTTPException(403, "Internal delivery token is invalid")
        if not conversation or os.getenv("OPSPILOT_CHAT_DELIVERY") != "1":
            raise HTTPException(503, "Teams delivery is disabled or unconfigured")
        await teams.send(conversation, teams_card(payload.fingerprint, payload.result))
        with Session() as session:
            row = session.get(ChatThread, ("teams", payload.fingerprint))
            if row is None:
                session.add(ChatThread(platform="teams", fingerprint=payload.fingerprint,
                                       channel_id=conversation, message_id=payload.fingerprint))
            else:
                row.channel_id = conversation
            session.commit()
        return {"sent": True}

    return web, teams


def main() -> None:
    import asyncio

    async def run():
        dev = os.getenv("OPSPILOT_TEAMS_DEV_UNAUTH") == "1"
        host = "127.0.0.1" if dev else "0.0.0.0"
        web, teams = create_bot(dev_unauthenticated=dev)
        await teams.initialize()
        server = uvicorn.Server(uvicorn.Config(web, host=host, port=3978))
        await server.serve()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
