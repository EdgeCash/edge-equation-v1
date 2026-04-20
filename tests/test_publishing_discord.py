from edge_equation.publishing.discord_publisher import DiscordPublisher


def _card():
    return {
        "card_type": "daily_edge",
        "headline": "Daily Edge",
        "subhead": "Today's plays.",
        "picks": [
            {"market_type": "ML", "selection": "BOS", "grade": "A", "edge": "0.049167", "kelly": "0.0324"},
        ],
        "tagline": "Facts. Not Feelings.",
    }


def test_discord_dry_run_success():
    pub = DiscordPublisher(webhook_url="https://example.invalid")
    result = pub.publish_card(_card(), dry_run=True)
    assert result.success is True
    assert result.target == "discord"
    assert result.message_id == "dry-run"
    assert result.error is None


def test_discord_non_dry_run_success():
    pub = DiscordPublisher()
    result = pub.publish_card(_card(), dry_run=False)
    assert result.success is True
    assert result.target == "discord"
    assert result.message_id.startswith("discord-")


def test_discord_embed_structure():
    pub = DiscordPublisher()
    embed = pub.build_embed(_card())
    assert "embeds" in embed and len(embed["embeds"]) == 1
    e = embed["embeds"][0]
    assert e["title"] == "Daily Edge"
    assert e["description"] == "Today's plays."
    assert e["footer"]["text"] == "Facts. Not Feelings."
    assert len(e["fields"]) == 1


def test_discord_handles_empty_picks_without_raising():
    pub = DiscordPublisher()
    card = {"headline": "h", "subhead": "s", "tagline": "t", "picks": []}
    result = pub.publish_card(card, dry_run=True)
    assert result.success is True
