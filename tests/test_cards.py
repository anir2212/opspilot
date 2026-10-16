"""Both chat formats preserve citations and expose no direct approval action."""

from opspilot.bots.cards import slack_message, teams_card


def test_chat_payloads_include_cited_summary_and_safe_actions():
    result = {"summary": "Payment errors require investigation [source:RB-01]"}
    slack = slack_message("alert-123", result)
    assert "RB-01" in slack["blocks"][1]["text"]["text"]
    assert {a["action_id"] for a in slack["blocks"][3]["elements"]} == {
        "opspilot_ack", "opspilot_open_ticket", "opspilot_draft_change"
    }
    teams = teams_card("alert-123", result).model_dump(by_alias=True, exclude_none=True)
    assert "RB-01" in teams["body"][1]["text"]
    assert [action["data"]["action"] for action in teams["actions"]] == [
        "ack", "ticket", "change"
    ]
